"""DW importer against a synthetic page shaped like learngerman.dw.com.

The fixture text is made up; no DW content is stored in the repository.
"""

import json

import pytest

from backend import dw

LESSON_URL = "https://learngerman.dw.com/de/ein-test-artikel/l-111"
EXERCISE_PATH = "/de/was-steht-im-text/l-111/e-222"


def page(state: dict) -> str:
    return f"<html><body><script>window.__APOLLO_STATE__ = {json.dumps(state)};</script></body></html>"


LESSON_STATE = {
    "Navigation:1": {"__typename": "Navigation", "title": "Top-Thema mit Vokabeln (B1)"},
    "Knowledge:9": {"__typename": "Knowledge", "knowledgeType": "GLOSSARY", "id": 9, "name": "Igel, - (m.)", "text": "<p>ein kleines Tier mit Stacheln</p>"},
    "Lesson:111": {
        "__typename": "Lesson",
        "id": 111,
        "name": "Ein Test-Artikel",
        "teaser": "Ein kurzer Vorspann.",
        "dkLearningLevel": 8,
        "canonicalUrl": LESSON_URL,
        "navigations": [{"__ref": "Navigation:1"}],
        "manuscript": (
            "<p><strong>Ein Test-Artikel<br />\n<br />\nEin kurzer Vorspann.</strong><br />\n<br />\n"
            'Der <span data-id="9" data-type="GLOSSARY" title="Glosar">Igel</span> schläft im Winter.<br />\n<br />\n'
            "Im Frühling wacht er wieder auf &ndash; und hat Hunger.</p>\n<p>SPRECHER:<br />\nEin dritter Absatz.</p>"
        ),
        "overviewParts": [
            {"lessonPart": "EXERCISE", "target": {"name": "Was steht im Text?", 'namedUrl({"contextId":111})': EXERCISE_PATH}},
            {"lessonPart": "EXERCISE", "target": {"name": "Übe die Vokabeln!", 'namedUrl({"contextId":111})': "/de/x/l-111/e-333"}},
        ],
    },
}

EXERCISE_STATE = {
    "Alternative:1": {"__typename": "Alternative", "alternativeText": "Er schläft im Winter.", "isCorrect": True},
    "Alternative:2": {"__typename": "Alternative", "alternativeText": "Er fliegt nach Süden.", "isCorrect": False},
    "Alternative:3": {"__typename": "Alternative", "alternativeText": "Er hat im Frühling Hunger.", "isCorrect": True},
    "Inquiry:10": {"__typename": "Inquiry", "inquiryText": "", "alternatives": [{"__ref": "Alternative:1"}, {"__ref": "Alternative:2"}, {"__ref": "Alternative:3"}], "clozeType": None},
    "Inquiry:11": {"__typename": "Inquiry", "inquiryText": "Was macht der Igel?", "selectionType": "MULTIPLE", "subInquiries": [{"__ref": "Inquiry:10"}], "alternatives": []},
    "Exercise:222": {"__typename": "Exercise", "name": "Was steht im Text?", "inquiries": [{"__ref": "Inquiry:11"}]},
}


def fake_fetch(url):
    if url.endswith("e-222"):
        return page(EXERCISE_STATE)
    if url.endswith("e-333"):
        raise AssertionError("vocabulary exercises must not be fetched")
    return page(LESSON_STATE)


def test_import_parses_text_level_glossary_and_quiz():
    a = dw.import_lesson(LESSON_URL, fetcher=fake_fetch)
    assert a["id"] == "dw-111"
    assert a["level"] == "B1"
    assert a["title"] == "Ein Test-Artikel"
    assert a["lead"] == "Ein kurzer Vorspann."
    assert a["paragraphs"] == [
        "Der Igel schläft im Winter.",
        "Im Frühling wacht er wieder auf – und hat Hunger.",
        "Ein dritter Absatz.",  # speaker label removed
    ]
    assert a["glossary"] == [{"term": "Igel, - (m.)", "definition": "ein kleines Tier mit Stacheln", "surface": ["Igel"]}]
    assert a["source"]["url"] == LESSON_URL
    assert a["quiz"] == [{"id": "q1", "question": "Was macht der Igel?", "options": ["Er schläft im Winter.", "Er fliegt nach Süden.", "Er hat im Frühling Hunger."], "answer": [0, 2], "multiple": True}]


def test_level_falls_back_to_bitmask():
    state = json.loads(json.dumps(LESSON_STATE))
    state["Navigation:1"]["title"] = "Video-Thema"
    state["Lesson:111"]["dkLearningLevel"] = 16
    assert dw.cefr_level(state["Lesson:111"], state) == "B2"


@pytest.mark.parametrize("url", [
    "https://example.com/de/x/l-1",
    "https://learngerman.dw.com/de/top-thema/s-55861562",
    "ftp://learngerman.dw.com/de/x/l-1",
])
def test_rejects_non_lesson_urls(url):
    with pytest.raises(dw.ImportError_):
        dw.lesson_id_from_url(url)


def test_umlaut_urls_are_percent_encoded():
    assert dw._quote_url("https://learngerman.dw.com/de/über/l-1") == "https://learngerman.dw.com/de/%C3%BCber/l-1"
