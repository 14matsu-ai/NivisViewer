from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import os
import runpy
import sys
import ast
from types import SimpleNamespace

import pytest

from scripts.verify_portable_build import verify, audit_runtime_files
from scripts.portable_build_policy import prepare_binaries


ROOT = Path(__file__).resolve().parents[1]


def test_binary_policy_rejects_external_toolchain_and_removes_only_same_package_dll(tmp_path):
    runtime = tmp_path / "runtime"
    external = tmp_path / "poppler"
    dll = runtime / "numpy.libs" / "blas.dll"
    entries = [("blas.dll", str(dll), "BINARY"), ("numpy.libs/blas.dll", str(dll), "BINARY")]
    assert prepare_binaries(entries, allowed_roots=[runtime]) == [entries[1]]
    other = runtime / "other" / "blas.dll"
    entries[0] = ("blas.dll", str(other), "BINARY")
    assert prepare_binaries(entries, allowed_roots=[runtime]) == entries
    with pytest.raises(ValueError, match="Unapproved binary source"):
        prepare_binaries([("icuuc.dll", str(external / "icuuc.dll"), "BINARY")], allowed_roots=[runtime])


def test_binary_policy_rejects_old_environment_inside_repository(tmp_path):
    source = tmp_path / ".venv311" / "Lib" / "site-packages" / "legacy.pyd"
    with pytest.raises(ValueError, match="different virtual environment"):
        prepare_binaries([("legacy.pyd", str(source), "BINARY")], allowed_roots=[tmp_path], environment_root=tmp_path / ".venv313")


@pytest.mark.parametrize("old_folder", ["dist/release-1.1.13", "build/old-candidate", "portable-backups/old"])
def test_actual_spec_source_allowlist_rejects_old_outputs_and_accepts_current_wheel(tmp_path, old_folder):
    # Evaluate only the production spec's allowlist expression, without running
    # Analysis or duplicating the configuration in this regression test.
    tree = ast.parse((ROOT / "NivisViewer.spec").read_text(encoding="utf-8"))
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "prepare_binaries")
    expression = next(keyword.value for keyword in call.keywords if keyword.arg == "allowed_roots")
    repository = tmp_path / "repo"
    venv = repository / ".venv313"
    roots = eval(compile(ast.Expression(expression), "NivisViewer.spec", "eval"), {
        "__builtins__": {}, "root": repository,
        "sys": SimpleNamespace(prefix=str(venv), base_prefix=str(tmp_path / "python")),
        "os": SimpleNamespace(environ={"SystemRoot": str(tmp_path / "Windows")}),
    })
    old = repository / old_folder / "NivisViewer/_internal/Qt6Core.dll"
    current = venv / "Lib/site-packages/PySide6/Qt6Core.dll"
    for source in (old, current):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"same DLL name, no Python ABI tag")
    with pytest.raises(ValueError, match="Unapproved binary source"):
        prepare_binaries([("Qt6Core.dll", str(old), "BINARY")], allowed_roots=roots, environment_root=venv)
    entries = [("PySide6/Qt6Core.dll", str(current), "BINARY")]
    assert prepare_binaries(entries, allowed_roots=roots, environment_root=venv) == entries


def test_runtime_file_audit_rejects_old_abi_bytecode_and_duplicate_readme(tmp_path):
    (tmp_path / "README.md").write_text("new", encoding="utf-8")
    (tmp_path / "_imaging.cp311-win_amd64.pyd").write_bytes(b"old")
    (tmp_path / "old.pyc").write_bytes(b"wrong bytecode")
    internal = tmp_path / "_internal"
    internal.mkdir()
    (internal / "README.md").write_text("old", encoding="utf-8")
    errors = audit_runtime_files(tmp_path, "3.13.16")
    assert any("ABI tag" in error for error in errors)
    assert any("bytecode magic" in error for error in errors)
    assert any("exactly one README" in error for error in errors)


def test_runtime_file_audit_accepts_crt_names_and_current_bytecode(tmp_path):
    (tmp_path / "README.md").write_text("new", encoding="utf-8")
    (tmp_path / "MSVCP140.dll").write_bytes(b"CRT file name contains cp140")
    (tmp_path / "new.pyc").write_bytes(importlib.util.MAGIC_NUMBER + b"fixture")
    assert audit_runtime_files(tmp_path, ".".join(map(str, sys.version_info[:3]))) == []


def test_portable_verifier_detects_duplicate_package_dll(tmp_path):
    internal = tmp_path / "_internal"
    libs = internal / "numpy.libs"
    libs.mkdir(parents=True)
    (internal / "blas.dll").write_bytes(b"identical DLL")
    (libs / "blas.dll").write_bytes(b"identical DLL")
    assert "duplicate package DLL at bundle root: blas.dll" in verify(tmp_path)


def test_spec_is_windowed_onedir_and_contains_portable_resources():
    text = (ROOT / "NivisViewer.spec").read_text(encoding="utf-8")
    assert 'name="NivisViewer"' in text
    assert "console=False" in text
    assert "upx=False" in text
    assert "strip=False" in text
    assert "portable.flag" in text
    assert "assets/icons" in text
    assert "icon=str(app_icon)" in text
    assert "COLLECT(" in text


def test_build_dependency_is_separate_from_runtime():
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    build = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    assert "PyInstaller" not in runtime
    assert "PyInstaller>=6,<7" in build


def test_portable_flag_and_build_scripts_exist():
    assert (ROOT / "portable.flag").is_file()
    assert (ROOT / "scripts" / "build_portable.ps1").is_file()
    assert (ROOT / "scripts" / "verify_portable_build.py").is_file()
    assert (ROOT / "scripts" / "collect_licenses.py").is_file()
    assert (ROOT / "scripts" / "generate_branding_assets.py").is_file()


def test_portable_verifier_requires_runtime_branding_assets(tmp_path):
    errors = verify(tmp_path)
    assert "branding asset not found: nivisviewer.ico" in errors
    assert "branding asset not found: nivisviewer_icon.png" in errors
    assert "branding asset not found: nivisviewer_logo.png" in errors

    for name in (
        "nivisviewer.ico",
        "nivisviewer_icon.png",
        "nivisviewer_logo.png",
    ):
        (tmp_path / name).write_bytes(b"branding")
    errors = verify(tmp_path)
    assert not any(error.startswith("branding asset not found:") for error in errors)


def test_frozen_smoke_hook_records_early_import_error(tmp_path, monkeypatch):
    output = tmp_path / "smoke.json"
    monkeypatch.setattr(sys, "argv", ["NivisViewer.exe", "--smoke-test-output", str(output)])
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)

    def exit_process(code):
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", exit_process)
    runpy.run_path(str(ROOT / "scripts" / "frozen_smoke_hook.py"))
    with pytest.raises(SystemExit, match="1"):
        sys.excepthook(ImportError, ImportError("synthetic missing DLL"), None)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["success"] is False
    assert result["stage"] == "startup"
    assert "synthetic missing DLL" in result["errors"][0]


def test_frozen_smoke_hook_leaves_normal_startup_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["NivisViewer.exe"])
    previous = sys.excepthook
    runpy.run_path(str(ROOT / "scripts" / "frozen_smoke_hook.py"))
    assert sys.excepthook is previous
