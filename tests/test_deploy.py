"""Deployment files that must agree with each other."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_vercel_dependencies_match_requirements():
    reqs = [
        line.strip()
        for line in (ROOT / "backend/requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert sorted(project["dependencies"]) == sorted(reqs)


def test_vercel_entrypoint_exists():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["vercel"]
    module, attr = cfg["entrypoint"].split(":")
    assert (ROOT / (module.replace(".", "/") + ".py")).exists() and attr == "app"


def test_runtime_files_are_not_ignored():
    ignored = {l.strip().rstrip("/") for l in (ROOT / ".vercelignore").read_text().splitlines() if l.strip() and not l.startswith("#")}
    for needed in ("backend", "frontend", "pyproject.toml"):
        assert needed not in ignored
