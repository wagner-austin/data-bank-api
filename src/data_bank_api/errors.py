from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiErrorBody:
    code: str
    message: str
    request_id: str | None


def error_body(code: str, message: str, request_id: str | None) -> dict[str, str | None]:
    return {"code": code, "message": message, "request_id": request_id}
