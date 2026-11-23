import pytest

from tests.conftest import TOKEN

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def session_body(user="kim", answers=None, n=300, y=40):
    samples = [[i * 33.3, 120 + (i % 10), y + (i % 5)] for i in range(n)]
    return {
        "user_id": user,
        "article_id": "schwarze-katze",
        "mode": "mouse",
        "started_at": 1_700_000_000_000,
        "duration_ms": n * 33.3,
        "calibration_error_px": None,
        "layout": {"width": 800, "height": 1600, "line_height": 30},
        "aois": [{"id": "p0", "x": 0, "y": 0, "w": 800, "h": 120, "words": 40}],
        "samples": samples,
        "answers": answers if answers is not None else {"q1": 1, "q2": 1, "q3": 2, "q4": 2, "q5": 1},
    }


def test_pages_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200


def test_article_hides_answers(client):
    a = client.get("/api/articles/schwarze-katze").json()
    assert a["paragraphs"] and a["quiz"]
    assert all("answer" not in q for q in a["quiz"])
    assert client.get("/api/articles/nope").status_code == 404


def test_submit_scores_and_grades(client):
    r = client.post("/api/sessions", json=session_body())
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["quiz_score"] == 100
    assert 0 <= res["gaze_score"] <= 100
    assert res["final_score"] == round(res["gaze_score"] * 0.6 + 40)
    assert all(f["correct"] for f in res["quiz_feedback"])


def test_invalid_user_id_rejected(client):
    assert client.post("/api/sessions", json=session_body(user="<script>")).status_code == 422


def test_admin_requires_token(client):
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/overview", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/admin/export.csv").status_code == 401


def test_admin_overview_groups_by_latest_session(client):
    client.post("/api/sessions", json=session_body(user="lee", answers={}))
    client.post("/api/sessions", json=session_body(user="lee"))
    client.post("/api/sessions", json=session_body(user="park", answers={}, y=900))  # off text, no answers
    ov = client.get("/api/admin/overview", headers=AUTH).json()
    users = {u["user_id"]: (lv, u) for lv, us in ov["groups"].items() for u in us}
    assert users["lee"][1]["session_count"] == 2
    assert users["lee"][1]["quiz_score"] == 100  # latest one counts
    assert users["park"][0] == "low"


def test_levels_update_and_validation(client):
    assert client.put("/api/admin/levels", json={"high": 30, "low": 50}, headers=AUTH).status_code == 400
    assert client.put("/api/admin/levels", json={"high": 90, "low": 10}, headers=AUTH).status_code == 200
    assert client.get("/api/admin/overview", headers=AUTH).json()["levels"] == {"high": 90, "low": 10}


def test_user_detail_session_and_delete(client):
    sid = client.post("/api/sessions", json=session_body(user="choi")).json()["session_id"]
    detail = client.get("/api/admin/users/choi", headers=AUTH).json()
    assert detail["sessions"][0]["id"] == sid
    full = client.get(f"/api/admin/sessions/{sid}", headers=AUTH).json()
    assert len(full["samples"]) == 300 and full["layout"]["aois"]
    assert client.delete("/api/admin/users/choi", headers=AUTH).json() == {"deleted_sessions": 1}
    assert client.get("/api/admin/users/choi", headers=AUTH).status_code == 404


def test_exports(client):
    client.post("/api/sessions", json=session_body())
    csv_text = client.get("/api/admin/export.csv", headers=AUTH).text
    assert csv_text.splitlines()[0].startswith("id,user_id")
    assert len(csv_text.strip().splitlines()) == 2
    assert len(client.get("/api/admin/export.json", headers=AUTH).json()["sessions"]) == 1


