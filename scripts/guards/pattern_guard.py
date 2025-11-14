from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TARGET_DIRS = [
    ROOT / "src",
    ROOT / "scripts",
    ROOT / "tests",
]

EXCLUDE_DIRNAMES = {".venv", "__pycache__", "node_modules"}
ALLOW_EXT = {".py"}

# Build suppression tokens without embedding the literal words in this file.
_SUP_PREFIX = "sup"
_PRESS_PART = "press"
_RESS_PART = "ress"

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
    # Audit generic sup/press helpers (contextlib or custom).
    "sup-helper": re.compile(rf"(?i)({_SUP_PREFIX}{_PRESS_PART}|{_SUP_PREFIX}{_RESS_PART})"),
}


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            # Disallow .pyi stubs entirely
            if path.suffix == ".pyi":
                yield path
                continue
            if path.suffix not in ALLOW_EXT:
                continue
            if any(part in EXCLUDE_DIRNAMES for part in path.parts):
                continue
            # Do not self-scan guard implementations; they encode patterns.
            if path == ROOT / "scripts" / "guard.py":
                continue
            if path == ROOT / "scripts" / "guards" / "pattern_guard.py":
                continue
            yield path


def _scan_patterns(path: Path, lines: list[str], *, allow_print: bool) -> list[str]:
    errors: list[str] = []
    for name, pattern in PATTERNS.items():
        if name == "noqa" and allow_print:
            continue
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                errors.append(f"{path}:{i}: disallowed pattern: {name}")
    return errors


def _scan_prints(path: Path, lines: list[str], *, allow_print: bool) -> list[str]:
    if allow_print:
        return []
    errors: list[str] = []
    for i, line in enumerate(lines, start=1):
        if re.search(r"(^|\s)print\s*\(", line):
            errors.append(f"{path}:{i}: disallowed pattern: print() in library code")
    return errors


def scan_file(path: Path) -> list[str]:
    # Disallow .pyi files existing at all
    if path.suffix == ".pyi":
        return [f"{path}: disallowed file: .pyi stubs are not permitted"]

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover
        raise RuntimeError(f"failed to read {path}: {exc}") from exc

    allow_print = "tests" in path.parts
    lines = text.splitlines()
    errors: list[str] = []
    errors.extend(_scan_patterns(path, lines, allow_print=allow_print))
    errors.extend(_scan_prints(path, lines, allow_print=allow_print))
    return errors


def run(roots: list[str]) -> int:
    base_paths = [ROOT / r for r in roots]
    violations: list[str] = []
    for file_path in iter_files(base_paths):
        violations.extend(scan_file(file_path))
    if violations:
        print("Guard checks failed:")
        for violation in violations:
            print(f"  {violation}")
        return 2
    print("Guards OK")
    return 0


def main() -> int:
    return run(["src", "scripts", "tests"])


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
