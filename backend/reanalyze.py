"""Recompute stored sessions with the current analysis parameters.

Raw gaze samples and the page layout are kept for every session, so when
thresholds change the scores can be brought up to date:

    python -m backend.reanalyze            # all sessions
    python -m backend.reanalyze --dry-run  # show what would change
"""

from __future__ import annotations

import sys

from . import analysis, config, db
from .content import get_article


def evaluate_stored(sess: dict, article: dict | None) -> tuple[int, int, list, dict]:
    layout = sess["layout"]
    samples = [analysis.Sample(*s) for s in sess["samples"]]
    aois = [analysis.AOI(a["id"], a["x"], a["y"], a["w"], a["h"], a["words"]) for a in layout["aois"]]
    words = [analysis.Word(*w) for w in layout.get("words", [])]
    params = analysis.adapt(analysis.AnalysisParams(), sess.get("calib_error_px"), sess.get("calib_jitter_px"))
    metrics = analysis.analyze(samples, aois, sess["duration_ms"], layout["line_height"], params, words=words or None)
    gaze = analysis.gaze_score(metrics)
    has_quiz = bool(article["quiz"]) if article else sess["quiz_score"] is not None
    final = analysis.final_score(gaze, sess["quiz_score"], config.GAZE_WEIGHT) if has_quiz else gaze
    flags = analysis.quality_flags(metrics, sess.get("calib_error_px"), layout["line_height"])
    return gaze, final, flags, metrics


def reanalyze_all(dry_run: bool = False) -> list[dict]:
    changes = []
    for sid in db.all_session_ids():
        sess = db.get_session(sid)
        gaze, final, flags, metrics = evaluate_stored(sess, get_article(sess["article_id"]))
        if (gaze, final) != (sess["gaze_score"], sess["final_score"]):
            changes.append({"id": sid, "user_id": sess["user_id"], "final": [sess["final_score"], final], "gaze": [sess["gaze_score"], gaze]})
        if not dry_run:
            db.update_scores(sid, gaze, final, flags, metrics)
    return changes


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    db.init()
    changed = reanalyze_all(dry_run=dry)
    for c in changed:
        print(f"{c['user_id']:<20} final {c['final'][0]:>3} -> {c['final'][1]:>3}   gaze {c['gaze'][0]:>3} -> {c['gaze'][1]:>3}")
    print(f"{len(changed)} session(s) {'would change' if dry else 'updated'}")
