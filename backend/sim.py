"""Synthetic readers with webcam-like gaze noise, used to tune the analysis.

A simulated reader goes through a laid-out text word by word (fixations with
realistic durations, skips, short regressions and return sweeps). Chosen
sentences are made difficult, either by reading them slowly or by coming back
to them after reading on. The resulting fixation sequence is turned into a
30 Hz sample stream and distorted like a webcam tracker would distort it:

* bias      - a constant offset left over after calibration (the "validation
              error" the calibration screen reports)
* jitter    - frame-to-frame noise, correlated over time (WebGazer applies a
              Kalman filter, so the noise is smooth rather than white)
* drift     - slow wandering of the offset over the session (head movement)
* dropouts  - short gaps where the face was not found

Because we know which sentences were difficult, detection quality and the
penalty given to readers without any difficulty can be measured.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from .analysis import AOI, Sample, Word

# Layout of the student page (frontend/css/reader.css): Tinos 21px, line
# height 2.3, ~656 px text column, 34 px paragraph gap.
FONT_PX = 21.0
CHAR_W = 9.6
SPACE_W = 5.3
LINE_H = FONT_PX * 2.3
TEXT_W = 656.0
LEFT = 68.0
TOP = 52.0
PARA_GAP = 34.0
TITLE_LINE_H = 54.0

# Sentence splitting mirrors frontend/js/article.js so indices line up.
SENT_END = re.compile(r"[.!?…][\"“”»«’‘)\]]*$")
SENT_START = re.compile(r"^[\"„“»«‘(\[]?[A-ZÄÖÜ0-9]")
ORDINAL = re.compile(r"^\d+\.$")
ABBR = {"z.", "B.", "z.B.", "bzw.", "ca.", "Dr.", "Prof.", "Nr.", "usw.", "etc.", "vgl.", "u.a.", "d.h.", "evtl.", "ggf.", "Hr.", "Fr.", "St.", "Mio.", "Mrd."}


def ends_sentence(tok: str, nxt: str | None) -> bool:
    return bool(SENT_END.search(tok)) and tok not in ABBR and not ORDINAL.match(tok) and (nxt is None or bool(SENT_START.match(nxt)))


@dataclass
class Layout:
    words: list[Word]
    texts: list[str]
    aois: list[AOI]
    sentence_words: list[list[int]]
    line_height: float = LINE_H


def layout_text(title: str, paragraphs: list[str], lead: str | None = None) -> Layout:
    words: list[Word] = []
    texts: list[str] = []
    aois: list[AOI] = []
    sentence_words: list[list[int]] = []
    y = TOP
    blocks = [("title", title, TITLE_LINE_H, CHAR_W * 1.75)]
    if lead:
        blocks.append(("lead", lead, LINE_H, CHAR_W))
    blocks += [(f"p{i + 1}", p, LINE_H, CHAR_W) for i, p in enumerate(paragraphs)]
    for aoi_id, text, lh, cw in blocks:
        tokens = text.split()
        x = LEFT
        top = y
        sentence: list[int] = []
        for k, tok in enumerate(tokens):
            w = len(tok) * cw
            if x > LEFT and x + w > LEFT + TEXT_W:
                x = LEFT
                y += lh
            s = len(sentence_words)
            words.append(Word(x=x, y=y + (lh - FONT_PX * 1.15) / 2, w=w, h=FONT_PX * 1.15, s=s))
            texts.append(tok)
            sentence.append(len(words) - 1)
            x += w + SPACE_W
            if ends_sentence(tok, tokens[k + 1] if k + 1 < len(tokens) else None) or k == len(tokens) - 1:
                sentence_words.append(sentence)
                sentence = []
        y += lh
        aois.append(AOI(aoi_id, LEFT, top, TEXT_W, y - top, len(tokens)))
        y += PARA_GAP
    return Layout(words, texts, aois, sentence_words)


# ----------------------------------------------------------------- reader ----


@dataclass
class ReaderProfile:
    mean_fix_ms: float = 230.0       # typical L2 reader
    sd_fix_ms: float = 80.0
    skip_short: float = 0.25          # skip probability for words <= 3 chars
    refix: float = 0.12               # second fixation on long words
    regress: float = 0.06             # short within-sentence regression
    slow_factor: tuple[float, float] = (1.8, 2.6)
    slow_refix: float = 0.45


@dataclass
class Truth:
    slow: set[int] = field(default_factory=set)
    reread: set[int] = field(default_factory=set)

    @property
    def difficult(self) -> set[int]:
        return self.slow | self.reread


def _dur(rng: random.Random, mean: float, sd: float) -> float:
    # log-normal fixation durations, clipped to plausible values
    sigma2 = math.log(1 + (sd / mean) ** 2)
    mu = math.log(mean) - sigma2 / 2
    return max(90.0, min(1200.0, rng.lognormvariate(mu, math.sqrt(sigma2))))


def read(layout: Layout, truth: Truth, rng: random.Random, prof: ReaderProfile = ReaderProfile()) -> list[tuple[float, float, float]]:
    """Fixation sequence (duration_ms, x, y) for one read-through."""
    fixes: list[tuple[float, float, float]] = []

    def fix_word(i: int, factor: float = 1.0, refix: float = prof.refix):
        w = layout.words[i]
        n = 2 if len(layout.texts[i]) > 7 and rng.random() < refix else 1
        for k in range(n):
            x = w.x + w.w * (0.35 + 0.3 * rng.random()) if n == 1 else w.x + w.w * (0.25 + 0.5 * k)
            fixes.append((_dur(rng, prof.mean_fix_ms * factor, prof.sd_fix_ms * factor), x, w.y + w.h / 2))

    def read_sentence(s: int, factor: float = 1.0, refix: float = prof.refix):
        ids = layout.sentence_words[s]
        k = 0
        while k < len(ids):
            i = ids[k]
            if len(layout.texts[i]) <= 3 and factor == 1.0 and rng.random() < prof.skip_short:
                k += 1
                continue
            fix_word(i, factor, refix)
            if k > 1 and rng.random() < prof.regress:
                fix_word(ids[k - rng.randint(1, min(2, k))])
            k += 1

    pending_rereads: dict[int, list[int]] = {}  # trigger sentence -> sentences to go back to
    for s in sorted(truth.reread):
        times = 2 if rng.random() < 0.5 else 1
        trigger = min(s + 1, len(layout.sentence_words) - 1)
        pending_rereads.setdefault(trigger, []).extend([s] * times)

    for s in range(len(layout.sentence_words)):
        if s in truth.slow:
            read_sentence(s, rng.uniform(*prof.slow_factor), prof.slow_refix)
        else:
            read_sentence(s)
        for back in pending_rereads.get(s, []):
            read_sentence(back, 1.2)
            # continue where we were: re-fixate the end of the current sentence
            fix_word(layout.sentence_words[s][-1])
    return fixes


# ------------------------------------------------------------- tracker ----


@dataclass
class NoiseModel:
    bias_px: float = 80.0      # magnitude of the constant offset (validation error)
    jitter_px: float = 40.0    # sd of frame-to-frame noise per axis
    rho: float = 0.75          # AR(1) correlation of the jitter between frames
    drift_px_per_min: float = 30.0
    dropout_rate: float = 0.01  # chance per frame that a gap starts
    hz: float = 30.0


def to_samples(fixes, rng: random.Random, noise: NoiseModel) -> tuple[list[Sample], float]:
    """Samples at `hz` with saccades between fixations, then add tracker noise."""
    dt = 1000.0 / noise.hz
    clean: list[tuple[float, float, float]] = []
    t = 0.0
    px = py = None
    for d, x, y in fixes:
        if px is not None:  # saccade, ~30-60 ms, positions interpolated
            sac = 30 + 0.08 * math.hypot(x - px, y - py)
            steps = max(1, int(sac // dt))
            for k in range(1, steps + 1):
                clean.append((t, px + (x - px) * k / (steps + 1), py + (y - py) * k / (steps + 1)))
                t += dt
        n = max(1, int(round(d / dt)))
        for _ in range(n):
            clean.append((t, x, y))
            t += dt
        px, py = x, y

    angle = rng.uniform(0, 2 * math.pi)
    bx, by = noise.bias_px * math.cos(angle), noise.bias_px * math.sin(angle)
    nx = ny = 0.0
    drift_x = drift_y = 0.0
    drift_step = noise.drift_px_per_min / math.sqrt(60 * noise.hz)
    s = math.sqrt(1 - noise.rho ** 2) * noise.jitter_px
    out: list[Sample] = []
    gap = 0
    for t, x, y in clean:
        nx = noise.rho * nx + s * rng.gauss(0, 1)
        ny = noise.rho * ny + s * rng.gauss(0, 1)
        drift_x += drift_step * rng.gauss(0, 1)
        drift_y += drift_step * rng.gauss(0, 1)
        if gap > 0:
            gap -= 1
            continue
        if rng.random() < noise.dropout_rate:
            gap = rng.randint(3, 12)
            continue
        out.append(Sample(t, x + bx + nx + drift_x, y + by + ny + drift_y))
    duration = clean[-1][0] + dt if clean else 0.0
    return out, duration


def pick_truth(layout: Layout, rng: random.Random, n_slow: int, n_reread: int) -> Truth:
    # only sentences with a few words, and not the title
    candidates = [s for s, ids in enumerate(layout.sentence_words) if len(ids) >= 5 and layout.words[ids[0]].y > TOP + TITLE_LINE_H]
    chosen = rng.sample(candidates, min(len(candidates), n_slow + n_reread))
    return Truth(slow=set(chosen[:n_slow]), reread=set(chosen[n_slow:]))
