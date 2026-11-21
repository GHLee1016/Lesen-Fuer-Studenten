"""Word glosses for the reader: German word in context -> learner's language.

Providers, tried in order:
  1. Claude (when ANTHROPIC_API_KEY is set): context-aware, returns the
     dictionary form, part of speech and article as well as the meaning.
  2. MyMemory (free, no key): translates the bare word.
Results are cached in the database so each word/sentence pair is only
translated once per language.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

log = logging.getLogger("lesen.translate")

LANGUAGES = {
    "ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文", "vi": "Tiếng Việt",
    "es": "Español", "fr": "Français", "ru": "Русский", "uk": "Українська",
    "tr": "Türkçe", "ar": "العربية", "pl": "Polski", "it": "Italiano", "pt": "Português",
}
LANGUAGE_NAMES_EN = {
    "ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Simplified Chinese", "vi": "Vietnamese",
    "es": "Spanish", "fr": "French", "ru": "Russian", "uk": "Ukrainian", "tr": "Turkish",
    "ar": "Arabic", "pl": "Polish", "it": "Italian", "pt": "Portuguese",
}

CLAUDE_MODEL = os.environ.get("LESEN_TRANSLATE_MODEL", "claude-opus-5-5")
WORD_RE = re.compile(r"[\wÄÖÜäöüß\-]+")


class Gloss(BaseModel):
    lemma: str = Field(description="Dictionary form of the word, e.g. 'gehen' for 'ging', 'Katze' for 'Katzen'.")
    article: str = Field(description="'der', 'die' or 'das' for nouns, otherwise an empty string.")
    pos: str = Field(description="Part of speech in the target language, short (e.g. '동사', 'noun').")
    translation: str = Field(description="Short meaning in the target language as used in this sentence (1-5 words).")
    note: str = Field(description="Optional very short usage hint in the target language, or an empty string.")


def clean_word(word: str) -> str:
    m = WORD_RE.search(word or "")
    return m.group(0) if m else ""


def cache_key(provider: str, lang: str, word: str, sentence: str) -> str:
    raw = f"{provider}|{lang}|{word}|{sentence if provider == 'claude' else ''}"
    return hashlib.sha1(raw.encode()).hexdigest()


# ------------------------------------------------------------- providers ----


def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_client = None


def _claude_client():
    global _client
    if _client is None:
        import anthropic  # optional dependency, only needed with a key

        _client = anthropic.Anthropic(timeout=20.0, max_retries=1)
    return _client


SCHEMA = {
    "type": "object",
    "properties": {
        "lemma": {"type": "string"},
        "article": {"type": "string"},
        "pos": {"type": "string"},
        "translation": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["lemma", "article", "pos", "translation", "note"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write glosses for German learners reading a text. Given a word and the sentence it "
    "appears in, return its dictionary form, its article if it is a noun, its part of speech and "
    "its meaning in this sentence, all in the learner's language except the German lemma. Keep "
    "the translation to a few words that fit under the word on screen."
)


def gloss_claude(word: str, sentence: str, lang: str) -> dict | None:
    import anthropic

    target = LANGUAGE_NAMES_EN.get(lang, "English")
    try:
        res = _claude_client().beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM,
            messages=[{"role": "user", "content": f"Learner's language: {target}\nWord: {word}\nSentence: {sentence}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            # If the request is declined, let the API retry it on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError:
        log.warning("Claude rate limited; falling back")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude error %s; falling back", e.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude unreachable; falling back")
        return None
    if res.stop_reason == "refusal":
        return None
    text = next((b.text for b in res.content if b.type == "text"), "")
    try:
        g = Gloss.model_validate(json.loads(text))
    except (ValueError, json.JSONDecodeError):
        return None
    return {**g.model_dump(), "source": "claude"}


SCRIPT_RE = {
    "ko": re.compile(r"[\uac00-\ud7a3]"), "ja": re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]"),
    "zh": re.compile(r"[\u4e00-\u9fff]"), "ru": re.compile(r"[\u0400-\u04ff]"),
    "uk": re.compile(r"[\u0400-\u04ff]"), "ar": re.compile(r"[\u0600-\u06ff]"),
}


def pick_mymemory(word: str, lang: str, data: dict) -> str | None:
    """MyMemory mixes dictionary hits with junk from its translation memory
    (e.g. 'MAN' for 'man', "''not used''"). Keep the first plausible one."""
    candidates = [((data.get("responseData") or {}).get("translatedText") or "", (data.get("responseData") or {}).get("match") or 0)]
    candidates += [(m.get("translation") or "", m.get("match") or 0) for m in data.get("matches") or []]
    script = SCRIPT_RE.get(lang)
    for text, match in candidates:
        t = text.strip().strip("'\"„“”«» ").replace("\xa0", " ")
        if not t or "not used" in t.lower() or "MYMEMORY" in t.upper():
            continue
        if t.lower() == word.lower() or (t.isupper() and len(t) > 1 and t.lower() == word.lower()):
            continue
        if script and not script.search(t):
            continue
        if len(t.split()) > 6 or float(match or 0) < 0.5:
            continue
        return t
    return None


def gloss_mymemory(word: str, lang: str) -> dict | None:
    url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": word, "langpair": f"de|{lang}"})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LesenFuerStudenten/1.0"}), timeout=8) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        log.warning("MyMemory failed: %s", e)
        return None
    if data.get("responseStatus") != 200:
        return None
    text = pick_mymemory(word, lang, data)
    if not text:
        return None
    return {"lemma": word, "article": "", "pos": "", "translation": text, "note": "", "source": "mymemory"}


def gloss(word: str, sentence: str, lang: str, cache_get, cache_put, providers=None) -> dict | None:
    """Look up a gloss, using the cache first. Returns None when no provider answered."""
    if providers is None:
        providers = []
        if claude_available():
            providers.append(("claude", lambda: gloss_claude(word, sentence, lang)))
        providers.append(("mymemory", lambda: gloss_mymemory(word, lang)))
    for name, fn in providers:
        key = cache_key(name, lang, word.lower(), sentence)
        hit = cache_get(key)
        if hit:
            return hit
        result = fn()
        if result:
            cache_put(key, result)
            return result
    return None
