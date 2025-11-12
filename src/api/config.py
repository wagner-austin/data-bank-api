from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Settings for the job processing client.

    This mirrors only the fields required by the tests and by the job implementation.
    """

    redis_url: str
    data_dir: str
    environment: str
    data_bank_api_url: str
    data_bank_api_key: str
