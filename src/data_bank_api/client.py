from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

import httpx


class DataBankClientError(Exception):
    pass


class AuthorizationError(DataBankClientError):
    pass


class ForbiddenError(DataBankClientError):
    pass


class NotFoundError(DataBankClientError):
    pass


class RangeNotSatisfiableError(DataBankClientError):
    pass


class BadRequestError(DataBankClientError):
    pass


class InsufficientStorageClientError(DataBankClientError):
    pass


@dataclass(frozen=True)
class HeadInfo:
    size: int
    etag: str
    content_type: str


@dataclass(frozen=True)
class UploadResult:
    file_id: str
    size: int
    sha256: str
    content_type: str
    created_at: str | None


class DataBankClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 60.0,
        retries: int = 3,
        backoff_seconds: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url: Final[str] = base_url.rstrip("/")
        self._api_key: Final[str] = api_key
        self._timeout: Final[float] = timeout_seconds
        self._retries: Final[int] = max(0, retries)
        self._backoff: Final[float] = max(0.0, backoff_seconds)
        self._client: Final[httpx.Client] = client or httpx.Client(timeout=self._timeout)

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers: dict[str, str] = {"X-API-Key": self._api_key}
        if request_id is not None and request_id.strip() != "":
            headers["X-Request-ID"] = request_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        files: dict[str, tuple[str, BinaryIO, str]] | None = None,
        idempotent: bool = True,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            try:
                resp = self._client.request(
                    method,
                    url,
                    headers=headers,
                    files=files,
                    timeout=self._timeout,
                )
                # Retry 5xx for idempotent methods
                if idempotent and 500 <= resp.status_code < 600 and attempt < self._retries:
                    attempt += 1
                    time.sleep(self._backoff * attempt)
                    continue
                return resp
            except httpx.TransportError as e:
                if not idempotent or attempt >= self._retries:
                    raise DataBankClientError(f"transport error: {e}") from e
                attempt += 1
                time.sleep(self._backoff * attempt)

    def _raise_for_error(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        msg = resp.text
        if resp.status_code == 401:
            raise AuthorizationError(msg)
        if resp.status_code == 403:
            raise ForbiddenError(msg)
        if resp.status_code == 404:
            raise NotFoundError(msg)
        if resp.status_code == 416:
            raise RangeNotSatisfiableError(msg)
        if resp.status_code == 400:
            raise BadRequestError(msg)
        if resp.status_code == 507:
            raise InsufficientStorageClientError(msg)
        raise DataBankClientError(f"HTTP {resp.status_code}: {msg}")

    def head(self, file_id: str, *, request_id: str | None = None) -> HeadInfo:
        resp = self._request("HEAD", f"/files/{file_id}", headers=self._headers(request_id))
        if resp.status_code >= 400:
            self._raise_for_error(resp)
        # Build typed headers map to avoid untyped values from headers.get
        headers_map: dict[str, str] = {k.lower(): v for (k, v) in resp.headers.items()}
        size = int(headers_map["content-length"]) if "content-length" in headers_map else 0
        etag = headers_map.get("etag", "")
        ctype = headers_map.get("content-type", "application/octet-stream")
        return HeadInfo(size=size, etag=etag, content_type=ctype)

    def info(self, file_id: str, *, request_id: str | None = None) -> dict[str, object]:
        head = self.head(file_id, request_id=request_id)
        return {
            "file_id": file_id,
            "size": head.size,
            "sha256": head.etag,
            "content_type": head.content_type,
        }

    def delete(self, file_id: str, *, request_id: str | None = None) -> None:
        resp = self._request("DELETE", f"/files/{file_id}", headers=self._headers(request_id))
        if resp.status_code >= 400:
            self._raise_for_error(resp)

    def upload(
        self,
        file_id: str,
        stream: BinaryIO,
        *,
        content_type: str = "application/octet-stream",
        request_id: str | None = None,
    ) -> UploadResult:
        files = {"file": (file_id, stream, content_type)}
        resp = self._request(
            "POST",
            "/files",
            headers=self._headers(request_id),
            files=files,
            idempotent=False,
        )
        if resp.status_code >= 400:
            self._raise_for_error(resp)
        head = self.head(file_id, request_id=request_id)
        return UploadResult(
            file_id=file_id,
            size=head.size,
            sha256=head.etag,
            content_type=head.content_type,
            created_at=None,
        )

    def download_to_path(
        self,
        file_id: str,
        dest: Path,
        *,
        resume: bool = True,
        request_id: str | None = None,
        verify_etag: bool = True,
        chunk_size: int = 1024 * 1024,
    ) -> HeadInfo:
        head = self.head(file_id, request_id=request_id)
        start = dest.stat().st_size if resume and dest.exists() else 0
        if start > head.size:
            raise RangeNotSatisfiableError("local file larger than remote")
        if start >= head.size:
            # Already complete, verify if requested
            if verify_etag:
                self._verify_file_etag(dest, head.etag)
            return head

        headers = self._headers(request_id)
        if start > 0:
            headers["Range"] = f"bytes={start}-"
        url = f"{self._base_url}/files/{file_id}"
        with self._client.stream("GET", url, headers=headers, timeout=self._timeout) as resp:
            if resp.status_code >= 400:
                self._raise_for_error(resp)
            if start > 0:
                with dest.open("r+b") as f:
                    f.seek(start)
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
            else:
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        if verify_etag:
            self._verify_file_etag(dest, head.etag)
        return head

    @staticmethod
    def _verify_file_etag(path: Path, expected_etag: str) -> None:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for data in iter(lambda: f.read(1024 * 1024), b""):
                h.update(data)
        if h.hexdigest() != expected_etag:
            raise DataBankClientError("downloaded file hash does not match ETag")
