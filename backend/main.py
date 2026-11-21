"""Lesen für Studenten: API server.

Gaze estimation runs entirely in the browser (WebGazer); webcam video never
leaves the student's machine. The server receives gaze samples plus the
page layout after a reading session, analyses them and stores the result.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import analysis, config, db, dw, translate
from .content import get_article, grade_quiz, load_articles, public_article, summary

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = config.log

USER_ID_PATTERN = r"^[\w.\-가-힣]{1,40}$"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    if config.ADMIN_TOKEN_GENERATED:
        log.warning("ADMIN_TOKEN is not set. Generated a temporary admin token: %s", config.ADMIN_TOKEN)
    yield


app = FastAPI(title="Lesen für Studenten", lifespan=lifespan)

if config.ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(config.FRONTEND_DIR / "admin.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(config.FRONTEND_DIR / "favicon.ico")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------- auth ----

_TOKEN_DIGEST = hashlib.sha256(config.ADMIN_TOKEN.encode()).digest()


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    return secrets.compare_digest(hashlib.sha256(token.encode()).digest(), _TOKEN_DIGEST)


def require_admin(authorization: str | None = Header(None)) -> None:
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid admin token")


# ------------------------------------------------------------ articles ----


LEVEL_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]


def _level_key(a: dict):
    lv = (a.get("level") or "?")[:2]
    return (LEVEL_ORDER.index(lv) if lv in LEVEL_ORDER else 99, a["title"])


@app.get("/api/articles")
def list_articles():
    return [summary(a) for a in sorted(load_articles().values(), key=_level_key)]


@app.get("/api/articles/{article_id}")
def article_detail(article_id: str):
    article = get_article(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Unknown article")
    return public_article(article)


# --------------------------------------------------------- lookups/vocab ----


@app.get("/api/languages")
def languages():
    return {"languages": translate.LANGUAGES, "provider": "claude" if translate.claude_available() else "mymemory"}


class LookupIn(BaseModel):
    user_id: str = Field(pattern=USER_ID_PATTERN)
    article_id: str = Field(max_length=80)
    word: str = Field(min_length=1, max_length=80)
    sentence: str = Field(default="", max_length=1200)
    lang: str = Field(max_length=5)


@app.post("/api/lookup")
async def lookup(body: LookupIn):
    if body.lang not in translate.LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language")
    word = translate.clean_word(body.word)
    if not word:
        raise HTTPException(status_code=400, detail="Not a word")
    g = await run_in_threadpool(translate.gloss, word, body.sentence, body.lang, db.cache_get, db.cache_put)
    if g is None:
        raise HTTPException(status_code=503, detail="Translation service unavailable")
    article = get_article(body.article_id)
    dw_def = None
    if article:
        lw = word.lower()
        for entry in article.get("glossary", []):
            if any(lw == s.lower() or lw in s.lower().split() for s in entry["surface"]):
                dw_def = {"term": entry["term"], "definition": entry["definition"]}
                break
    entry = await run_in_threadpool(db.add_vocab, body.user_id, body.lang, g, body.sentence, body.article_id)
    return {"word": word, **g, "dw": dw_def, "vocab_id": entry["id"], "lookups": entry["lookups"]}


@app.get("/api/vocab")
def vocab_list(user_id: str):
    return {"user_id": user_id, "items": db.list_vocab(user_id)}


@app.delete("/api/vocab/{vocab_id}")
def vocab_delete(vocab_id: int, user_id: str):
    if not db.delete_vocab(user_id, vocab_id):
        raise HTTPException(status_code=404, detail="Unknown entry")
    return {"ok": True}


def _vocab_csv(items: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["German", "Meaning", "Part of speech", "Example", "Lookups"])
    for v in items:
        de = f"{v['article']} {v['lemma']}".strip()
        w.writerow([de, v["translation"], v["pos"], v["example"], v["lookups"]])
    return buf.getvalue()


@app.get("/api/vocab.csv")
def vocab_csv(user_id: str):
    return Response(
        "\ufeff" + _vocab_csv(db.list_vocab(user_id)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vocabulary.csv"'},
    )


# ------------------------------------------------------------ sessions ----


class AOIIn(BaseModel):
    id: str = Field(max_length=40)
    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)
    words: int = Field(ge=0)


class LayoutIn(BaseModel):
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    line_height: float = Field(gt=0, le=200)
    viewport_w: float | None = None
    viewport_h: float | None = None


class SessionIn(BaseModel):
    user_id: str = Field(pattern=USER_ID_PATTERN)
    article_id: str
    mode: Literal["webcam", "mouse"]
    started_at: int  # epoch ms
    duration_ms: float = Field(gt=0, le=config.MAX_DURATION_MS)
    calibration_error_px: float | None = Field(default=None, ge=0)
    layout: LayoutIn
    aois: list[AOIIn] = Field(min_length=1, max_length=200)
    # [t_ms, x, y] triples, t relative to reading start
    samples: list[tuple[float, float, float]] = Field(max_length=config.MAX_SAMPLES)
    # [x, y, w, h, sentence] per word, in reading order
    words: list[tuple[float, float, float, float, int]] = Field(default_factory=list, max_length=6000)
    lookups: list[dict] = Field(default_factory=list, max_length=500)
    answers: dict[str, list[int] | int] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def answers_as_lists(cls, v):
        return {k: (a if isinstance(a, list) else [a]) for k, a in v.items()}

    @field_validator("aois")
    @classmethod
    def unique_ids(cls, v):
        if len({a.id for a in v}) != len(v):
            raise ValueError("AOI ids must be unique")
        return v


def _evaluate(body: SessionIn, article: dict) -> dict:
    samples = [analysis.Sample(t, x, y) for t, x, y in body.samples]
    aois = [analysis.AOI(a.id, a.x, a.y, a.w, a.h, a.words) for a in body.aois]
    words = [analysis.Word(*w) for w in body.words]
    metrics = analysis.analyze(samples, aois, body.duration_ms, body.layout.line_height, words=words or None)
    gaze = analysis.gaze_score(metrics)
    quiz, feedback = grade_quiz(article, body.answers)
    # Without quiz questions the final score is the gaze score alone.
    final = analysis.final_score(gaze, quiz, config.GAZE_WEIGHT) if article["quiz"] else gaze
    return {
        "id": uuid.uuid4().hex,
        "user_id": body.user_id,
        "article_id": body.article_id,
        "mode": body.mode,
        "started_at": body.started_at,
        "duration_ms": round(body.duration_ms),
        "calib_error_px": body.calibration_error_px,
        "gaze_score": gaze,
        "quiz_score": quiz,
        "final_score": final,
        "flags": analysis.quality_flags(metrics, body.calibration_error_px, body.layout.line_height),
        "metrics": metrics,
        "answers": body.answers,
        "layout": {**body.layout.model_dump(), "aois": [a.model_dump() for a in body.aois], "words": [list(w) for w in body.words]},
        "samples": [list(s) for s in body.samples],
        "lookups": [{"t": l.get("t"), "i": l.get("i"), "word": str(l.get("word", ""))[:80]} for l in body.lookups],
        "marks": (metrics.get("difficulty") or {}).get("difficult_sentences", []),
        "quiz_feedback": feedback,
    }


@app.post("/api/sessions")
async def submit_session(body: SessionIn):
    article = get_article(body.article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Unknown article")
    result = await run_in_threadpool(_evaluate, body, article)
    feedback = result.pop("quiz_feedback")
    await run_in_threadpool(db.insert_session, result)
    await admin_hub.broadcast_overview()
    return {
        "session_id": result["id"],
        "gaze_score": result["gaze_score"],
        "quiz_score": result["quiz_score"],
        "final_score": result["final_score"],
        "flags": result["flags"],
        "metrics": result["metrics"],
        "marks": result["marks"],
        "has_quiz": bool(article["quiz"]),
        "quiz_feedback": feedback,
    }


class MarksIn(BaseModel):
    sentences: list[int] = Field(max_length=2000)


@app.put("/api/sessions/{session_id}/marks")
async def set_marks(session_id: str, body: MarksIn):
    """Sentences the student marked as difficult (session id acts as the key)."""
    if not db.set_marks(session_id, sorted(set(body.sentences))):
        raise HTTPException(status_code=404, detail="Unknown session")
    await admin_hub.broadcast_overview()
    return {"ok": True}


# --------------------------------------------------------------- admin ----


class LevelsIn(BaseModel):
    high: float = Field(ge=0, le=100)
    low: float = Field(ge=0, le=100)


def overview() -> dict:
    levels = db.get_levels()
    groups = {"high": [], "medium": [], "low": []}
    for u in db.latest_per_user():
        s = u["final_score"]
        lv = "high" if s >= levels["high"] else "low" if s < levels["low"] else "medium"
        groups[lv].append(u)
    for g in groups.values():
        g.sort(key=lambda u: u["final_score"], reverse=True)
    return {"levels": levels, "groups": groups}


@app.get("/api/admin/overview", dependencies=[Depends(require_admin)])
def admin_overview():
    return overview()


@app.put("/api/admin/levels", dependencies=[Depends(require_admin)])
async def admin_set_levels(levels: LevelsIn):
    if not levels.low < levels.high:
        raise HTTPException(status_code=400, detail="low must be smaller than high")
    db.set_levels(levels.model_dump())
    await admin_hub.broadcast_overview()
    return levels


@app.get("/api/admin/users/{user_id}", dependencies=[Depends(require_admin)])
def admin_user(user_id: str):
    sessions = db.user_sessions(user_id)
    if not sessions:
        raise HTTPException(status_code=404, detail="Unknown user")
    return {"user_id": user_id, "sessions": sessions, "vocab": db.list_vocab(user_id)}


class ImportIn(BaseModel):
    url: str = Field(max_length=500)


@app.post("/api/admin/articles/import", dependencies=[Depends(require_admin)])
async def admin_import_article(body: ImportIn):
    try:
        article = await run_in_threadpool(dw.import_lesson, body.url)
    except dw.ImportError_ as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.save_article(article)
    return summary(get_article(article["id"]))


@app.get("/api/admin/articles", dependencies=[Depends(require_admin)])
def admin_articles():
    return [summary(a) for a in sorted(load_articles().values(), key=_level_key)]


@app.delete("/api/admin/articles/{article_id}", dependencies=[Depends(require_admin)])
def admin_delete_article(article_id: str):
    if not db.delete_article(article_id):
        raise HTTPException(status_code=404, detail="Only imported articles can be deleted")
    return {"ok": True}


@app.get("/api/admin/vocab/{user_id}.csv", dependencies=[Depends(require_admin)])
def admin_vocab_csv(user_id: str):
    return vocab_csv(user_id)


@app.delete("/api/admin/users/{user_id}", dependencies=[Depends(require_admin)])
async def admin_delete_user(user_id: str):
    n = db.delete_user(user_id)
    await admin_hub.broadcast_overview()
    return {"deleted_sessions": n}


@app.get("/api/admin/sessions/{session_id}", dependencies=[Depends(require_admin)])
def admin_session(session_id: str):
    s = db.get_session(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    return s


EXPORT_FIELDS = [
    "id", "user_id", "article_id", "mode", "started_at", "duration_ms", "calib_error_px",
    "gaze_score", "quiz_score", "final_score", "reading_coverage", "wpm", "fixations",
    "regression_rate", "on_text_ratio", "difficulty_penalty", "difficult_sentences", "flags",
]


@app.get("/api/admin/export.csv", dependencies=[Depends(require_admin)])
def admin_export_csv():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    w.writeheader()
    for s in db.all_sessions(with_metrics=True):
        m = s.pop("metrics")
        d = m.get("difficulty") or {}
        w.writerow({
            **s,
            **{k: m[k] for k in ("reading_coverage", "wpm", "fixations", "regression_rate", "on_text_ratio")},
            "difficulty_penalty": d.get("penalty", 0),
            "difficult_sentences": len(d.get("difficult_sentences", [])),
            "flags": ";".join(s["flags"]),
        })
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sessions.csv"'},
    )


@app.get("/api/admin/export.json", dependencies=[Depends(require_admin)])
def admin_export_json():
    data = {"exported_at": int(time.time() * 1000), "sessions": db.all_sessions(with_metrics=True)}
    return Response(
        json.dumps(data, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sessions.json"'},
    )


class AdminHub:
    """Pushes the overview to connected admin dashboards."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast_overview(self) -> None:
        if not self.clients:
            return
        payload = {"type": "overview", "data": await run_in_threadpool(overview)}
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                self.clients.discard(ws)


admin_hub = AdminHub()


@app.websocket("/api/admin/ws")
async def admin_ws(ws: WebSocket):
    # The token is sent as the first message rather than in the URL so it
    # does not end up in proxy or server access logs.
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=5)
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=4401)
        return
    if not isinstance(first, dict) or first.get("type") != "auth" or not verify_token(first.get("token")):
        await ws.close(code=4401, reason="Invalid admin token")
        return

    admin_hub.clients.add(ws)
    try:
        await ws.send_json({"type": "overview", "data": await run_in_threadpool(overview)})
        while True:
            await ws.receive_text()  # pings; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        admin_hub.clients.discard(ws)
