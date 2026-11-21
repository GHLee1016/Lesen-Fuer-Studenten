import random

from backend.analysis import AOI, AnalysisParams, Sample, analyze, detect_fixations, final_score

P = AnalysisParams()


def dwell(x, y, t0, ms, hz=30, jitter=10, rng=random.Random(0)):
    step = 1000 / hz
    return [Sample(t0 + k * step, x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter)) for k in range(int(ms / step))]


def test_single_fixation_is_detected():
    fx = detect_fixations(dwell(300, 200, 0, 500))
    assert len(fx) == 1
    assert abs(fx[0].x - 300) < 10 and abs(fx[0].y - 200) < 10
    assert 430 <= fx[0].duration <= 500


def test_two_targets_give_two_fixations():
    s = dwell(100, 100, 0, 400) + dwell(600, 100, 400, 400)
    fx = detect_fixations(s)
    assert len(fx) == 2
    assert fx[0].x < 200 < fx[1].x


def test_short_glance_is_not_a_fixation():
    assert detect_fixations(dwell(100, 100, 0, 60)) == []


def test_tracking_gap_splits_fixations():
    s = dwell(100, 100, 0, 300) + dwell(100, 100, 1300, 300)
    assert len(detect_fixations(s)) == 2


def test_noise_spread_wider_than_threshold_yields_nothing():
    rng = random.Random(1)
    s = [Sample(k * 33, rng.uniform(0, 1000), rng.uniform(0, 1000)) for k in range(300)]
    assert detect_fixations(s) == []


def _aois():
    # two paragraphs of 50 words each, one under the other
    return [AOI("p1", 0, 0, 800, 200, 50), AOI("p2", 0, 260, 800, 200, 50)]


def read_paragraph(top, t0, lines=5, line_h=40, per_fix_ms=250, fix_per_line=4):
    out, t = [], t0
    for ln in range(lines):
        for k in range(fix_per_line):
            out += dwell(100 + k * 180, top + 20 + ln * line_h, t, per_fix_ms)
            t += per_fix_ms
    return out, t


def test_reading_both_paragraphs_gives_full_coverage():
    s1, t = read_paragraph(0, 0)
    s2, t = read_paragraph(260, t)
    m = analyze(s1 + s2, _aois(), duration_ms=t, line_height=40)
    # 20 fixations * 250 ms = 5 s per paragraph; 50 words at 200 wpm = 15 s
    p1, p2 = m["paragraphs"]
    assert p1["fixations"] == 20 and p2["fixations"] == 20
    assert abs(p1["coverage"] - p2["coverage"]) < 0.05
    assert m["skipped_paragraphs"] == []
    assert m["on_text_ratio"] == 1.0


def test_coverage_saturates_and_skipped_paragraph_is_reported():
    s, t = read_paragraph(0, 0, lines=5, per_fix_ms=1000)  # 20 s on p1, nothing on p2
    m = analyze(s, _aois(), duration_ms=t, line_height=40)
    p1, p2 = m["paragraphs"]
    assert p1["coverage"] == 1.0
    assert p2["coverage"] == 0.0
    assert m["skipped_paragraphs"] == ["p2"]
    assert m["reading_coverage"] == 0.5


def test_regressions_are_counted():
    s = (
        dwell(100, 30, 0, 300)
        + dwell(500, 30, 300, 300)
        + dwell(200, 30, 600, 300)  # back within the same line
        + dwell(100, 300, 900, 300)  # p2
        + dwell(100, 30, 1200, 300)  # back to p1
    )
    m = analyze(s, _aois(), duration_ms=1500, line_height=40)
    assert m["regressions_within_line"] == 1
    assert m["regressions_between_paragraphs"] == 1


def test_return_sweep_is_not_a_regression():
    s = dwell(700, 30, 0, 300) + dwell(100, 70, 300, 300)  # end of line 1 -> start of line 2
    m = analyze(s, _aois(), duration_ms=600, line_height=40)
    assert m["regressions_within_line"] == 0


def test_off_text_gaze():
    s = dwell(400, 900, 0, 1000)
    m = analyze(s, _aois(), duration_ms=1000)
    assert m["fixations"] == 1 and m["on_text_ratio"] == 0.0
    assert m["fixation_list"][0]["aoi"] is None


def test_final_score_weighting():
    assert final_score(100, 0) == 60
    assert final_score(50, 100) == 70
