import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    article_id     TEXT NOT NULL,
    mode           TEXT NOT NULL,
    started_at     INTEGER NOT NULL,
    duration_ms    INTEGER NOT NULL,
    calib_error_px REAL,
    gaze_score     INTEGER NOT NULL,
    quiz_score     INTEGER NOT NULL,
    final_score    INTEGER NOT NULL,
    flags          TEXT NOT NULL,
    metrics        TEXT NOT NULL,
    answers        TEXT NOT NULL,
    layout         TEXT NOT NULL,
    samples        TEXT NOT NULL,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    imported_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS translations (
    key        TEXT PRIMARY KEY,
    result     TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS vocab (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    lang        TEXT NOT NULL,
    lemma       TEXT NOT NULL,
    article     TEXT NOT NULL DEFAULT '',
    pos         TEXT NOT NULL DEFAULT '',
    translation TEXT NOT NULL,
    example     TEXT NOT NULL DEFAULT '',
    article_id  TEXT NOT NULL DEFAULT '',
    lookups     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE (user_id, lang, lemma)
);
"""

# Columns added after the first release; created on startup if missing.
MIGRATIONS = [
    ("sessions", "lookups", "TEXT NOT NULL DEFAULT '[]'"),
    ("sessions", "marks", "TEXT NOT NULL DEFAULT '[]'"),
    ("sessions", "calib_jitter_px", "REAL"),
]

SUMMARY_COLS = (
    "id, user_id, article_id, mode, started_at, duration_ms, calib_error_px, "
    "gaze_score, quiz_score, final_score, flags, created_at"
)


def _path() -> Path:
    return config.DB_PATH


@contextmanager
def connect():
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _summary(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["flags"] = json.loads(d["flags"])
    return d


def insert_session(s: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO sessions (id, user_id, article_id, mode, started_at, duration_ms, calib_error_px, "
            "gaze_score, quiz_score, final_score, flags, metrics, answers, layout, samples, created_at, lookups, marks, calib_jitter_px) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s["id"], s["user_id"], s["article_id"], s["mode"], s["started_at"], s["duration_ms"],
                s["calib_error_px"], s["gaze_score"], s["quiz_score"], s["final_score"],
                json.dumps(s["flags"]), json.dumps(s["metrics"]), json.dumps(s["answers"]),
                json.dumps(s["layout"]), json.dumps(s["samples"]), int(time.time() * 1000),
                json.dumps(s.get("lookups", [])), json.dumps(s.get("marks", [])), s.get("calib_jitter_px"),
            ),
        )


def latest_per_user() -> list[dict]:
    """Most recent session of every user, plus how many sessions they have."""
    with connect() as c:
        rows = c.execute(
            f"""
            SELECT {SUMMARY_COLS}, metrics,
                   (SELECT COUNT(*) FROM sessions s2 WHERE s2.user_id = s.user_id) AS session_count
            FROM sessions s
            WHERE rowid = (SELECT MAX(rowid) FROM sessions s3 WHERE s3.user_id = s.user_id)
            """
        ).fetchall()
    out = []
    for r in rows:
        d = _summary(r)
        m = json.loads(d.pop("metrics"))
        d["reading_coverage"] = m["reading_coverage"]
        d["wpm"] = m["wpm"]
        d["regression_rate"] = m["regression_rate"]
        out.append(d)
    return out


def user_sessions(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            f"SELECT {SUMMARY_COLS}, metrics FROM sessions WHERE user_id = ? ORDER BY rowid DESC",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = _summary(r)
        m = json.loads(d.pop("metrics"))
        m.pop("fixation_list", None)
        d["metrics"] = m
        out.append(d)
    return out


def get_session(session_id: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    for k in ("flags", "metrics", "answers", "layout", "samples", "lookups", "marks"):
        d[k] = json.loads(d[k])
    return d


def all_sessions(with_metrics: bool = False) -> list[dict]:
    cols = SUMMARY_COLS + (", metrics" if with_metrics else "")
    with connect() as c:
        rows = c.execute(f"SELECT {cols} FROM sessions ORDER BY rowid").fetchall()
    out = []
    for r in rows:
        d = _summary(r)
        if with_metrics:
            d["metrics"] = json.loads(d["metrics"])
        out.append(d)
    return out


def delete_user(user_id: str) -> int:
    with connect() as c:
        c.execute("DELETE FROM vocab WHERE user_id = ?", (user_id,))
        return c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


def get_levels() -> dict:
    with connect() as c:
        r = c.execute("SELECT value FROM settings WHERE key = 'levels'").fetchone()
    return json.loads(r["value"]) if r else dict(config.DEFAULT_LEVELS)


def set_levels(levels: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES ('levels', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(levels),),
        )


def set_marks(session_id: str, marks: list[int]) -> bool:
    with connect() as c:
        return c.execute("UPDATE sessions SET marks = ? WHERE id = ?", (json.dumps(marks), session_id)).rowcount > 0


# ------------------------------------------------------------- articles ----


def save_article(article: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO articles(id, data, imported_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, imported_at = excluded.imported_at",
            (article["id"], json.dumps(article, ensure_ascii=False), int(time.time() * 1000)),
        )


def stored_articles() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT data, imported_at FROM articles ORDER BY imported_at DESC").fetchall()
    return [{**json.loads(r["data"]), "imported_at": r["imported_at"]} for r in rows]


def delete_article(article_id: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM articles WHERE id = ?", (article_id,)).rowcount > 0


# --------------------------------------------------------- translations ----


def cache_get(key: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT result FROM translations WHERE key = ?", (key,)).fetchone()
    return json.loads(r["result"]) if r else None


def cache_put(key: str, result: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO translations(key, result, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(result, ensure_ascii=False), int(time.time() * 1000)),
        )


# ---------------------------------------------------------------- vocab ----


def add_vocab(user_id: str, lang: str, g: dict, example: str, article_id: str) -> dict:
    now = int(time.time() * 1000)
    with connect() as c:
        c.execute(
            """
            INSERT INTO vocab(user_id, lang, lemma, article, pos, translation, example, article_id, lookups, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id, lang, lemma) DO UPDATE SET
                lookups = lookups + 1, updated_at = excluded.updated_at,
                translation = excluded.translation, example = excluded.example, article_id = excluded.article_id
            """,
            (user_id, lang, g["lemma"], g.get("article", ""), g.get("pos", ""), g["translation"], example, article_id, now, now),
        )
        r = c.execute("SELECT * FROM vocab WHERE user_id = ? AND lang = ? AND lemma = ?", (user_id, lang, g["lemma"])).fetchone()
    return dict(r)


def list_vocab(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM vocab WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_vocab(user_id: str, vocab_id: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM vocab WHERE id = ? AND user_id = ?", (vocab_id, user_id)).rowcount > 0


def all_session_ids() -> list[str]:
    with connect() as c:
        return [r["id"] for r in c.execute("SELECT id FROM sessions ORDER BY rowid")]


def update_scores(session_id: str, gaze: int, final: int, flags: list, metrics: dict) -> None:
    with connect() as c:
        c.execute(
            "UPDATE sessions SET gaze_score = ?, final_score = ?, flags = ?, metrics = ? WHERE id = ?",
            (gaze, final, json.dumps(flags), json.dumps(metrics), session_id),
        )
