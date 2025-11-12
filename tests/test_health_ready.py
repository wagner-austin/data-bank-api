from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from data_bank_api.app import create_app
from data_bank_api.config import Settings


def _with_tmp_root(tmp_path: Path) -> TestClient:
    root = tmp_path / "files"
    s = Settings(DATA_ROOT=str(root), MIN_FREE_GB=0)
    return TestClient(create_app(s))


def test_healthz_ok(tmp_path: Path) -> None:
    client = _with_tmp_root(tmp_path)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz_ready_when_writable(tmp_path: Path) -> None:
    client = _with_tmp_root(tmp_path)
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"

