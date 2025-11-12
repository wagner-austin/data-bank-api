from __future__ import annotations

from data_bank_api.errors import error_body


def test_error_body_shape() -> None:
    body = error_body("X", "msg", "rid")
    assert body == {"code": "X", "message": "msg", "request_id": "rid"}
