"""Measure how well the difficulty analysis works under webcam-like noise.

    python -m tools.tune baseline          # current parameters, noise grid
    python -m tools.tune sweep             # parameter search, writes docs/tuning.md

Two kinds of simulated readers are used:
  * clean     - no difficult sentences; any penalty they get is unfair
  * difficult - 2 slow and 2 re-read sentences; we measure how many of them
                are found (recall) and how many flags are right (precision)
"""

from __future__ import annotations

import json
import random
import statistics as st
import sys
from dataclasses import replace
from pathlib import Path

from backend.analysis import AnalysisParams, adapt, analyze
from backend.sim import NoiseModel, Truth, layout_text, pick_truth, read, to_samples

ROOT = Path(__file__).resolve().parent.parent
ARTICLE = json.loads((ROOT / "backend/content/schwarze-katze.json").read_text(encoding="utf-8"))
LAYOUT = layout_text(ARTICLE["title"], [ARTICLE["lead"], *ARTICLE["paragraphs"]])

NOISE_GRID = [
    ("ideal", NoiseModel(bias_px=0, jitter_px=10, drift_px_per_min=0, dropout_rate=0)),
    ("good (≈50px)", NoiseModel(bias_px=40, jitter_px=25)),
    ("typical (≈100px)", NoiseModel(bias_px=80, jitter_px=45)),
    ("poor (≈160px)", NoiseModel(bias_px=140, jitter_px=70)),
]


def measured(noise: NoiseModel, rng: random.Random) -> tuple[float, float]:
    """What calibration would report: the validation error (medians of each
    dot, so mostly the bias) and the frame-to-frame jitter, both imprecise."""
    error = (noise.bias_px + 0.25 * noise.jitter_px) * rng.uniform(0.85, 1.15)
    jitter = noise.jitter_px * rng.uniform(0.8, 1.2)
    return error, jitter


def run(params: AnalysisParams, noise: NoiseModel, truth: Truth, seed: int):
    rng = random.Random(seed)
    fixes = read(LAYOUT, truth, rng)
    samples, duration = to_samples(fixes, rng, noise)
    p = adapt(params, *measured(noise, rng))
    return analyze(samples, LAYOUT.aois, duration, LAYOUT.line_height, p, words=LAYOUT.words)


def evaluate(params: AnalysisParams, noise: NoiseModel, trials: int = 12, seed0: int = 0) -> dict:
    clean_pen, clean_fa, clean_cov = [], [], []
    tp = fp = fn = 0
    diff_pen = []
    applied = []
    for k in range(trials):
        m = run(params, noise, Truth(), seed0 + k)
        d = m["difficulty"]
        clean_pen.append(d["raw_penalty"])
        clean_fa.append(len(d["difficult_sentences"]))
        clean_cov.append(m["reading_coverage"])

        rng = random.Random(10_000 + seed0 + k)
        truth = pick_truth(LAYOUT, rng, 2, 2)
        m = run(params, noise, truth, 20_000 + seed0 + k)
        d = m["difficulty"]
        found = set(d["difficult_sentences"])
        tp += len(found & truth.difficult)
        fp += len(found - truth.difficult)
        fn += len(truth.difficult - found)
        diff_pen.append(d["raw_penalty"])
        applied.append(d["penalty"])
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 2), "recall": round(recall, 2), "f1": round(f1, 2),
        "clean_penalty": round(st.mean(clean_pen), 1), "clean_false_flags": round(st.mean(clean_fa), 2),
        "difficult_penalty": round(st.mean(diff_pen), 1), "applied_penalty": round(st.mean(applied), 1),
        "clean_coverage": round(st.mean(clean_cov), 2),
    }


def table(rows: list[tuple[str, dict]]) -> str:
    keys = ["precision", "recall", "f1", "clean_false_flags", "clean_penalty", "difficult_penalty", "applied_penalty", "clean_coverage"]
    out = ["| 조건 | " + " | ".join(keys) + " |", "|---" * (len(keys) + 1) + "|"]
    for name, r in rows:
        out.append(f"| {name} | " + " | ".join(str(r[k]) for k in keys) + " |")
    return "\n".join(out)


def baseline(params: AnalysisParams = AnalysisParams(), trials: int = 12) -> str:
    return table([(name, evaluate(params, noise, trials)) for name, noise in NOISE_GRID])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if cmd == "baseline":
        print(f"words={len(LAYOUT.words)} sentences={len(LAYOUT.sentence_words)}")
        print(baseline())


GRID = {
    "slow_factor": [1.6, 1.75, 2.0],
    "min_sentence_words": [3, 5],
    "reread_returns": [1, 2],
    "return_min_lines": [1.5, 2.0, 2.5],
    "return_min_ms": [350.0, 500.0],
    "disp_jitter_factor": [4.0, 6.0],
}
TUNE_NOISE = NOISE_GRID[1:3]  # good and typical webcams


def score(results: list[dict]) -> float:
    """Mean F1, minus a cost for flags on readers who had no difficulty."""
    f1 = st.mean(r["f1"] for r in results)
    false_flags = st.mean(r["clean_false_flags"] for r in results)
    unfair = st.mean(r["clean_penalty"] for r in results)
    return f1 - 0.12 * false_flags - (0.5 if unfair > 2.5 else 0.0)


def sweep(trials: int = 10):
    import itertools

    keys = list(GRID)
    best = []
    for values in itertools.product(*(GRID[k] for k in keys)):
        params = replace(AnalysisParams(), **dict(zip(keys, values)))
        res = [evaluate(params, noise, trials, seed0=0) for _, noise in TUNE_NOISE]
        best.append((score(res), dict(zip(keys, values)), res))
    best.sort(key=lambda b: b[0], reverse=True)
    return best


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "sweep":
    ranked = sweep()
    for sc, cfg, res in ranked[:8]:
        print(round(sc, 3), cfg, [(r["f1"], r["clean_false_flags"], r["clean_penalty"]) for r in res])


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "report":
    # held-out readers: seeds not used by the sweep
    print(table([(name, evaluate(AnalysisParams(), noise, trials=24, seed0=500)) for name, noise in NOISE_GRID]))
