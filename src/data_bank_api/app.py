from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Response, status

from .config import Settings
from .logging import setup_logging


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
    usage = os.statvfs(path) if hasattr(os, "statvfs") else None
    if usage is not None:
        free_bytes = usage.f_bavail * usage.f_frsize
    else:
        # Fallback for platforms without statvfs
        st = os.stat(path)
        # We cannot compute free space from os.stat; mark as unknown high value
        free_bytes = 1 << 40
    return free_bytes / (1024 ** 3)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    setup_logging("INFO")
    app = FastAPI(title="data-bank-api", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(resp: Response) -> dict[str, str]:
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

    return app

