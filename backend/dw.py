"""Importer for DW Learn German lessons (learngerman.dw.com).

A lesson page embeds its data as Apollo client state. From it we take the
manuscript (the reading text), the course title and CEFR level, DW's own
German glossary, and the comprehension exercises with their answers.

Imported texts are stored in the local database only (never in the
repository) and always shown with a link to the original lesson.
"""

from __future__ import annotations

import html as htmllib
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DW_HOST = "learngerman.dw.com"
USER_AGENT = "Mozilla/5.0 (compatible; LesenFuerStudenten/1.0; educational reader)"
TIMEOUT_S = 15

# dkLearningLevel is a bit mask (observed: 8 = B1 "Top-Thema", 16 = B2
# "Video-Thema", 32 = C1 "Alltagsdeutsch").
LEVEL_BITS = [(1, "A1"), (2, "A1"), (4, "A2"), (8, "B1"), (16, "B2"), (32, "C1"), (64, "C2")]
CEFR_RE = re.compile(r"\b([ABC][12])\b")
# Exercises that check understanding of the text (not vocabulary or grammar).
COMPREHENSION_RE = re.compile(r"text|inhalt|verst", re.I)
SPEAKER_RE = re.compile(r"^(?:[A-ZÄÖÜ][A-ZÄÖÜ .\-]{1,30}):\s+")
MAX_QUESTIONS = 8


class ImportError_(Exception):
    """Raised with a user-facing message when a lesson cannot be imported."""


# --------------------------------------------------------------- fetching ----


def lesson_id_from_url(url: str) -> int:
    u = urllib.parse.urlparse(url.strip())
    if u.scheme not in ("http", "https") or u.netloc != DW_HOST:
        raise ImportError_("learngerman.dw.com 레슨 주소만 가져올 수 있어요.")
    m = re.search(r"/l-(\d+)", urllib.parse.unquote(u.path))
    if not m:
        raise ImportError_("레슨 주소가 아니에요. 주소에 '/l-숫자'가 있는 개별 레슨 페이지를 넣어 주세요.")
    return int(m.group(1))


def _quote_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(u.path), safe="/-_.~")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, path, u.query, ""))


