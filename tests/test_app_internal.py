from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Never

from _pytest.monkeypatch import MonkeyPatch

from data_bank_api.app import _is_writable, _request_id


def test__is_writable_handles_oserror(monkeypatch: MonkeyPatch) -> None:
    def _raise(*args: tuple[object, ...], **kwargs: dict[str, object]) -> Never:
        raise OSError("denied")

    monkeypatch.setattr(tempfile, "mkstemp", _raise)
    ok = _is_writable(Path(tempfile.gettempdir()) / "nope")
    assert ok is False


def test__request_id_handles_none() -> None:
    assert _request_id(None) is None
