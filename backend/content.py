import json
from functools import lru_cache

from . import db
from .config import CONTENT_DIR


def _word_count(text: str) -> int:
    return len(text.split())


def normalize(data: dict) -> dict:
    """Fill derived fields and bring quiz answers to the list form."""
    data = dict(data)
    data.setdefault("lead", "")
    data.setdefault("glossary", [])
    data.setdefault("source", None)
    quiz = []
    for q in data.get("quiz", []):
        answer = q["answer"] if isinstance(q["answer"], list) else [q["answer"]]
        quiz.append({**q, "answer": answer, "multiple": bool(q.get("multiple")) or len(answer) > 1})
    data["quiz"] = quiz
    data["words"] = _word_count(data["title"]) + _word_count(data["lead"]) + sum(_word_count(p) for p in data["paragraphs"])
    return data


@lru_cache(maxsize=None)
def builtin_articles() -> dict[str, dict]:
    out = {}
    for path in sorted(CONTENT_DIR.glob("*.json")):
        data = normalize(json.loads(path.read_text(encoding="utf-8")))
        out[data["id"]] = data
    return out


def load_articles() -> dict[str, dict]:
    articles = dict(builtin_articles())
    for a in db.stored_articles():
        articles[a["id"]] = normalize(a)
    return articles


def get_article(article_id: str) -> dict | None:
    return load_articles().get(article_id)


def summary(a: dict) -> dict:
    return {
        "id": a["id"], "title": a["title"], "level": a["level"], "words": a["words"],
        "questions": len(a["quiz"]), "source": a.get("source"), "builtin": a["id"] in builtin_articles(),
        "imported_at": a.get("imported_at"),
    }


def public_article(article: dict) -> dict:
    """Article as sent to students: quiz answers stripped."""
    return {
        "id": article["id"],
        "level": article["level"],
        "title": article["title"],
        "lead": article["lead"],
        "paragraphs": article["paragraphs"],
        "words": article["words"],
        "source": article.get("source"),
        "glossary": article.get("glossary", []),
        "quiz": [
            {"id": q["id"], "question": q["question"], "options": q["options"], "multiple": q["multiple"]}
            for q in article["quiz"]
        ],
    }


def grade_quiz(article: dict, answers: dict[str, list[int]]) -> tuple[int, list[dict]]:
    """A question counts as correct only when the chosen set equals the answer set."""
    feedback = []
    correct = 0
    for q in article["quiz"]:
        given = sorted(set(answers.get(q["id"]) or []))
        ok = given == sorted(q["answer"])
        correct += ok
        feedback.append({"id": q["id"], "given": given, "answer": q["answer"], "correct": ok})
    score = round(100 * correct / len(article["quiz"])) if article["quiz"] else 0
    return score, feedback
