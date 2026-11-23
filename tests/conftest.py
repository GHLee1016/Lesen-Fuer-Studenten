import importlib
import os

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """App with an empty database: a fresh SQLite file, or the Postgres
    database named by TEST_DATABASE_URL with every table dropped first."""
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    pg = os.environ.get("TEST_DATABASE_URL", "")
    if pg:
        monkeypatch.setenv("DATABASE_URL", pg)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    import backend.config
    import backend.db
    import backend.main

    importlib.reload(backend.config)
    if pg:
        backend.db.reset()
    importlib.reload(backend.main)
    with TestClient(backend.main.app) as c:
        yield c