def test_admin_ws_auth(client):
    with client.websocket_connect("/api/admin/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        assert ws.receive_json()["type"] == "overview"
        # periodic refresh (sessions saved on another server instance)
        ws.send_text("refresh")
        assert ws.receive_json()["type"] == "overview"
    with client.websocket_connect("/api/admin/ws") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        with pytest.raises(Exception):
            ws.receive_json()


# ------------------------------------------------------------ phase 2 ----

from backend import translate  # noqa: E402


@pytest.fixture()
def fake_translator(monkeypatch):
    calls = []

    def fake(word, lang):
        calls.append(word)
        return {"lemma": word.lower(), "article": "", "pos": "", "translation": f"<{word}>", "note": "", "source": "mymemory"}

    monkeypatch.setattr(translate, "gloss_mymemory", fake)
    monkeypatch.setattr(translate, "claude_available", lambda: False)
    return calls


def test_lookup_translates_caches_and_builds_vocab(client, fake_translator):
    body = {"user_id": "kim", "article_id": "schwarze-katze", "word": "Katzen,", "sentence": "Schwarze Katzen.", "lang": "ko"}
    r = client.post("/api/lookup", json=body).json()
    assert r["word"] == "Katzen" and r["translation"] == "<Katzen>" and r["lookups"] == 1
    r2 = client.post("/api/lookup", json=body).json()
    assert r2["lookups"] == 2
    assert fake_translator == ["Katzen"]  # second call served from cache
    items = client.get("/api/vocab", params={"user_id": "kim"}).json()["items"]
    assert len(items) == 1 and items[0]["lookups"] == 2
    csv_text = client.get("/api/vocab.csv", params={"user_id": "kim"}).text
    assert "katzen" in csv_text and "Schwarze Katzen." in csv_text
    assert client.delete(f"/api/vocab/{items[0]['id']}", params={"user_id": "someone-else"}).status_code == 404
    assert client.delete(f"/api/vocab/{items[0]['id']}", params={"user_id": "kim"}).status_code == 200
    assert client.get("/api/vocab", params={"user_id": "kim"}).json()["items"] == []


def test_lookup_rejects_unknown_language(client, fake_translator):
    body = {"user_id": "kim", "article_id": "schwarze-katze", "word": "Katze", "lang": "xx"}
    assert client.post("/api/lookup", json=body).status_code == 400


def test_session_with_words_reports_difficulty_and_marks(client):
    body = session_body(user="han")
    # one sentence of 4 words under the gaze, line height 30
    body["words"] = [[100 + k * 20, 35, 18, 20, 0] for k in range(4)]
    body["lookups"] = [{"t": 1200, "i": 2, "word": "Katze"}]
    r = client.post("/api/sessions", json=body).json()
    assert "difficulty" in r["metrics"] and r["metrics"]["difficulty"]["word_hits"] > 0
    sid = r["session_id"]
    assert client.put(f"/api/sessions/{sid}/marks", json={"sentences": [0, 0]}).json() == {"ok": True}
    full = client.get(f"/api/admin/sessions/{sid}", headers=AUTH).json()
    assert full["marks"] == [0]
    assert full["lookups"][0]["word"] == "Katze"
    assert len(full["layout"]["words"]) == 4
    assert client.put("/api/sessions/nope/marks", json={"sentences": []}).status_code == 404


def test_multiple_answer_questions_need_the_exact_set(client, monkeypatch):
    from backend import content

    art = {
        "id": "multi", "level": "B1", "title": "T", "paragraphs": ["Ein Satz."],
        "quiz": [{"id": "q1", "question": "?", "options": ["a", "b", "c"], "answer": [0, 2]}],
    }
    from backend import db as dbm
    dbm.save_article(art)
    pub = client.get("/api/articles/multi").json()
    assert pub["quiz"][0]["multiple"] is True and "answer" not in pub["quiz"][0]
    _, fb = content.grade_quiz(content.get_article("multi"), {"q1": [2, 0]})
    assert fb[0]["correct"]
    _, fb = content.grade_quiz(content.get_article("multi"), {"q1": [0]})
    assert not fb[0]["correct"]


def test_article_without_quiz_uses_gaze_score_only(client):
    from backend import db as dbm
    dbm.save_article({"id": "noquiz", "level": "B2", "title": "T", "paragraphs": ["Text hier."], "quiz": []})
    body = session_body(user="yoon")
    body["article_id"] = "noquiz"
    r = client.post("/api/sessions", json=body).json()
    assert r["final_score"] == r["gaze_score"] and r["has_quiz"] is False


def test_admin_import_list_delete(client, monkeypatch):
    from backend import dw
    from tests.test_dw import LESSON_URL, fake_fetch

    monkeypatch.setattr(dw, "fetch", fake_fetch)
    assert client.post("/api/admin/articles/import", json={"url": LESSON_URL}).status_code == 401
    r = client.post("/api/admin/articles/import", json={"url": LESSON_URL}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "dw-111" and r.json()["questions"] == 1
    listed = [a["id"] for a in client.get("/api/articles").json()]
    assert "dw-111" in listed and "schwarze-katze" in listed
    bad = client.post("/api/admin/articles/import", json={"url": "https://example.com/x"}, headers=AUTH)
    assert bad.status_code == 400
    assert client.delete("/api/admin/articles/schwarze-katze", headers=AUTH).status_code == 404  # built-in
    assert client.delete("/api/admin/articles/dw-111", headers=AUTH).status_code == 200


def test_mymemory_junk_is_filtered():
    data = {"responseStatus": 200, "responseData": {"translatedText": "MAN", "match": 0.98},
            "matches": [{"translation": "''not used''", "match": 0.9}, {"translation": "사람들", "match": 0.8}]}
    assert translate.pick_mymemory("man", "ko", data) == "사람들"
    assert translate.pick_mymemory("man", "ko", {"responseData": {"translatedText": "MAN"}, "matches": []}) is None


def test_reanalyze_endpoint(client):
    body = session_body(user="re")
    body["calibration_error_px"] = 200
    body["calibration_jitter_px"] = 60
    body["mode"] = "webcam"
    client.post("/api/sessions", json=body)
    assert client.post("/api/admin/reanalyze").status_code == 401
    r = client.post("/api/admin/reanalyze", headers=AUTH)
    assert r.status_code == 200 and r.json()["changed"] == []  # same params -> same scores
