from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .errors import error_body
from .logging import setup_logging
from .storage import (
    InsufficientStorageError,
    Storage,
    StorageError,
    StoredFileNotFoundError,
)


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="probe_", dir=str(path))
        os.close(fd)
        Path(tmp).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _free_gb(path: Path) -> float:
    usage = shutil.disk_usage(str(path))
    free_bytes = usage.free
    return free_bytes / (1024**3)


def _request_id(req: Request | None) -> str | None:
    if req is None:
        return None
    rid = req.headers.get("X-Request-ID")
    return rid if rid is not None and rid.strip() != "" else None


Permission = Literal["upload", "read", "delete"]


def _ensure_auth(cfg: Settings, perm: Permission, req: Request) -> JSONResponse | None:
    allowed = (
        cfg.api_upload_keys
        if perm == "upload"
        else cfg.api_read_keys
        if perm == "read"
        else cfg.api_delete_keys
    )
    # If no keys configured for this permission, auth is disabled.
    if len(allowed) == 0:
        return None
    key = req.headers.get("X-API-Key")
    if key is None or key.strip() == "":
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=error_body("UNAUTHORIZED", "missing API key", _request_id(req)),
        )
    if key not in allowed:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=error_body("FORBIDDEN", "invalid API key for permission", _request_id(req)),
        )
    return None


def _build_healthz_handler() -> Callable[[], dict[str, str]]:
    def handler() -> dict[str, str]:
        return {"status": "ok"}

    return handler


def _download_full(
    storage: Storage, file_id: str, request: Request
) -> StreamingResponse | JSONResponse:
    try:
        meta = storage.head(file_id)
    except StoredFileNotFoundError:
        return JSONResponse(
            status_code=404,
            content=error_body("NOT_FOUND", "file not found", _request_id(request)),
        )
    it, start, last = storage.open_range(file_id, 0, None)
    total = last - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(total),
        "ETag": meta.sha256,
    }
    return StreamingResponse(
        it,
        status_code=200,
        headers=headers,
        media_type=meta.content_type,
    )


def _download_range(
    storage: Storage, file_id: str, request: Request, range_header: str
) -> StreamingResponse | JSONResponse:
    if not range_header.startswith("bytes="):
        rid = _request_id(request)
        return JSONResponse(
            status_code=416,
            content=error_body("INVALID_RANGE", "invalid range", rid),
        )
    spec = range_header[len("bytes=") :]
    if "," in spec:
        rid = _request_id(request)
        return JSONResponse(
            status_code=416,
            content=error_body("INVALID_RANGE", "multiple ranges not supported", rid),
        )
    start_s, _, end_s = spec.partition("-")
    try:
        start = int(start_s) if start_s != "" else 0
        end = int(end_s) if end_s != "" else None
    except ValueError:
        rid = _request_id(request)
        return JSONResponse(
            status_code=416,
            content=error_body("INVALID_RANGE", "invalid range", rid),
        )
    try:
        it, start_pos, last_pos = storage.open_range(file_id, start, end)
    except StoredFileNotFoundError:
        return JSONResponse(
            status_code=404,
            content=error_body("NOT_FOUND", "file not found", _request_id(request)),
        )
    except StorageError:
        try:
            total_size = storage.get_size(file_id)
        except StoredFileNotFoundError:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", "file not found", _request_id(request)),
            )
        headers = {"Content-Range": f"bytes */{total_size}"}
        rid = _request_id(request)
        return JSONResponse(
            status_code=416,
            content=error_body("RANGE_NOT_SATISFIABLE", "unsatisfiable range", rid),
            headers=headers,
        )
    total = last_pos - start_pos + 1
    total_size2 = storage.get_size(file_id)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(total),
        "Content-Range": f"bytes {start_pos}-{last_pos}/{total_size2}",
    }
    return StreamingResponse(
        it,
        status_code=206,
        headers=headers,
        media_type="application/octet-stream",
    )


def _build_readyz_handler(cfg: Settings) -> Callable[[Response], dict[str, str]]:
    def handler(resp: Response) -> dict[str, str]:
        root = Path(cfg.data_root)
        if not root.exists() and not _is_writable(root):
            resp.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "degraded", "reason": "storage not writable"}
        if not _is_writable(root):
            resp.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "degraded", "reason": "storage not writable"}
        free = _free_gb(root)
        if free < float(cfg.min_free_gb):
            resp.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "degraded", "reason": "low disk"}
        return {"status": "ready"}

    return handler


