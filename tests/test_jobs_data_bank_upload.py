from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from api.config import Settings
from api.jobs import process_corpus_impl


class _FakeLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def info(self, msg: str, *, extra: dict[str, object] | None = None) -> None:
        self.records.append({"level": "info", "msg": msg, "extra": extra or {}})

    def error(self, msg: str, *, extra: dict[str, object] | None = None) -> None:
        self.records.append({"level": "error", "msg": msg, "extra": extra or {}})


class _Redis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    def hset(self, name: str, *, mapping: dict[str, str]) -> int:
        bucket = self._hashes.setdefault(name, {})
        bucket.update(mapping)
        return 1

    def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))


def _settings(tmp_path: Path, *, url: str = "", key: str = "") -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        data_dir=str(tmp_path),
        environment="test",
        data_bank_api_url=url,
        data_bank_api_key=key,
    )


def test_jobs_uploads_to_data_bank_and_sets_file_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange a tiny corpus stream and environment
    job_id: Final[str] = "job-123"
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)

    # Patch corpus ensure + streaming
    def _noop_ensure1(
        *,
        source: str,
        language: str,
        data_dir: str,
        max_sentences: int,
        transliterate: bool,
        confidence_threshold: float,
    ) -> None:
        return None

    monkeypatch.setattr("core.corpus_download.ensure_corpus_file", _noop_ensure1)

    class _Svc:
        def __init__(self, _data_dir: str) -> None:
            pass

        def stream(self, _spec: object) -> Iterator[str]:
            yield from ["a", "b", "c"]

    monkeypatch.setattr("api.jobs.LocalCorpusService", _Svc)

    # Mock httpx.post to simulate data-bank-api upload success
    class _Resp:
        def __init__(self, code: int, body: dict[str, object]) -> None:
            self.status_code = code
            self.text = json.dumps(body)

    captured_url: str = ""
    captured_headers: dict[str, str] = {}
    captured_files_key: str = ""

    def _post(
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        timeout: float,
    ) -> _Resp:
        nonlocal captured_url, captured_headers, captured_files_key
        captured_url = url
        captured_headers = headers
        captured_files_key = next(iter(files.keys()))
        return _Resp(201, {"file_id": "deadbeef"})

    import api.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod.httpx, "post", _post)

    # Redis in-memory
    r = _Redis()
    s = _settings(tmp_path, url="http://db", key="K")
    log = _FakeLogger()

    # Act
    out = process_corpus_impl(
        job_id,
        params={
            "source": "oscar",
            "language": "kk",
            "max_sentences": 3,
            "transliterate": False,
            "confidence_threshold": 0.9,
        },
        redis=r,
        settings=s,
        logger=log,
    )

    # Assert output path created and upload endpoint invoked
    assert (tmp_path / "results" / f"{job_id}.txt").exists()
    assert captured_url.endswith("/files") and captured_headers["X-API-Key"] == "K"
    # file_id persisted in redis
    data = r.hgetall(f"job:{job_id}")
    assert data.get("file_id") == "deadbeef"
    # function result reflects completion
    assert out["status"] == "completed"


def test_jobs_upload_handles_missing_file_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange minimal environment and noop corpus
    def _noop_ensure(
        *,
        source: str,
        language: str,
        data_dir: str,
        max_sentences: int,
        transliterate: bool,
        confidence_threshold: float,
    ) -> None:
        return None

    monkeypatch.setattr("core.corpus_download.ensure_corpus_file", _noop_ensure)

    class _Svc2:
        def __init__(self, _data_dir: str) -> None:
            pass

        def stream(self, _spec: object) -> Iterator[str]:
            yield from ["x"]

    monkeypatch.setattr("api.jobs.LocalCorpusService", _Svc2)

    class _Resp:
        def __init__(self, code: int, body: dict[str, object]) -> None:
            self.status_code = code
            self.text = json.dumps(body)

    # Respond without file_id
    def _post2(
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        timeout: float,
    ) -> _Resp:
        return _Resp(200, {"ok": True})

    monkeypatch.setattr("api.jobs.httpx.post", _post2)

    r = _Redis()
    s = _settings(tmp_path, url="http://db", key="K")
    out = process_corpus_impl(
        "job-2",
        params={
            "source": "oscar",
            "language": "kk",
            "max_sentences": 1,
            "transliterate": False,
            "confidence_threshold": 0.9,
        },
        redis=r,
        settings=s,
        logger=_FakeLogger(),
    )
    # file_id not set but job still completes
    data = r.hgetall("job:job-2")
    assert "file_id" not in data
    assert out["status"] == "completed"
