import importlib
import os

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import backend.config
    import backend.main

    importlib.reload(backend.config)
    importlib.reload(backend.main)
    with TestClient(backend.main.app) as c:
        yield c


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
    with client.websocket_connect("/api/admin/ws") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        with pytest.raises(Exception):
            ws.receive_json()