def _build_upload_handler(
    storage: Storage, cfg: Settings
) -> Callable[[Annotated[UploadFile, File(...)], Request], Response | dict[str, object]]:
    def handler(
        file: Annotated[UploadFile, File(...)],
        request: Request,
    ) -> Response | dict[str, object]:
        auth = _ensure_auth(cfg, "upload", request)
        if auth is not None:
            return auth
        try:
            ct = file.content_type or "application/octet-stream"
            name = file.filename or ""
            meta = storage.save_stream(name, file.file, ct)
            return {
                "file_id": meta.file_id,
                "size": meta.size_bytes,
                "sha256": meta.sha256,
                "content_type": meta.content_type,
                "created_at": None,
            }
        except InsufficientStorageError:
            return JSONResponse(
                status_code=507,
                content=error_body(
                    "INSUFFICIENT_STORAGE", "insufficient storage", _request_id(request)
                ),
            )
        except StorageError as err:
            return JSONResponse(
                status_code=400,
                content=error_body("BAD_REQUEST", str(err), _request_id(request)),
            )

    return handler


def _build_head_handler(storage: Storage, cfg: Settings) -> Callable[[str, Request], Response]:
    def handler(file_id: str, request: Request) -> Response:
        auth = _ensure_auth(cfg, "read", request)
        if auth is not None:
            return auth
        try:
            meta = storage.head(file_id)
        except StoredFileNotFoundError:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", "file not found", _request_id(request)),
            )
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(meta.size_bytes),
            "ETag": meta.sha256,
            "Content-Type": meta.content_type,
        }
        return Response(status_code=200, headers=headers)

    return handler


def _build_download_handler(storage: Storage, cfg: Settings) -> Callable[[str, Request], Response]:
    def handler(file_id: str, request: Request) -> Response:
        auth = _ensure_auth(cfg, "read", request)
        if auth is not None:
            return auth

        range_header = request.headers.get("Range")
        if range_header is None:
            return _download_full(storage, file_id, request)

        return _download_range(storage, file_id, request, range_header)

    return handler


def _build_info_handler(
    storage: Storage, cfg: Settings
) -> Callable[[str, Request], Response | dict[str, object]]:
    def handler(file_id: str, request: Request) -> Response | dict[str, object]:
        auth = _ensure_auth(cfg, "read", request)
        if auth is not None:
            return auth
        try:
            meta = storage.head(file_id)
        except StoredFileNotFoundError:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", "file not found", _request_id(request)),
            )
        return {
            "file_id": meta.file_id,
            "size": meta.size_bytes,
            "sha256": meta.sha256,
            "content_type": meta.content_type,
        }

    return handler


def _build_delete_handler(storage: Storage, cfg: Settings) -> Callable[[str, Request], Response]:
    def handler(file_id: str, request: Request) -> Response:
        auth = _ensure_auth(cfg, "delete", request)
        if auth is not None:
            return auth
        deleted = storage.delete(file_id)
        if not deleted and cfg.delete_strict_404:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", "file not found", _request_id(request)),
            )
        return Response(status_code=204)

    return handler


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    setup_logging("INFO")
    app = FastAPI(title="data-bank-api", version="0.1.0")
    storage = Storage(root=Path(cfg.data_root), min_free_gb=cfg.min_free_gb)

    app.add_api_route("/healthz", _build_healthz_handler(), methods=["GET"], response_model=None)
    app.add_api_route("/readyz", _build_readyz_handler(cfg), methods=["GET"], response_model=None)
    app.add_api_route(
        "/files",
        _build_upload_handler(storage, cfg),
        methods=["POST"],
        status_code=status.HTTP_201_CREATED,
        response_model=None,
    )
    app.add_api_route(
        "/files/{file_id}",
        _build_head_handler(storage, cfg),
        methods=["HEAD"],
        response_model=None,
    )
    app.add_api_route(
        "/files/{file_id}",
        _build_download_handler(storage, cfg),
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route(
        "/files/{file_id}/info",
        _build_info_handler(storage, cfg),
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route(
        "/files/{file_id}",
        _build_delete_handler(storage, cfg),
        methods=["DELETE"],
        response_model=None,
    )

    return app
