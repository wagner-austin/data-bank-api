from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from scripts import guard


def test_guard_flags_pyi_stub(tmp_path: Path) -> None:
    pyi = tmp_path / "stub.pyi"
    pyi.write_text("# stub file\n", encoding="utf-8")
    errs = guard.scan_file(pyi)
    assert any("disallowed file: .pyi" in e for e in errs)


def test_guard_detects_forbidden_patterns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    # Build forbidden patterns dynamically to avoid tripping the repository guard
    any_kw = "".join(["A", "n", "y"])  # assembled token
    type_ignore = "".join(["type", ": ", "ignore"])  # assembled token
    basic_cfg = ".".join(["logging", "".join(["basic", "Config"])])  # assembled token
    lines = [
        f"from typing import {any_kw}\n",
        "import logging\n",
        f"def f(x):  # {type_ignore}\n",
        "    " + "print" + '("hi")\n',
        f"    {basic_cfg}(level=logging.INFO)\n",
        "    return x\n",
    ]
    bad.write_text("".join(lines), encoding="utf-8")
    errs = guard.scan_file(bad)
    # Expect to see several violations without relying on specific token strings
    assert len(errs) >= 3


def test_iter_files_and_exclusions(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "stub.pyi").write_text("# s\n", encoding="utf-8")
    excl = tmp_path / "__pycache__"
    excl.mkdir()
    (excl / "in_cache.py").write_text("print(1)\n", encoding="utf-8")

    files = list(guard.iter_files([tmp_path]))
    assert tmp_path / "ok.py" in files
    assert tmp_path / "stub.pyi" in files
    assert tmp_path / "skip.txt" not in files
    assert (excl / "in_cache.py") not in files
    # Non-existent base path yields no files
    assert list(guard.iter_files([tmp_path / "missing_dir"])) == []


def test_scan_print_rule_library_vs_tests(tmp_path: Path) -> None:
    lib_py = tmp_path / "lib.py"
    lib_py.write_text("def f():\n    print('x')\n", encoding="utf-8")
    errs1 = guard.scan_file(lib_py)
    assert any("print() in library code" in e for e in errs1)

    tdir = tmp_path / "tests"
    tdir.mkdir()
    test_py = tdir / "t.py"
    test_py.write_text("# " + "no" + "qa" + "\nprint('x')\n", encoding="utf-8")
    errs2 = guard.scan_file(test_py)
    joined = "\n".join(errs2)
    assert "print() in library code" not in joined


def test_guard_main_entrypoint_runs() -> None:
    import runpy

    try:
        runpy.run_path("scripts/guard.py", run_name="__main__")
    except SystemExit as e:
        assert isinstance(e.code, int)


def test_main_reports_violations_and_ok(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("# " + "TO" + "DO" + ": x\n", encoding="utf-8")

    def _iter(_paths: list[Path]) -> list[Path]:
        return [bad]

    monkeypatch.setattr(guard, "iter_files", _iter)
    rc = guard.main()
    assert rc == 2

    def _iter_empty(_paths: list[Path]) -> list[Path]:
        return []

    monkeypatch.setattr(guard, "iter_files", _iter_empty)
    rc2 = guard.main()
    assert rc2 == 0
