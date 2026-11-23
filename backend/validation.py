"""Compare automatic difficulty detection with what students reported.

Before seeing their results, students mark the sentences they found
difficult ("self_marks"). Those reports are the reference here. They are not
perfect labels either (students forget or under-report), so the numbers are
agreement, not accuracy, and come with confidence intervals.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace

from . import db
from . import params as params_mod
from .analysis import AnalysisParams
from .content import get_article
from .reanalyze import evaluate_stored

BUCKETS = [("≤60px", 0, 60), ("60–100px", 60, 100), ("100–150px", 100, 150), (">150px", 150, math.inf)]

# Candidates tried by the what-if comparison: the detection thresholds that
# matter most, around the current values.
WHATIF_GRID = {
    "slow_factor": [1.75, 2.0, 2.4],
    "return_min_lines": [1.5, 2.0, 2.5],
    "return_min_ms": [350.0, 500.0, 800.0],
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def bucket_of(sess: dict) -> str:
    if sess["mode"] == "mouse":
        return "마우스"
    e = sess.get("calib_error_px") or 0.0
    return next(name for name, _lo, hi in BUCKETS if e < hi)


def _counts(auto: set[int], label: set[int]) -> dict:
    return {"tp": len(auto & label), "fp": len(auto - label), "fn": len(label - auto)}


def _stats(rows: list[dict]) -> dict:
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if precision is not None and recall is not None else None)
    no_report = [r for r in rows if not r["label"]]
    return {
        "sessions": len(rows),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3) if precision is not None else None,
        "precision_ci": [round(x, 3) for x in wilson(tp, tp + fp)] if tp + fp else None,
        "recall": round(recall, 3) if recall is not None else None,
        "recall_ci": [round(x, 3) for x in wilson(tp, tp + fn)] if tp + fn else None,
        "f1": round(f1, 3) if f1 is not None else None,
        # sessions where the student reported nothing difficult: any flag or
        # penalty there is a likely false alarm
        "no_report_sessions": len(no_report),
        "no_report_flags": round(sum(len(r["auto"]) for r in no_report) / len(no_report), 2) if no_report else None,
        "no_report_penalty": round(sum(r["penalty"] for r in no_report) / len(no_report), 2) if no_report else None,
    }


def labelled_sessions(include_mouse: bool = False) -> list[dict]:
    out = []
    for sid in db.all_session_ids():
        s = db.get_session(sid)
        if s["self_marks"] is None or (s["mode"] == "mouse" and not include_mouse):
            continue
        out.append(s)
    return out


def _row(sess: dict, metrics: dict) -> dict:
    d = metrics.get("difficulty") or {}
    auto = set(d.get("difficult_sentences", []))
    label = set(sess["self_marks"] or [])
    return {
        "id": sess["id"], "user_id": sess["user_id"], "article_id": sess["article_id"], "mode": sess["mode"],
        "created_at": sess["created_at"], "calib_error_px": sess.get("calib_error_px"), "bucket": bucket_of(sess),
        "auto": sorted(auto), "label": sorted(label), "final_marks": sorted(sess.get("marks") or []),
        "penalty": d.get("penalty", 0), "raw_penalty": d.get("raw_penalty", d.get("penalty", 0)),
        **_counts(auto, label),
    }


def summarize(sessions: list[dict]) -> dict:
    rows = [_row(s, s["metrics"]) for s in sessions]
    by_bucket = []
    for name in [b[0] for b in BUCKETS] + ["마우스"]:
        sub = [r for r in rows if r["bucket"] == name]
        if sub:
            by_bucket.append({"bucket": name, **_stats(sub)})
    # How students changed their marks after seeing the automatic result.
    accepted = sum(len(set(r["final_marks"]) & set(r["auto"]) - set(r["label"])) for r in rows)
    rejected = sum(len(set(r["label"]) - set(r["final_marks"])) for r in rows)
    return {
        "overall": _stats(rows),
        "by_bucket": by_bucket,
        "after_results": {"added_from_auto": accepted, "removed_own": rejected},
        "sessions": rows,
        "params": params_mod.overrides(),
    }


def whatif(sessions: list[dict], grid: dict | None = None, limit: int = 8) -> list[dict]:
    """Re-run detection with candidate thresholds and rank by agreement."""
    grid = grid or WHATIF_GRID
    base = params_mod.current()
    articles = {s["article_id"]: get_article(s["article_id"]) for s in sessions}
    keys = list(grid)
    seen = set()
    results = []
    candidates = [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]
    candidates.insert(0, {k: getattr(base, k) for k in keys})  # current settings first
    for cand in candidates:
        sig = tuple(sorted(cand.items()))
        if sig in seen:
            continue
        seen.add(sig)
        p: AnalysisParams = replace(base, **cand)
        rows = []
        for s in sessions:
            _, _, _, metrics = evaluate_stored(s, articles[s["article_id"]], p)
            rows.append(_row(s, metrics))
        st = _stats(rows)
        results.append({"params": cand, "current": len(results) == 0, **st})
    # rank: agreement first, then fewer false alarms on sessions with no report
    results.sort(key=lambda r: ((r["f1"] or 0) - 0.1 * (r["no_report_flags"] or 0)), reverse=True)
    top = results[:limit]
    if not any(r["current"] for r in top):  # always show the current settings for comparison
        top.append(next(r for r in results if r["current"]))
    return top
