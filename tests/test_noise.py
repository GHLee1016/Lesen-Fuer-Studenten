"""Behaviour under webcam-like noise, using the reading simulator."""

import random
import statistics as st

from backend.analysis import AnalysisParams, adapt, analyze, reliability
from backend.sim import NoiseModel, Truth, layout_text, pick_truth, read, to_samples

TEXT = [
    "Am Morgen geht Anna zur Schule. Der Weg ist lang, aber sie geht gern zu Fuß. "
    "Unterwegs trifft sie ihre Freundin Lea. Die beiden reden über den Test am Nachmittag.",
    "In der Schule ist es laut. Viele Kinder spielen noch auf dem Hof, bevor der Unterricht beginnt. "
    "Anna sucht ihr Heft und merkt, dass sie es zu Hause vergessen hat. Sie ist ein bisschen nervös.",
    "Die Lehrerin erklärt die Aufgaben sehr genau. Danach schreiben alle Schüler still. "
    "Anna denkt lange über die zweite Frage nach, weil sie das Wort nicht kennt. Am Ende ist sie aber zufrieden.",
] * 2
LAYOUT = layout_text("Ein Tag in der Schule", TEXT)
TYPICAL = NoiseModel(bias_px=80, jitter_px=45)


def session(truth, noise, seed, error, jitter):
    rng = random.Random(seed)
    samples, duration = to_samples(read(LAYOUT, truth, rng), rng, noise)
    p = adapt(AnalysisParams(), error, jitter)
    return analyze(samples, LAYOUT.aois, duration, LAYOUT.line_height, p, words=LAYOUT.words)


def test_adapt_widens_tolerances_with_noise():
    p = adapt(AnalysisParams(), 100, 45)
    assert p.max_dispersion_px == 6 * 45
    assert p.aoi_margin_y == 50
    q = adapt(AnalysisParams(), None, None)  # mouse demo
    assert q.max_dispersion_px == AnalysisParams().max_dispersion_px and q.error_px == 0


def test_reliability_fades_with_calibration_error():
    assert reliability(adapt(AnalysisParams(), 40, 20)) == 1.0
    assert reliability(adapt(AnalysisParams(), 105, 20)) == 0.5
    assert reliability(adapt(AnalysisParams(), 200, 20)) == 0.0


def test_readers_without_difficulty_are_not_penalised_by_noise():
    flags, pens = [], []
    for seed in range(8):
        d = session(Truth(), TYPICAL, seed, 90, 45)["difficulty"]
        flags.append(len(d["difficult_sentences"]))
        pens.append(d["raw_penalty"])
    # before the noise-aware analysis this was ~30 flags and the full 25 points
    assert st.mean(flags) <= 2.5
    assert st.mean(pens) <= 4


def test_difficult_readers_get_more_penalty_than_clean_ones():
    clean, hard = [], []
    for seed in range(8):
        clean.append(session(Truth(), NoiseModel(bias_px=40, jitter_px=25), seed, 45, 25)["difficulty"]["raw_penalty"])
        truth = pick_truth(LAYOUT, random.Random(100 + seed), 2, 2)
        hard.append(session(truth, NoiseModel(bias_px=40, jitter_px=25), 200 + seed, 45, 25)["difficulty"]["raw_penalty"])
    assert st.mean(hard) > st.mean(clean) + 1


def test_inaccurate_calibration_withholds_penalty_and_word_candidates():
    truth = pick_truth(LAYOUT, random.Random(7), 3, 2)
    d = session(truth, NoiseModel(bias_px=150, jitter_px=70), 7, 170, 70)["difficulty"]
    assert d["penalty"] == 0 and d["reliability"] == 0
    assert d["words_reliable"] is False and d["difficult_words"] == []


def test_ideal_tracking_still_finds_difficult_sentences():
    found = total = 0
    for seed in range(6):
        truth = pick_truth(LAYOUT, random.Random(300 + seed), 2, 2)
        d = session(truth, NoiseModel(bias_px=0, jitter_px=8, drift_px_per_min=0, dropout_rate=0), 400 + seed, None, None)["difficulty"]
        found += len(set(d["difficult_sentences"]) & truth.difficult)
        total += len(truth.difficult)
    assert found / total >= 0.75
