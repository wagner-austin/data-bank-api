#!/usr/bin/env python
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGET_DIRS = [
    ROOT / "src",
    ROOT / "tests",
]

EXCLUDE_DIRNAMES = {".venv", "__pycache__", "node_modules"}
ALLOW_EXT = {".py"}

PATTERNS: dict[str, re.Pattern[str]] = {
    "typing.Any": re.compile(r"\btyping\.Any\b"),
    "Any import": re.compile(r"\bfrom\s+typing\s+import\b[^#\n]*\bAny\b"),
    "Any usage": re.compile(r"(?<!\w)Any(?!\w)"),
    "type: ignore": re.compile(r"type:\s*ignore"),
    "typing.cast": re.compile(r"\btyping\.cast\b"),
    "TODO": re.compile(r"\bTODO\b"),
    "FIXME": re.compile(r"\bFIXME\b"),
    "HACK": re.compile(r"\bHACK\b"),
    "XXX": re.compile(r"\bXXX\b"),
    "WIP": re.compile(r"\bWIP\b"),
    "logging.basicConfig": re.compile(r"\blogging\.basicConfig\s*\("),
    "noqa": re.compile(r"#\s*noqa\b"),
}


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for base in paths:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            # Disallow .pyi stubs entirely
            if p.suffix == ".pyi":
                yield p
                continue
            if p.suffix not in ALLOW_EXT:
                continue
            if any(part in EXCLUDE_DIRNAMES for part in p.parts):
                continue
            yield p


def scan_file(path: Path) -> list[str]:
    errors: list[str] = []
    # Disallow .pyi files existing at all
    if path.suffix == ".pyi":
        return [f"{path}: disallowed file: .pyi stubs are not permitted"]

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError) as e:  # pragma: no cover
        raise RuntimeError(f"failed to read {path}: {e}") from e

    allow_print = "tests" in path.parts
    lines = text.splitlines()
    for name, pat in PATTERNS.items():
        if name == "noqa" and allow_print:
            pass
        for i, line in enumerate(lines, start=1):
            if pat.search(line):
                errors.append(f"{path}:{i}: disallowed pattern: {name}")

    # Disallow print() in library code
    if not allow_print:
        for i, line in enumerate(lines, start=1):
            if re.search(r"(^|\s)print\s*\(", line):
                errors.append(f"{path}:{i}: disallowed pattern: print() in library code")

    return errors


def main() -> int:
    violations: list[str] = []
    for f in iter_files(TARGET_DIRS):
        violations.extend(scan_file(f))
    if violations:
        print("Guard checks failed:")
        for v in violations:
            print(f"  {v}")
        return 2
    print("Guards OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
