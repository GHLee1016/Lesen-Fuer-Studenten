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
"""

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


def _summary(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["flags"] = json.loads(d["flags"])
    return d


def insert_session(s: dict) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s["id"], s["user_id"], s["article_id"], s["mode"], s["started_at"], s["duration_ms"],
                s["calib_error_px"], s["gaze_score"], s["quiz_score"], s["final_score"],
                json.dumps(s["flags"]), json.dumps(s["metrics"]), json.dumps(s["answers"]),
                json.dumps(s["layout"]), json.dumps(s["samples"]), int(time.time() * 1000),
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
    for k in ("flags", "metrics", "answers", "layout", "samples"):
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
