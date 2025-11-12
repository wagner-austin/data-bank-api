from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_bank_api.app import create_app
from data_bank_api.config import Settings


def _client(tmp_path: Path, settings: Settings | None = None) -> TestClient:
    root = tmp_path / "files"
    s = settings or Settings(data_root=str(root), min_free_gb=0)
    return TestClient(create_app(s))


def test_auth_enforced_upload_401_403_200(tmp_path: Path) -> None:
    s = Settings(
        data_root=str(tmp_path / "files"),
        min_free_gb=0,
        api_upload_keys=frozenset({"k1"}),
    )
    client = _client(tmp_path, s)

    # Missing key -> 401
    r1 = client.post(
        "/files",
        files={"file": ("abcd1234", io.BytesIO(b"hi"), "text/plain")},
    )
    assert r1.status_code == 401
    b1: dict[str, object] = json.loads(r1.text)
    assert b1["code"] == "UNAUTHORIZED"

    # Wrong key -> 403
    r2 = client.post(
        "/files",
        files={"file": ("abcd1234", io.BytesIO(b"hi"), "text/plain")},
        headers={"X-API-Key": "wrong"},
    )
    assert r2.status_code == 403

    # Correct key -> 201
    r3 = client.post(
        "/files",
        files={"file": ("abcd1234", io.BytesIO(b"hi"), "text/plain")},
        headers={"X-API-Key": "k1"},
    )
    assert r3.status_code == 201
    # also verify HEAD works with read key when configured
    s2 = Settings(
        data_root=str(tmp_path / "files2"),
        min_free_gb=0,
        api_read_keys=frozenset({"r1"}),
    )
    client2 = _client(tmp_path, s2)
    # HEAD without key -> 401
    assert client2.head("/files/deadbeef").status_code == 401
    # with wrong key -> 403
    assert client2.head("/files/deadbeef", headers={"X-API-Key": "bad"}).status_code == 403
    # with correct key -> 404 (resource missing), but auth passed
    assert client2.head("/files/deadbeef", headers={"X-API-Key": "r1"}).status_code == 404


def test_range_errors_and_headers(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = b"hello world" * 3
    r0 = client.post(
        "/files",
        files={"file": ("anyname.txt", io.BytesIO(payload), "application/octet-stream")},
    )
    assert r0.status_code in (200, 201)
    body0: dict[str, object] = json.loads(r0.text)
    fid = str(body0.get("file_id", ""))
    assert fid != ""

    # invalid prefix
    r1 = client.get(f"/files/{fid}", headers={"Range": "bad=0-10"})
    assert r1.status_code == 416
    j1: dict[str, object] = json.loads(r1.text)
    assert j1["code"] == "INVALID_RANGE"

    # multiple ranges
    r2 = client.get(f"/files/{fid}", headers={"Range": "bytes=0-1,2-3"})
    assert r2.status_code == 416

    # non-numeric
    r3 = client.get(f"/files/{fid}", headers={"Range": "bytes=abc-"})
    assert r3.status_code == 416

    # unsatisfiable
    r4 = client.get(f"/files/{fid}", headers={"Range": "bytes=999999-"})
    assert r4.status_code == 416
    assert r4.headers["Content-Range"].startswith("bytes */")

    # ETag on HEAD and GET
    h = client.head(f"/files/{fid}")
    assert h.status_code == 200
    etag: str = h.headers["ETag"]
    g = client.get(f"/files/{fid}")
    assert g.status_code == 200
    assert g.headers["ETag"] == etag


def test_upload_507_from_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force guard to raise at upload path
    client = _client(tmp_path)
    from data_bank_api.storage import InsufficientStorageError, Storage

    def _boom(self: Storage) -> None:
        raise InsufficientStorageError("x")

    monkeypatch.setattr(Storage, "_ensure_free_space", _boom)
    r = client.post(
        "/files",
        files={"file": ("abcd1234", io.BytesIO(b"data"), "text/plain")},
    )
    assert r.status_code == 507


def test_upload_413_payload_too_large(tmp_path: Path) -> None:
    # Configure max size to 1 byte and upload 2 bytes to trigger 413
    s = Settings(
        data_root=str(tmp_path / "files"),
        min_free_gb=0,
        delete_strict_404=False,
        max_file_bytes=1,
    )
    client = _client(tmp_path, s)
    resp = client.post(
        "/files",
        files={"file": ("x.txt", io.BytesIO(b"dd"), "text/plain")},
    )
    assert resp.status_code == 413


def test_delete_strict_404(tmp_path: Path) -> None:
    s = Settings(
        data_root=str(tmp_path / "files"),
        min_free_gb=0,
        delete_strict_404=True,
    )
    client = _client(tmp_path, s)
    r = client.delete("/files/deadbeef")
    assert r.status_code == 404


def test_download_missing_file_full_and_range(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # full GET missing
    r1 = client.get("/files/deadbeef")
    assert r1.status_code == 404
    # range GET missing
    r2 = client.get("/files/deadbeef", headers={"Range": "bytes=0-10"})
    assert r2.status_code == 404


def test_unsatisfiable_range_with_disappearing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path)
    fid = "a1b2c3d4"
    # create a small file
    _ = client.post(
        "/files",
        files={"file": (fid, io.BytesIO(b"hello"), "application/octet-stream")},
    )

    # Simulate file disappearing when computing size after unsatisfiable detection
    from data_bank_api.storage import Storage, StoredFileNotFoundError

    def _raise_get_size(self: Storage, _file_id: str) -> int:
        raise StoredFileNotFoundError("gone")

    monkeypatch.setattr(Storage, "get_size", _raise_get_size)
    r = client.get(f"/files/{fid}", headers={"Range": "bytes=999999-"})
    assert r.status_code == 404


def test_read_auth_enforced_for_head_get_info_delete(tmp_path: Path) -> None:
    s = Settings(
        data_root=str(tmp_path / "files"),
        min_free_gb=0,
        api_read_keys=frozenset({"rk"}),
        api_delete_keys=frozenset({"dk"}),
    )
    client = _client(tmp_path, s)

    assert client.head("/files/deadbeef").status_code == 401
    assert client.get("/files/deadbeef").status_code == 401
    assert client.get("/files/deadbeef/info").status_code == 401
    assert client.delete("/files/deadbeef").status_code == 401


def test_info_404_on_missing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/files/deadbeef/info").status_code == 404
