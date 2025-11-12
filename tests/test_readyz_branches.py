from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from data_bank_api.app import create_app
from data_bank_api.config import Settings


def _client(tmp_path: Path, min_free_gb: int = 1) -> TestClient:
    root = tmp_path / "files"
    s = Settings(data_root=str(root), min_free_gb=min_free_gb)
    return TestClient(create_app(s))


def test_readyz_degraded_when_missing_and_not_writable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = _client(tmp_path)

    def _always_false(path: Path) -> bool:
        return False

    monkeypatch.setattr("data_bank_api.app._is_writable", _always_false)
    r = client.get("/readyz")
    assert r.status_code == 503

    assert "storage not writable" in r.text


def test_readyz_degraded_when_exists_but_not_writable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = _client(tmp_path)

    def _always_false(path: Path) -> bool:
        return False

    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("data_bank_api.app._is_writable", _always_false)
    r = client.get("/readyz")
    assert r.status_code == 503

    assert "storage not writable" in r.text


def test_readyz_degraded_when_low_disk(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = _client(tmp_path, min_free_gb=10)

    def _fake_free(_: Path) -> float:
        return 0.1

    monkeypatch.setattr("data_bank_api.app._free_gb", _fake_free)
    r = client.get("/readyz")
    assert r.status_code == 503

    assert "low disk" in r.text
