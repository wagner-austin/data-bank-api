from __future__ import annotations

import io
import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from data_bank_api.app import create_app
from data_bank_api.config import Settings


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

    # info
    r5 = client.get(f"/files/{fid}/info")
    assert r5.status_code == 200
    b5: dict[str, object] = json.loads(r5.text)
    size_val = b5.get("size")
    assert isinstance(size_val, int)
    assert size_val == len(payload)

    # delete
    r6 = client.delete(f"/files/{fid}")
    assert r6.status_code == 204
    # idempotent
    r7 = client.delete(f"/files/{fid}")
    assert r7.status_code == 204
