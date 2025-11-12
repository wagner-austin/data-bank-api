from __future__ import annotations

import io
import json
import os
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from data_bank_api.client import (
    AuthorizationError,
    BadRequestError,
    DataBankClient,
    DataBankClientError,
    ForbiddenError,
    InsufficientStorageClientError,
    NotFoundError,
    RangeNotSatisfiableError,
)


class _MemStore:
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._ctype: dict[str, str] = {}

    def put(self, fid: str, data: bytes, ctype: str) -> None:
        self._files[fid] = data
        self._ctype[fid] = ctype

    def get(self, fid: str) -> tuple[bytes, str]:
        if fid not in self._files:
            raise KeyError(fid)
        return self._files[fid], self._ctype.get(fid, "application/octet-stream")

    def delete(self, fid: str) -> bool:
        if fid in self._files:
            del self._files[fid]
            self._ctype.pop(fid, None)
            return True
        return False

    def exists(self, fid: str) -> bool:
        return fid in self._files


class _MockServer:
    def __init__(self, store: _MemStore, expect_key: str) -> None:
        self._store = store
        self._key = expect_key
        self._counts: dict[str, int] = {}

    @staticmethod
    def _unauth() -> httpx.Response:
        body = {"code": "UNAUTHORIZED", "message": "missing/invalid", "request_id": None}
        return httpx.Response(401, text=json.dumps(body))

    @staticmethod
    def _not_found() -> httpx.Response:
        body = {"code": "NOT_FOUND", "message": "not found", "request_id": None}
        return httpx.Response(404, text=json.dumps(body))

    def _post_files(self, request: httpx.Request) -> httpx.Response:
        content = request.content
        hdrs = {k.lower(): v for (k, v) in request.headers.items()}
        ct_header = hdrs.get("content-type", "")
        # extract boundary token
        b_key = "boundary="
        bpos = ct_header.find(b_key)
        assert bpos != -1
        boundary = ct_header[bpos + len(b_key) :]
        boundary = boundary.strip('"')
        bbytes = ("--" + boundary).encode("latin-1")
        # find filename for fid
        txt = content.decode("latin-1", errors="ignore")
        idx = txt.find("filename=")
        assert idx != -1
        q1 = txt.find('"', idx)
        q2 = txt.find('"', q1 + 1)
        fid = txt[q1 + 1 : q2]
        # find payload start/end using boundary markers
        # locate the first boundary line, then the end of part headers
        bpos0 = content.find(bbytes)
        assert bpos0 != -1
        start = content.find(b"\r\n\r\n", bpos0)
        assert start != -1
        start += 4
        end = content.find(b"\r\n" + bbytes, start)
        assert end != -1
        data = content[start:end]
        # derive ctype from part headers
        ctype_line_start = txt.find("Content-Type:", q2)
        ctype = "application/octet-stream"
        if ctype_line_start != -1:
            ctype_line_end = txt.find("\r\n", ctype_line_start)
            ctype = txt[ctype_line_start:ctype_line_end].split(":", 1)[1].strip()
        if len(data) > 0 or not self._store.exists(fid):
            self._store.put(fid, data, ctype)
        body = {
            "file_id": fid,
            "size": len(data),
            "sha256": sha256(data).hexdigest(),
            "content_type": ctype,
            "created_at": None,
        }
        return httpx.Response(201, text=json.dumps(body))

    def _head_file(self, fid: str) -> httpx.Response:
        # special error ids
        if fid == "bad400":
            body = {"code": "BAD_REQUEST", "message": "bad", "request_id": None}
            return httpx.Response(400, text=json.dumps(body))
        if fid == "bad403":
            body = {"code": "FORBIDDEN", "message": "no", "request_id": None}
            return httpx.Response(403, text=json.dumps(body))
        if fid == "bad507":
            body = {"code": "INSUFFICIENT_STORAGE", "message": "low", "request_id": None}
            return httpx.Response(507, text=json.dumps(body))
        if fid == "err502":
            body = {"code": "ERROR", "message": "bad gateway", "request_id": None}
            return httpx.Response(502, text=json.dumps(body))
        if fid == "checkrid":
            # Ensure request-id header is surfaced; require it
            # header set in handle via hdrs dict
            headers = {
                "Content-Length": "0",
                "ETag": "",
                "Content-Type": "application/octet-stream",
            }
            return httpx.Response(200, headers=headers)
        if fid == "retryme":
            c = self._counts.get(fid, 0) + 1
            self._counts[fid] = c
            if c <= 2:
                body = {"code": "E", "message": "retry", "request_id": None}
                return httpx.Response(500, text=json.dumps(body))
        try:
            data, ctype = self._store.get(fid)
        except KeyError:
            return self._not_found()
        headers = {
            "Content-Length": str(len(data)),
            "ETag": sha256(data).hexdigest(),
            "Content-Type": ctype,
        }
        return httpx.Response(200, headers=headers)

    def _get_info(self, fid: str) -> httpx.Response:
        try:
            data, ctype = self._store.get(fid)
        except KeyError:
            return self._not_found()
        body = {
            "file_id": fid,
            "size": len(data),
            "sha256": sha256(data).hexdigest(),
            "content_type": ctype,
        }
        return httpx.Response(200, text=json.dumps(body))

    def _get_file(self, fid: str, rng: str | None) -> httpx.Response:
        if fid == "err416":
            body = {"code": "RANGE_NOT_SATISFIABLE", "message": "bad", "request_id": None}
            headers = {"Content-Range": "bytes */10"}
            return httpx.Response(416, text=json.dumps(body), headers=headers)
        try:
            data, ctype = self._store.get(fid)
        except KeyError:
            return self._not_found()
        if rng is None:
            headers = {"Content-Length": str(len(data)), "Content-Type": ctype}
            return httpx.Response(200, content=data, headers=headers)
        if not rng.startswith("bytes="):
            body = {"code": "INVALID_RANGE", "message": "invalid range", "request_id": None}
            return httpx.Response(416, text=json.dumps(body))
        start_s = rng[len("bytes=") :].split("-")[0]
        try:
            start = int(start_s) if start_s != "" else 0
        except ValueError:
            body = {"code": "INVALID_RANGE", "message": "invalid range", "request_id": None}
            return httpx.Response(416, text=json.dumps(body))
        if start >= len(data):
            headers = {"Content-Range": f"bytes */{len(data)}"}
            body = {"code": "RANGE_NOT_SATISFIABLE", "message": "unsat", "request_id": None}
            return httpx.Response(416, text=json.dumps(body), headers=headers)
        part = data[start:]
        headers = {
            "Content-Length": str(len(part)),
            "Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}",
        }
        return httpx.Response(206, content=part, headers=headers)

    def _delete(self, fid: str) -> httpx.Response:
        if not self._store.delete(fid):
            return self._not_found()
        return httpx.Response(204)

    def handle(self, request: httpx.Request) -> httpx.Response:
        hdrs: dict[str, str] = {k.lower(): v for (k, v) in request.headers.items()}
        hdr = hdrs.get("x-api-key")
        if hdr != self._key:
            return self._unauth()
        path = request.url.path
        if path == "/files" and request.method == "POST":
            return self._post_files(request)
        if request.method == "HEAD" and path.startswith("/files/"):
            fid = path.split("/")[-1]
            # require request id for checkrid
            if fid == "checkrid" and "x-request-id" not in hdrs:
                body = {"code": "E", "message": "missing rid", "request_id": None}
                return httpx.Response(500, text=json.dumps(body))
            return self._head_file(fid)
        if request.method == "GET" and path.endswith("/info"):
            return self._get_info(path.split("/")[-2])
        if request.method == "GET" and path.startswith("/files/"):
            rng_s = hdrs.get("range")
            return self._get_file(path.split("/")[-1], rng_s)
        if request.method == "DELETE" and path.startswith("/files/"):
            return self._delete(path.split("/")[-1])
        body = {"code": "ERROR", "message": "unhandled", "request_id": None}
        return httpx.Response(500, text=json.dumps(body))