def fetch(url: str) -> str:
    req = urllib.request.Request(_quote_url(url), headers={"User-Agent": USER_AGENT, "Accept-Language": "de"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            return res.read().decode("utf-8", "replace")
    except Exception as e:  # network errors, HTTP errors
        raise ImportError_(f"DW 페이지를 불러오지 못했어요 ({e}).") from e


def apollo_state(page: str) -> dict:
    m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", page, re.S)
    if not m:
        raise ImportError_("페이지에서 레슨 데이터를 찾지 못했어요. DW 사이트 구조가 바뀌었을 수 있어요.")
    return json.loads(m.group(1))


# ---------------------------------------------------------------- parsing ----


def _resolve(state: dict, ref):
    return state.get(ref["__ref"]) if isinstance(ref, dict) and "__ref" in ref else ref


def _plain(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment or "")
    text = htmllib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


class _GlossaryCollector(HTMLParser):
    """Collects the surface text of every DW glossary span with its id."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str | None] = []
        self.found: list[tuple[str, str]] = []
        self._buf: dict[str, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        a = dict(attrs)
        gid = a.get("data-id") if a.get("data-type") == "GLOSSARY" else None
        self.stack.append(gid)
        if gid:
            self._buf[gid] = []

    def handle_endtag(self, tag):
        if tag == "span" and self.stack:
            gid = self.stack.pop()
            if gid and gid in self._buf:
                self.found.append((gid, re.sub(r"\s+", " ", "".join(self._buf.pop(gid))).strip()))

    def handle_data(self, data):
        for gid in self.stack:
            if gid and gid in self._buf:
                self._buf[gid].append(data)


def split_manuscript(manuscript: str) -> list[str]:
    """Paragraphs of plain text. Blocks are separated by </p> or a blank <br>."""
    parts = re.split(r"</p>|<br\s*/?>\s*(?:&nbsp;|\s)*<br\s*/?>", manuscript or "", flags=re.I)
    out = []
    for p in parts:
        t = _plain(re.sub(r"<br\s*/?>", " ", p, flags=re.I))
        t = SPEAKER_RE.sub("", t)
        if t:
            out.append(t)
    return out


def cefr_level(lesson: dict, state: dict) -> str:
    for nav in lesson.get("navigations") or []:
        n = _resolve(state, nav) or {}
        for field in ("title", "name"):
            m = CEFR_RE.search(n.get(field) or "")
            if m:
                return m.group(1)
    mask = lesson.get("dkLearningLevel") or 0
    labels = []
    for bit, label in LEVEL_BITS:
        if mask & bit and label not in labels:
            labels.append(label)
    if not labels:
        return "?"
    return labels[0] if len(labels) == 1 else f"{labels[0]}–{labels[-1]}"


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s).lower()


def parse_lesson(state: dict, lesson_id: int) -> dict:
    lesson = state.get(f"Lesson:{lesson_id}")
    if not lesson:
        raise ImportError_("레슨 데이터가 비어 있어요.")
    title = _plain(lesson.get("name") or "")
    teaser = _plain(lesson.get("teaser") or "")
    blocks = split_manuscript(lesson.get("manuscript") or "")
    if not blocks:
        raise ImportError_("이 레슨에는 읽을 원고(Manuskript)가 없어요.")

    # The manuscript usually starts with the bold title and the teaser.
    if blocks and _norm(blocks[0]) == _norm(title):
        blocks = blocks[1:]
    lead = ""
    if blocks and teaser and _norm(blocks[0]) == _norm(teaser):
        lead, blocks = blocks[0], blocks[1:]

    # DW glossary: German explanations for marked words.
    knowledge = {
        str(v["id"]): v
        for v in state.values()
        if isinstance(v, dict) and v.get("__typename") == "Knowledge" and v.get("knowledgeType") == "GLOSSARY"
    }
    collector = _GlossaryCollector()
    collector.feed(lesson.get("manuscript") or "")
    glossary: dict[str, dict] = {}
    for gid, surface in collector.found:
        k = knowledge.get(gid)
        if not k or not surface:
            continue
        entry = glossary.setdefault(gid, {"term": _plain(k.get("name") or ""), "definition": _plain(k.get("text") or ""), "surface": []})
        if surface not in entry["surface"]:
            entry["surface"].append(surface)

    exercises = [
        p["target"]
        for p in lesson.get("overviewParts") or []
        if p.get("lessonPart") == "EXERCISE" and isinstance(p.get("target"), dict)
    ]
    comprehension = []
    for ex in exercises:
        url = next((v for k, v in ex.items() if k.startswith("namedUrl")), None)
        if url and COMPREHENSION_RE.search(ex.get("name") or ""):
            comprehension.append({"name": ex.get("name"), "url": f"https://{DW_HOST}{url}"})

    return {
        "id": f"dw-{lesson_id}",
        "level": cefr_level(lesson, state),
        "title": title,
        "lead": lead,
        "paragraphs": blocks,
        "glossary": list(glossary.values()),
        "source": {"name": "DW Deutsch lernen", "url": lesson.get("canonicalUrl") or f"https://{DW_HOST}/de/l-{lesson_id}"},
        "published": lesson.get("firstPublicationDate"),
        "_exercise_pages": comprehension,
    }


def parse_exercise(state: dict) -> list[dict]:
    """Choice questions from an exercise page; cloze and free-text are skipped."""
    questions = []
    exercises = [v for v in state.values() if isinstance(v, dict) and v.get("__typename") == "Exercise"]
    for ex in exercises:
        for ref in ex.get("inquiries") or []:
            top = _resolve(state, ref) or {}
            groups = [top] + [_resolve(state, s) or {} for s in top.get("subInquiries") or []]
            for g in groups:
                alts = [_resolve(state, a) or {} for a in g.get("alternatives") or []]
                alts = [a for a in alts if a.get("alternativeText")]
                if len(alts) < 2 or g.get("clozeType"):
                    continue
                question = _plain(g.get("inquiryText") or "") or _plain(top.get("inquiryText") or "")
                if not question:
                    continue
                options = [_plain(a["alternativeText"]) for a in alts]
                answer = [i for i, a in enumerate(alts) if a.get("isCorrect")]
                if not answer:
                    continue
                multiple = top.get("selectionType") == "MULTIPLE" or len(answer) > 1
                questions.append({"question": question, "options": options, "answer": answer, "multiple": multiple})
    return questions


def import_lesson(url: str, fetcher=None) -> dict:
    fetcher = fetcher or fetch
    lesson_id = lesson_id_from_url(url)
    state = apollo_state(fetcher(url.strip()))
    article = parse_lesson(state, lesson_id)
    quiz = []
    for page in article.pop("_exercise_pages"):
        try:
            quiz += parse_exercise(apollo_state(fetcher(page["url"])))
        except ImportError_:
            continue
        if len(quiz) >= MAX_QUESTIONS:
            break
    article["quiz"] = [{"id": f"q{i + 1}", **q} for i, q in enumerate(quiz[:MAX_QUESTIONS])]
    return article
