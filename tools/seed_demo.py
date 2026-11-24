"""Fill the database with simulated sessions to try out the validation page.

    DB_PATH=data/demo.db python -m tools.seed_demo [count]
    DATABASE_URL=postgresql://... python -m tools.seed_demo [count]   # e.g. Supabase

Every session is marked mode="simulated" and its user id starts with "sim-",
so it can never be mistaken for a real student. Self-reports are made
imperfect on purpose: a simulated student reports each truly difficult
sentence with 75% probability and occasionally marks one that was not.
"""

from __future__ import annotations

import json
import random
import sys
import time
import uuid
from pathlib import Path

from backend import analysis, config, db
from backend.content import get_article, grade_quiz
from backend.params import current
from backend.sim import NoiseModel, Truth, layout_text, pick_truth, read, to_samples

ARTICLE_ID = "schwarze-katze"
NOISES = [NoiseModel(bias_px=30, jitter_px=20), NoiseModel(bias_px=55, jitter_px=30), NoiseModel(bias_px=85, jitter_px=45), NoiseModel(bias_px=140, jitter_px=65)]


def seed(count: int = 16, seed: int = 7) -> list[str]:
    db.init()
    article = get_article(ARTICLE_ID)
    layout = layout_text(article["title"], article["paragraphs"], lead=article["lead"])
    rng = random.Random(seed)
    ids = []
    for n in range(count):
        noise = NOISES[n % len(NOISES)]
        truth = Truth() if n % 5 == 0 else pick_truth(layout, rng, rng.randint(1, 2), rng.randint(0, 2))
        fixes = read(layout, truth, rng)
        samples, duration = to_samples(fixes, rng, noise)
        error = (noise.bias_px + 0.25 * noise.jitter_px) * rng.uniform(0.85, 1.15)
        jitter = noise.jitter_px * rng.uniform(0.8, 1.2)
        p = analysis.adapt(current(), error, jitter)
        metrics = analysis.analyze(samples, layout.aois, duration, layout.line_height, p, words=layout.words)
        gaze = analysis.gaze_score(metrics)
        answers = {q["id"]: (q["answer"] if rng.random() < 0.75 else [(q["answer"][0] + 1) % len(q["options"])]) for q in article["quiz"]}
        quiz, _ = grade_quiz(article, answers)
        self_report = sorted(
            {s for s in truth.difficult if rng.random() < 0.75}
            | {s for s in range(len(layout.sentence_words)) if s not in truth.difficult and rng.random() < 0.02}
        )
        sid = uuid.uuid4().hex
        db.insert_session({
            "id": sid, "user_id": f"sim-{n + 1:02d}", "article_id": ARTICLE_ID, "mode": "simulated",
            "started_at": int(time.time() * 1000) - (count - n) * 60_000, "duration_ms": round(duration),
            "calib_error_px": round(error, 1), "calib_jitter_px": round(jitter, 1),
            "gaze_score": gaze, "quiz_score": quiz, "final_score": analysis.final_score(gaze, quiz, config.GAZE_WEIGHT),
            "flags": analysis.quality_flags(metrics, error, layout.line_height),
            "metrics": metrics, "answers": answers,
            "layout": {"width": 792.0, "height": layout.aois[-1].y + layout.aois[-1].h + 48, "line_height": layout.line_height,
                       "aois": [a.__dict__ for a in layout.aois], "words": [[w.x, w.y, w.w, w.h, w.s] for w in layout.words]},
            "samples": [[round(s.t), round(s.x, 1), round(s.y, 1)] for s in samples],
            "lookups": [], "marks": [],
        })
        db.set_self_marks(sid, self_report)
        ids.append(sid)
    return ids


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    target = "Postgres (DATABASE_URL)" if config.DATABASE_URL else config.DB_PATH
    print(f"{len(seed(n))} simulated sessions written to {target}")
