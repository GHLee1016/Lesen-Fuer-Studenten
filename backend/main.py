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

from . import analysis, config, db
from .content import grade_quiz, load_articles, public_article

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


@app.get("/api/articles")
def list_articles():
    return [
        {"id": a["id"], "title": a["title"], "level": a["level"], "words": a["words"]}
        for a in load_articles().values()
    ]


@app.get("/api/articles/{article_id}")
def get_article(article_id: str):
    article = load_articles().get(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Unknown article")
    return public_article(article)


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
    answers: dict[str, int] = Field(default_factory=dict)

    @field_validator("aois")
    @classmethod
    def unique_ids(cls, v):
        if len({a.id for a in v}) != len(v):
            raise ValueError("AOI ids must be unique")
        return v


def _evaluate(body: SessionIn, article: dict) -> dict:
    samples = [analysis.Sample(t, x, y) for t, x, y in body.samples]
    aois = [analysis.AOI(a.id, a.x, a.y, a.w, a.h, a.words) for a in body.aois]
    metrics = analysis.analyze(samples, aois, body.duration_ms, body.layout.line_height)
    gaze = analysis.gaze_score(metrics)
    quiz, feedback = grade_quiz(article, body.answers)
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
        "final_score": analysis.final_score(gaze, quiz, config.GAZE_WEIGHT),
        "flags": analysis.quality_flags(metrics, body.calibration_error_px, body.layout.line_height),
        "metrics": metrics,
        "answers": body.answers,
        "layout": {**body.layout.model_dump(), "aois": [a.model_dump() for a in body.aois]},
        "samples": [list(s) for s in body.samples],
        "quiz_feedback": feedback,
    }


@app.post("/api/sessions")
async def submit_session(body: SessionIn):
    article = load_articles().get(body.article_id)
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
        "quiz_feedback": feedback,
    }


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
    return {"user_id": user_id, "sessions": sessions}


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
    "regression_rate", "on_text_ratio", "flags",
]


@app.get("/api/admin/export.csv", dependencies=[Depends(require_admin)])
def admin_export_csv():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    w.writeheader()
    for s in db.all_sessions(with_metrics=True):
        m = s.pop("metrics")
        w.writerow({**s, **{k: m[k] for k in ("reading_coverage", "wpm", "fixations", "regression_rate", "on_text_ratio")}, "flags": ";".join(s["flags"])})
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