def _mock_transport(store: _MemStore, expect_key: str) -> httpx.MockTransport:
    server = _MockServer(store, expect_key)
    return httpx.MockTransport(server.handle)


def _client_with_transport(transport: httpx.BaseTransport) -> DataBankClient:
    cx = httpx.Client(transport=transport)
    return DataBankClient("http://testserver", api_key="k", client=cx)


def test_client_upload_head_download(tmp_path: Path) -> None:
    store = _MemStore()
    client = _client_with_transport(_mock_transport(store, expect_key="k"))

    payload = b"hello" * 1000
    # Pre-populate to sidestep multipart parsing in mock
    store.put("abcd1234", payload, "text/plain")
    up = client.upload("abcd1234", io.BytesIO(payload), content_type="text/plain")
    assert up.size == len(payload)

    head = client.head("abcd1234")
    assert head.size == len(payload)
    assert head.etag == sha256(payload).hexdigest()

    dest = tmp_path / "file.bin"
    client.download_to_path("abcd1234", dest)
    assert dest.read_bytes() == payload


def test_client_resume_and_verify(tmp_path: Path) -> None:
    store = _MemStore()
    data = os.urandom(128 * 1024)
    store.put("deadbeef", data, "application/octet-stream")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))

    dest = tmp_path / "part.bin"
    # write first 10k
    dest.write_bytes(data[:10_000])
    head = client.download_to_path("deadbeef", dest, resume=True)
    assert head.size == len(data)
    assert dest.read_bytes() == data


