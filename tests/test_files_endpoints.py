from __future__ import annotations

import io
import json
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_bank_api.app import create_app
from data_bank_api.config import Settings
from data_bank_api.storage import Storage, StorageError


def _client(tmp_path: Path) -> TestClient:
    root = tmp_path / "files"
    s = Settings(data_root=str(root), min_free_gb=0)
    return TestClient(create_app(s))


def test_upload_head_get_delete_roundtrip(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = b"hello world" * 1000
    _ = sha256(payload).hexdigest()

    # upload
    resp = client.post(
        "/files",
        files={"file": ("abcd1234", io.BytesIO(payload), "text/plain")},
    )
    # fastapi may return 200 or 201 depending on model; accept either
    assert resp.status_code in (200, 201)
    body: dict[str, object] = json.loads(resp.text)
    fid = str(body["file_id"]) if "file_id" in body else "abcd1234"

    # head
    r2 = client.head(f"/files/{fid}")
    assert r2.status_code == 200
    assert r2.headers["Content-Length"] == str(len(payload))

    # get full
    r3 = client.get(f"/files/{fid}")
    assert r3.status_code == 200
    assert r3.content == payload

    # get range
    r4 = client.get(f"/files/{fid}", headers={"Range": "bytes=5-15"})
    assert r4.status_code == 206
    assert r4.content == payload[5:16]
    # headers include ETag and Content-Type on partial content
    headers_map: dict[str, str] = {str(k).lower(): str(v) for (k, v) in r4.headers.items()}
    assert "etag" in headers_map
    ctype = headers_map.get("content-type", "")
    assert ctype.startswith("text/plain")

    # info
    r5 = client.get(f"/files/{fid}/info")
    assert r5.status_code == 200
    b5: dict[str, object] = json.loads(r5.text)
    size_val = b5.get("size")
    assert isinstance(size_val, int)
    assert size_val == len(payload)
    # info includes created_at
    assert "created_at" in b5

    # delete
    r6 = client.delete(f"/files/{fid}")
    assert r6.status_code == 204
    # idempotent
    r7 = client.delete(f"/files/{fid}")
    assert r7.status_code == 204


def test_upload_400_bad_request_on_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Monkeypatch Storage.save_stream to raise StorageError
    client = _client(tmp_path)

    def _boom(self: Storage, stream: object, content_type: str) -> object:
        raise StorageError("boom")

    monkeypatch.setattr(Storage, "save_stream", _boom)
    resp = client.post(
        "/files",
        files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert resp.status_code == 400
    body: dict[str, object] = json.loads(resp.text)
    assert body.get("code") == "BAD_REQUEST"
