#!/usr/bin/env python
from __future__ import annotations

from collections.abc import Callable

from scripts.guards.pattern_guard import run as run_pattern_guard

Runner = Callable[[list[str]], int]


def run_guards(roots: list[str]) -> int:
    runners: list[Runner] = [
        run_pattern_guard,
    ]
    for runner in runners:
        rc = runner(roots)
        if rc != 0:
            return rc
    return 0


def main() -> int:
    return run_guards(["src", "scripts", "tests"])


if __name__ == "__main__":
    raise SystemExit(main())
