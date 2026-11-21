import json
from functools import lru_cache

from .config import CONTENT_DIR


def _word_count(text: str) -> int:
    return len(text.split())


@lru_cache(maxsize=None)
def load_articles() -> dict[str, dict]:
    articles = {}
    for path in sorted(CONTENT_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["words"] = _word_count(data["title"]) + _word_count(data.get("lead", "")) + sum(
            _word_count(p) for p in data["paragraphs"]
        )
        articles[data["id"]] = data
    return articles


def public_article(article: dict) -> dict:
    """Article as sent to students: quiz answers stripped."""
    return {
        "id": article["id"],
        "level": article["level"],
        "title": article["title"],
        "lead": article.get("lead", ""),
        "paragraphs": article["paragraphs"],
        "words": article["words"],
        "quiz": [{"id": q["id"], "question": q["question"], "options": q["options"]} for q in article["quiz"]],
    }


def grade_quiz(article: dict, answers: dict[str, int]) -> tuple[int, list[dict]]:
    feedback = []
    correct = 0
    for q in article["quiz"]:
        given = answers.get(q["id"])
        ok = given == q["answer"]
        correct += ok
        feedback.append({"id": q["id"], "given": given, "answer": q["answer"], "correct": ok})
    score = round(100 * correct / len(article["quiz"])) if article["quiz"] else 0
    return score, feedback
