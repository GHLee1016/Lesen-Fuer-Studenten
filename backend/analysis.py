"""Reading-behaviour analysis for webcam eye-tracking sessions.

All coordinates are in CSS pixels relative to the top-left corner of the
article element, so they are independent of page scroll.

Webcam trackers are noisy (typical error 100-200 px), so the analysis works
at paragraph level and treats within-line metrics as estimates.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Sample:
    t: float  # ms since reading started
    x: float
    y: float


@dataclass(frozen=True)
class Fixation:
    start: float
    end: float
    x: float
    y: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class AOI:
    """Area of interest: one block of text (title or paragraph)."""

    id: str
    x: float
    y: float
    w: float
    h: float
    words: int


@dataclass(frozen=True)
class AnalysisParams:
    # I-DT fixation detection. Dispersion = (max x - min x) + (max y - min y).
    max_dispersion_px: float = 120.0
    min_fixation_ms: float = 100.0
    # A gap longer than this means tracking was lost; windows never span it.
    max_gap_ms: float = 250.0
    # Tolerance around paragraph boxes, because webcam gaze lands a bit off.
    aoi_margin_x: float = 40.0
    aoi_margin_y: float = 18.0
    # Reference pace. A paragraph counts as fully read once the reader spent
    # at least the time this pace would need. Deliberately lenient for L2
    # learners: reading slower than this never costs points.
    reference_wpm: float = 200.0
    # Within-line regression: leftward jump of at least this many px while
    # staying on roughly the same line.
    regression_min_dx: float = 80.0
    skipped_coverage: float = 0.25
    # --- difficulty -------------------------------------------------------
    # A fixation belongs to a word if it is within this distance of the
    # word box (vertical tolerance is a fraction of the line height).
    word_margin_x: float = 30.0
    word_margin_y_lines: float = 0.45
    # Sentence is "slow" when its time per word is this many times the
    # reader's own median, and it took at least min_sentence_ms.
    slow_factor: float = 2.0
    min_sentence_ms: float = 1200.0
    min_sentence_words: int = 5
    # Sentence is "re-read" when the reader came back to it this many times
    # after moving on to a later sentence.
    reread_returns: int = 1
    # Word candidates: long dwell compared with the reader's median word dwell.
    word_min_ms: float = 700.0
    word_factor: float = 3.5
    max_difficult_words: int = 12
    # Penalty in score points.
    penalty_sentence_weight: float = 40.0  # x share of words in difficult sentences
    penalty_per_word: float = 1.5
    max_penalty: float = 25.0
    # --- robustness to webcam noise ---------------------------------------
    # A return to earlier text only counts when the gaze moved at least this
    # many lines above the furthest point read so far and stayed there for
    # return_min_ms. Webcam jitter of about a line otherwise looks like
    # constant re-reading.
    return_min_lines: float = 2.0
    return_min_ms: float = 500.0
    # I-DT dispersion grows with the tracker's frame-to-frame jitter.
    disp_jitter_factor: float = 6.0
    # Paragraph boxes get a vertical margin of this fraction of the
    # calibration error.
    aoi_margin_error_factor: float = 0.5
    # Word-level candidates need gaze accurate to about half a line.
    word_max_error_px: float = 40.0
    # The penalty is applied in full up to penalty_full_error_px of
    # calibration error, fades out linearly and is withheld from
    # penalty_zero_error_px on: with less accurate gaze the detection is
    # not reliable enough to cost a student points.
    penalty_full_error_px: float = 60.0
    penalty_zero_error_px: float = 150.0
    # Measured tracker quality for this session (filled in by adapt()).
    error_px: float = 0.0
    jitter_px: float = 0.0


def adapt(p: AnalysisParams, error_px: float | None, jitter_px: float | None) -> AnalysisParams:
    """Parameters for one session, given its measured calibration quality.

    error_px  - mean validation error after calibration (None: mouse demo)
    jitter_px - frame-to-frame gaze noise measured while fixating dots
    """
    from dataclasses import replace

    e = max(0.0, error_px or 0.0)
    j = max(0.0, jitter_px or 0.0)
    return replace(
        p,
        error_px=e,
        jitter_px=j,
        max_dispersion_px=max(p.max_dispersion_px, p.disp_jitter_factor * j),
        aoi_margin_y=max(p.aoi_margin_y, p.aoi_margin_error_factor * e),
    )


def reliability(p: AnalysisParams) -> float:
    """1.0 when gaze is accurate enough to penalise difficulty, falling to 0."""
    if p.error_px <= p.penalty_full_error_px:
        return 1.0
    if p.error_px >= p.penalty_zero_error_px:
        return 0.0
    return (p.penalty_zero_error_px - p.error_px) / (p.penalty_zero_error_px - p.penalty_full_error_px)


def detect_fixations(samples: Sequence[Sample], p: AnalysisParams = AnalysisParams()) -> list[Fixation]:
    """Dispersion-threshold identification (I-DT, Salvucci & Goldberg 2000)."""
    pts = sorted(samples, key=lambda s: s.t)
    fixations: list[Fixation] = []
    n = len(pts)
    i = 0
    while i < n:
        # Grow an initial window that covers the minimum duration.
        j = i
        while j + 1 < n and pts[j + 1].t - pts[j].t <= p.max_gap_ms and pts[j].t - pts[i].t < p.min_fixation_ms:
            j += 1
        if pts[j].t - pts[i].t < p.min_fixation_ms:
            i = j + 1
            continue

        xs = [s.x for s in pts[i : j + 1]]
        ys = [s.y for s in pts[i : j + 1]]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        if (maxx - minx) + (maxy - miny) > p.max_dispersion_px:
            i += 1
            continue

        # Extend the window while dispersion stays under the threshold.
        while j + 1 < n and pts[j + 1].t - pts[j].t <= p.max_gap_ms:
            nx, ny = pts[j + 1].x, pts[j + 1].y
            nminx, nmaxx = min(minx, nx), max(maxx, nx)
            nminy, nmaxy = min(miny, ny), max(maxy, ny)
            if (nmaxx - nminx) + (nmaxy - nminy) > p.max_dispersion_px:
                break
            minx, maxx, miny, maxy = nminx, nmaxx, nminy, nmaxy
            j += 1
            xs.append(nx)
            ys.append(ny)

        fixations.append(
            Fixation(start=pts[i].t, end=pts[j].t, x=sum(xs) / len(xs), y=sum(ys) / len(ys))
        )
        i = j + 1
    return fixations


def assign_aoi(f: Fixation, aois: Sequence[AOI], p: AnalysisParams) -> str | None:
    """Return the id of the AOI that contains the fixation (with margin)."""
    best, best_d = None, float("inf")
    for a in aois:
        if a.x - p.aoi_margin_x <= f.x <= a.x + a.w + p.aoi_margin_x and a.y - p.aoi_margin_y <= f.y <= a.y + a.h + p.aoi_margin_y:
            # Inside an expanded box. Margins can overlap, so prefer the box
            # whose vertical extent is closest.
            d = 0.0 if a.y <= f.y <= a.y + a.h else min(abs(f.y - a.y), abs(f.y - a.y - a.h))
            if d < best_d:
                best, best_d = a.id, d
    return best


def count_regressions(fixations: Sequence[Fixation], hits: Sequence[str | None], order: dict[str, int], line_height: float, p: AnalysisParams) -> tuple[int, int]:
    """Count (within-line, between-paragraph) backward eye movements."""
    within = between = 0
    prev: tuple[Fixation, str] | None = None
    for f, aoi in zip(fixations, hits):
        if aoi is None:
            continue
        if prev is not None:
            pf, paoi = prev
            if order[aoi] < order[paoi]:
                between += 1
            elif aoi == paoi and abs(f.y - pf.y) < line_height * 0.6 and pf.x - f.x >= p.regression_min_dx:
                within += 1
        prev = (f, aoi)
    return within, between


@dataclass(frozen=True)
class Word:
    x: float
    y: float
    w: float
    h: float
    s: int  # sentence index


def _median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    if not n:
        return 0.0
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def assign_words(fixations: Sequence[Fixation], words: Sequence[Word], line_height: float, p: AnalysisParams) -> list[int | None]:
    """Nearest word for every fixation (or None when it is between lines / off text)."""
    # bucket words by line so each fixation only checks nearby rows
    rows: dict[int, list[int]] = {}
    for i, w in enumerate(words):
        rows.setdefault(int((w.y + w.h / 2) // line_height), []).append(i)
    max_dy = line_height * p.word_margin_y_lines
    out: list[int | None] = []
    for f in fixations:
        r = int(f.y // line_height)
        best, best_cost = None, float("inf")
        for rr in (r - 1, r, r + 1):
            for i in rows.get(rr, ()):
                w = words[i]
                dx = max(0.0, w.x - f.x, f.x - (w.x + w.w))
                dy = max(0.0, w.y - f.y, f.y - (w.y + w.h))
                if dx > p.word_margin_x or dy > max_dy:
                    continue
                cost = dx + 2 * dy
                if cost < best_cost:
                    best, best_cost = i, cost
        out.append(best)
    return out


def difficulty(fixations: Sequence[Fixation], words: Sequence[Word], line_height: float, p: AnalysisParams = AnalysisParams()) -> dict:
    """Find sentences and words the reader struggled with.

    * slow: time per word in a sentence far above the reader's own median
    * reread: the reader went back to the sentence after reading further on
    * word candidates: unusually long dwell on one word, or returns to it
    Webcam gaze is only accurate to about a line, so word results are
    candidates; sentence results are the reliable unit.
    """
    hits = assign_words(fixations, words, line_height, p)
    n_sent = max((w.s for w in words), default=-1) + 1
    s_words = [0] * n_sent
    for w in words:
        s_words[w.s] += 1
    s_dwell = [0.0] * n_sent
    s_returns = [0] * n_sent
    w_dwell = [0.0] * len(words)
    w_returns = [0] * len(words)

    for f, wi in zip(fixations, hits):
        if wi is None:
            continue
        s_dwell[words[wi].s] += f.duration
        w_dwell[wi] += f.duration

    # Returns: episodes where the gaze went back well above the furthest
    # line read so far and stayed there. The frontier follows the median of
    # the last three fixations so a single noisy fixation cannot move it.
    back_px = p.return_min_lines * line_height
    frontier = float("-inf")
    recent: list[float] = []
    episode: list[int] = []

    def close_episode():
        if not episode:
            return
        if sum(fixations[k].duration for k in episode) >= p.return_min_ms:
            per_s: dict[int, float] = {}
            per_w: dict[int, float] = {}
            for k in episode:
                wi = hits[k]
                if wi is not None:
                    per_s[words[wi].s] = per_s.get(words[wi].s, 0.0) + fixations[k].duration
                    per_w[wi] = per_w.get(wi, 0.0) + fixations[k].duration
            if per_s:
                s_returns[max(per_s, key=per_s.get)] += 1
            if per_w:
                w_returns[max(per_w, key=per_w.get)] += 1
        episode.clear()

    for k, f in enumerate(fixations):
        if f.y <= frontier - back_px:
            episode.append(k)
            continue
        close_episode()
        recent = (recent + [f.y])[-3:]
        frontier = max(frontier, sorted(recent)[len(recent) // 2])
    close_episode()

    rates = [s_dwell[i] / s_words[i] for i in range(n_sent) if s_dwell[i] > 0 and s_words[i] >= p.min_sentence_words]
    baseline = _median(rates)

    sentences = []
    difficult_words_in_sentences = 0
    for i in range(n_sent):
        rate = s_dwell[i] / s_words[i] if s_words[i] else 0.0
        reasons = []
        if baseline and s_words[i] >= p.min_sentence_words and s_dwell[i] >= p.min_sentence_ms and rate >= p.slow_factor * baseline:
            reasons.append("slow")
        if s_returns[i] >= p.reread_returns:
            reasons.append("reread")
        if reasons:
            difficult_words_in_sentences += s_words[i]
        sentences.append({"s": i, "words": s_words[i], "dwell_ms": round(s_dwell[i]), "ms_per_word": round(rate), "returns": s_returns[i], "reasons": reasons})

    words_reliable = p.error_px <= p.word_max_error_px
    cand = []
    if words_reliable:
        fixated = [d for d in w_dwell if d > 0]
        w_base = _median(fixated)
        threshold = max(p.word_min_ms, p.word_factor * w_base)
        cand = [
            {"i": i, "dwell_ms": round(w_dwell[i]), "returns": w_returns[i]}
            for i in range(len(words))
            if w_dwell[i] >= threshold or w_returns[i] >= 2
        ]
        cand.sort(key=lambda c: (c["returns"], c["dwell_ms"]), reverse=True)
        cand = cand[: p.max_difficult_words]

    total = len(words) or 1
    share = difficult_words_in_sentences / total
    raw_penalty = min(p.max_penalty, p.penalty_sentence_weight * share + p.penalty_per_word * len(cand))
    rel = reliability(p)
    penalty = round(raw_penalty * rel)
    return {
        "baseline_ms_per_word": round(baseline),
        "sentences": sentences,
        "difficult_sentences": [x["s"] for x in sentences if x["reasons"]],
        "difficult_words": cand,
        "difficult_share": round(share, 3),
        "penalty": int(penalty),
        "raw_penalty": round(raw_penalty, 1),
        "reliability": round(rel, 2),
        "words_reliable": words_reliable,
        "word_hits": sum(1 for h in hits if h is not None),
    }


def analyze(
    samples: Iterable[Sample],
    aois: Sequence[AOI],
    duration_ms: float,
    line_height: float = 30.0,
    p: AnalysisParams = AnalysisParams(),
    words: Sequence[Word] | None = None,
) -> dict:
    samples = list(samples)
    fixations = detect_fixations(samples, p)
    hits = [assign_aoi(f, aois, p) for f in fixations]
    order = {a.id: k for k, a in enumerate(aois)}

    per = {
        a.id: {"id": a.id, "words": a.words, "dwell_ms": 0.0, "fixations": 0, "visits": 0, "first_visit_ms": None}
        for a in aois
    }
    prev_hit = None
    for f, aoi in zip(fixations, hits):
        if aoi is not None:
            st = per[aoi]
            st["dwell_ms"] += f.duration
            st["fixations"] += 1
            if aoi != prev_hit:
                st["visits"] += 1
            if st["first_visit_ms"] is None:
                st["first_visit_ms"] = round(f.start)
        prev_hit = aoi

    total_words = sum(a.words for a in aois)
    weighted = 0.0
    paragraphs = []
    for a in aois:
        st = per[a.id]
        expected = a.words / p.reference_wpm * 60000.0 if a.words else 0.0
        coverage = min(1.0, st["dwell_ms"] / expected) if expected > 0 else 0.0
        weighted += coverage * a.words
        paragraphs.append(
            {
                **st,
                "dwell_ms": round(st["dwell_ms"]),
                "expected_ms": round(expected),
                "coverage": round(coverage, 3),
                "skipped": coverage < p.skipped_coverage,
            }
        )

    reading_coverage = weighted / total_words if total_words else 0.0
    fix_time = sum(f.duration for f in fixations)
    on_text_time = sum(f.duration for f, h in zip(fixations, hits) if h is not None)
    within, between = count_regressions(fixations, hits, order, line_height, p)
    on_text_fix = sum(1 for h in hits if h is not None)
    minutes = duration_ms / 60000.0

    diff = difficulty(fixations, words, line_height, p) if words else None
    return {
        "difficulty": diff,
        "duration_ms": round(duration_ms),
        "samples": len(samples),
        "sample_rate_hz": round(len(samples) / (duration_ms / 1000.0), 1) if duration_ms > 0 else 0.0,
        "fixations": len(fixations),
        "mean_fixation_ms": round(fix_time / len(fixations)) if fixations else 0,
        "on_text_ratio": round(on_text_time / fix_time, 3) if fix_time else 0.0,
        "reading_coverage": round(reading_coverage, 3),
        "regressions_within_line": within,
        "regressions_between_paragraphs": between,
        "regression_rate": round((within + between) / on_text_fix, 3) if on_text_fix else 0.0,
        "wpm": round(total_words / minutes) if minutes > 0 else 0,
        "total_words": total_words,
        "skipped_paragraphs": [x["id"] for x in paragraphs if x["skipped"]],
        "paragraphs": paragraphs,
        "fixation_list": [
            {"t": round(f.start), "d": round(f.duration), "x": round(f.x, 1), "y": round(f.y, 1), "aoi": h}
            for f, h in zip(fixations, hits)
        ],
    }


def gaze_score(metrics: dict) -> int:
    """Paragraph coverage minus the difficulty penalty."""
    penalty = (metrics.get("difficulty") or {}).get("penalty", 0)
    return max(0, round(100 * metrics["reading_coverage"]) - penalty)


def final_score(gaze: float, quiz: float, gaze_weight: float = 0.6) -> int:
    return round(gaze * gaze_weight + quiz * (1 - gaze_weight))


def quality_flags(metrics: dict, calib_error_px: float | None, line_height: float) -> list[str]:
    """Hints for the teacher that a session's gaze data may be unreliable."""
    flags = []
    if calib_error_px is not None and calib_error_px > 4 * line_height:
        flags.append("high_calibration_error")
    if metrics["sample_rate_hz"] and metrics["sample_rate_hz"] < 10:
        flags.append("low_sample_rate")
    if metrics["fixations"] and metrics["on_text_ratio"] < 0.4:
        flags.append("gaze_mostly_off_text")
    return flags


def params_dict(p: AnalysisParams = AnalysisParams()) -> dict:
    return asdict(p)
