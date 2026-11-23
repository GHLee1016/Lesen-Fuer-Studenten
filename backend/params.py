"""Analysis parameters in effect: code defaults plus overrides saved from the
teacher dashboard (after comparing candidates against real sessions)."""

from __future__ import annotations

from dataclasses import asdict, replace

from . import db
from .analysis import AnalysisParams

SETTING_KEY = "analysis_params"

# Parameters a teacher may change, with allowed range and a short label.
TUNABLE: dict[str, tuple[float, float, str]] = {
    "slow_factor": (1.2, 4.0, "느린 문장: 본인 중앙값 대비 배수"),
    "min_sentence_words": (2, 15, "느린 문장: 최소 단어 수"),
    "min_sentence_ms": (300, 5000, "느린 문장: 최소 체류 시간(ms)"),
    "reread_returns": (1, 4, "다시 읽기: 필요한 되돌아감 횟수"),
    "return_min_lines": (0.5, 5.0, "다시 읽기: 최소 되돌아간 줄 수"),
    "return_min_ms": (100, 3000, "다시 읽기: 최소 머문 시간(ms)"),
    "word_factor": (1.5, 10.0, "단어 후보: 본인 중앙값 대비 배수"),
    "word_max_error_px": (0, 200, "단어 후보: 허용 보정 오차(px)"),
    "penalty_full_error_px": (0, 300, "감점 100% 적용 보정 오차(px)"),
    "penalty_zero_error_px": (10, 400, "감점 보류 보정 오차(px)"),
    "max_penalty": (0, 50, "감점 상한(점)"),
    "reference_wpm": (80, 400, "문단 커버리지 기준 속도(WPM)"),
}
INT_FIELDS = {"min_sentence_words", "reread_returns"}


class InvalidParams(ValueError):
    pass


def validate(overrides: dict) -> dict:
    out = {}
    for k, v in overrides.items():
        if k not in TUNABLE:
            raise InvalidParams(f"'{k}' 값은 바꿀 수 없어요.")
        lo, hi, _ = TUNABLE[k]
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise InvalidParams(f"'{k}' 값이 숫자가 아니에요.") from None
        if not lo <= v <= hi:
            raise InvalidParams(f"'{k}' 값은 {lo}–{hi} 사이여야 해요.")
        out[k] = int(round(v)) if k in INT_FIELDS else v
    eff = replace(AnalysisParams(), **out)
    if eff.penalty_zero_error_px <= eff.penalty_full_error_px:
        raise InvalidParams("감점 보류 오차는 100% 적용 오차보다 커야 해요.")
    return out


def overrides() -> dict:
    return db.get_setting(SETTING_KEY, {}) or {}


def current() -> AnalysisParams:
    return replace(AnalysisParams(), **overrides())


def save(new_overrides: dict) -> dict:
    clean = validate(new_overrides)
    db.set_setting(SETTING_KEY, clean)
    return clean


def describe() -> dict:
    defaults = asdict(AnalysisParams())
    ov = overrides()
    return {
        "fields": [
            {"key": k, "label": label, "min": lo, "max": hi, "default": defaults[k], "value": ov.get(k, defaults[k]), "overridden": k in ov}
            for k, (lo, hi, label) in TUNABLE.items()
        ],
        "overrides": ov,
    }
