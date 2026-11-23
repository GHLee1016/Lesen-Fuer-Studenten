import importlib

import pytest
from fastapi.testclient import TestClient

from backend.validation import _stats, wilson

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def row(auto, label, penalty=0):
    auto, label = set(auto), set(label)
    return {"auto": sorted(auto), "label": sorted(label), "penalty": penalty,
            "tp": len(auto & label), "fp": len(auto - label), "fn": len(label - auto)}


def test_wilson_interval():
    lo, hi = wilson(8, 10)
    assert 0.48 < lo < 0.5 and 0.94 < hi < 0.95
    assert wilson(0, 0) == (0.0, 1.0)


def test_stats_counts_and_false_alarms():
    s = _stats([row([1, 2], [2, 3]), row([4], [], penalty=6), row([], [])])
    assert (s["tp"], s["fp"], s["fn"]) == (1, 2, 1)
    assert s["precision"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["recall"] == 0.5
    assert s["no_report_sessions"] == 2
    assert s["no_report_flags"] == 0.5 and s["no_report_penalty"] == 3


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v.db"))
    import backend.config
    import backend.main

    importlib.reload(backend.config)
    importlib.reload(backend.main)
    with TestClient(backend.main.app) as c:
        yield c


def test_seeded_sessions_flow_through_validation_and_whatif(client):
    from tools.seed_demo import seed

    seed(6)
    rep = client.get("/api/admin/validation", headers=AUTH).json()
    assert rep["overall"]["sessions"] == 6
    assert {b["bucket"] for b in rep["by_bucket"]} <= {"≤60px", "60–100px", "100–150px", ">150px"}
    assert all(r["mode"] == "simulated" and r["user_id"].startswith("sim-") for r in rep["sessions"])
    wi = client.post("/api/admin/validation/whatif", headers=AUTH).json()
    assert wi["sessions"] == 6 and any(c["current"] for c in wi["candidates"])
    csv_text = client.get("/api/admin/validation.csv", headers=AUTH).text
    assert len(csv_text.strip().splitlines()) == 7
    assert client.get("/api/admin/validation").status_code == 401


def test_self_marks_endpoint_and_mouse_sessions_excluded(client):
    from tests.test_api import session_body

    sid = client.post("/api/sessions", json=session_body(user="kim")).json()["session_id"]  # mouse mode
    assert client.put(f"/api/sessions/{sid}/self-marks", json={"sentences": [3, 1, 3]}).json() == {"ok": True}
    full = client.get(f"/api/admin/sessions/{sid}", headers=AUTH).json()
    assert full["self_marks"] == [1, 3] and full["marks"] == [1, 3]
    assert client.get("/api/admin/validation", headers=AUTH).json()["overall"]["sessions"] == 0
    assert client.get("/api/admin/validation?include_mouse=true", headers=AUTH).json()["overall"]["sessions"] == 1
    assert client.put("/api/sessions/nope/self-marks", json={"sentences": []}).status_code == 404


def test_new_sessions_do_not_premark_automatic_flags(client):
    from tests.test_api import session_body

    r = client.post("/api/sessions", json=session_body(user="lee")).json()
    assert r["marks"] == []


def test_params_override_validation_and_effect(client):
    from backend import params

    info = client.get("/api/admin/params", headers=AUTH).json()
    assert any(f["key"] == "slow_factor" for f in info["fields"])
    bad = [{"nope": 1}, {"slow_factor": 99}, {"penalty_full_error_px": 200, "penalty_zero_error_px": 100}]
    for o in bad:
        assert client.put("/api/admin/params", json={"overrides": o}, headers=AUTH).status_code == 400
    r = client.put("/api/admin/params", json={"overrides": {"slow_factor": 2.4, "reread_returns": 2.0}}, headers=AUTH)
    assert r.status_code == 200 and r.json()["overrides"] == {"slow_factor": 2.4, "reread_returns": 2}
    assert params.current().slow_factor == 2.4 and params.current().reread_returns == 2
    assert client.put("/api/admin/params", json={"overrides": {}}, headers=AUTH).status_code == 200
    assert params.current().slow_factor == 2.0