def test_client_416_and_404_errors(tmp_path: Path) -> None:
    store = _MemStore()
    store.put("aa11bb22", b"x" * 10, "application/octet-stream")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))

    dest = tmp_path / "d.bin"
    dest.write_bytes(b"z" * 100)
    with pytest.raises(RangeNotSatisfiableError):
        client.download_to_path("aa11bb22", dest, resume=True)

    with pytest.raises(NotFoundError):
        client.head("missing")


def test_client_auth_errors() -> None:
    store = _MemStore()
    client = _client_with_transport(_mock_transport(store, expect_key="correct"))
    with pytest.raises(AuthorizationError):
        client.head("anything")


def test_client_retry_and_error_mappings(tmp_path: Path) -> None:
    store = _MemStore()
    # add file for retry path
    store.put("retryme", b"xx", "text/plain")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))
    head = client.head("retryme")
    assert head.size == 2

    # 400
    store.put("bad400", b"x", "text/plain")
    with pytest.raises(BadRequestError):
        client.head("bad400")
    # 403
    store.put("bad403", b"x", "text/plain")
    with pytest.raises(ForbiddenError):
        client.head("bad403")
    # 507
    store.put("bad507", b"x", "text/plain")
    with pytest.raises(InsufficientStorageClientError):
        client.head("bad507")
    # 5xx default mapping
    store.put("err502", b"x", "text/plain")
    with pytest.raises(DataBankClientError):
        client.head("err502")
    # request-id propagation
    client.head("checkrid", request_id="RID-1")


def test_download_stream_error_branch(tmp_path: Path) -> None:
    store = _MemStore()
    # HEAD ok, GET returns 416
    store.put("err416", b"abcd", "text/plain")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))
    with pytest.raises(RangeNotSatisfiableError):
        client.download_to_path("err416", tmp_path / "x.bin", resume=False)


def test_download_no_verify_branch(tmp_path: Path) -> None:
    store = _MemStore()
    data = b"m" * 64
    store.put("nv", data, "application/octet-stream")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))
    dest = tmp_path / "nv.bin"
    client.download_to_path("nv", dest, resume=False, verify_etag=False)
    assert dest.read_bytes() == data


def test_client_delete_and_info(tmp_path: Path) -> None:
    store = _MemStore()
    data = b"abc" * 10
    store.put("ff00aa11", data, "text/plain")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))

    info = client.info("ff00aa11")
    assert info["size"] == len(data)
    assert info["sha256"] == sha256(data).hexdigest()

    # delete existing
    client.delete("ff00aa11")
    # delete missing
    with pytest.raises(NotFoundError):
        client.delete("ff00aa11")

    # etag mismatch verification
    store.put("11223344", b"xyz", "application/octet-stream")
    dest = tmp_path / "mismatch.bin"
    client.download_to_path("11223344", dest)
    # corrupt local file
    dest.write_bytes(b"zzz")
    with pytest.raises(DataBankClientError):
        client.download_to_path("11223344", dest, resume=True, verify_etag=True)


def test_client_transport_retry_then_fail() -> None:
    # Handler raises TransportError; with small retry count, client should raise DataBankClientError
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - replaced by raise
        raise httpx.ConnectError("boom")

    client = DataBankClient(
        "http://x",
        api_key="k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retries=1,
        backoff_seconds=0.0,
    )
    with pytest.raises(DataBankClientError):
        client.head("any")


def test_client_upload_unauthorized() -> None:
    store = _MemStore()
    # Expect different key so upload should be 401
    client = _client_with_transport(_mock_transport(store, expect_key="other"))
    with pytest.raises(AuthorizationError):
        client.upload("fid", io.BytesIO(b"data"), content_type="text/plain")


def test_client_resume_already_complete(tmp_path: Path) -> None:
    store = _MemStore()
    data = b"z" * 1024
    store.put("c1", data, "application/octet-stream")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))
    dest = tmp_path / "c1.bin"
    # First download
    client.download_to_path("c1", dest)
    # Second call should short-circuit as already complete and verify ETag
    head2 = client.download_to_path("c1", dest, resume=True, verify_etag=True)
    assert head2.size == len(data)


def test_client_already_complete_no_verify(tmp_path: Path) -> None:
    store = _MemStore()
    data = b"q" * 256
    store.put("c2", data, "application/octet-stream")
    client = _client_with_transport(_mock_transport(store, expect_key="k"))
    dest = tmp_path / "c2.bin"
    client.download_to_path("c2", dest)
    # Call again with verify_etag disabled to exercise the false branch
    head = client.download_to_path("c2", dest, resume=True, verify_etag=False)
    assert head.size == len(data)


def test_raise_for_error_return_branch() -> None:
    # Ensure _raise_for_error returns on <400 without raising
    dummy = httpx.Response(200, text="ok")
    # Direct call to private for branch coverage
    DataBankClient("http://x", api_key="k")._raise_for_error(dummy)
