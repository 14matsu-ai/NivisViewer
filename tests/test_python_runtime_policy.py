from __future__ import annotations

import pytest

from scripts.python_runtime_policy import validate_runtime, validate_release_dependencies


def details(**changes):
    result = dict(version=[3, 13, 16], releaselevel="final", platform="win32",
                  bits=64, machine="AMD64", free_threaded=False,
                  prefix="new-venv", base_prefix="installed-python")
    result.update(changes)
    return result


@pytest.mark.parametrize("version", [[3, 11, 17], [3, 12, 14], [3, 13, 15], [3, 14, 0]])
def test_build_rejects_old_or_different_python_series(version):
    with pytest.raises(ValueError, match="3.13.16"):
        validate_runtime(details(version=version), require_venv=True)


@pytest.mark.parametrize("change", [dict(bits=32), dict(machine="ARM64"), dict(platform="linux"), dict(free_threaded=True), dict(releaselevel="candidate"), dict(prefix="installed-python")])
def test_build_rejects_unsupported_runtime_or_nonisolated_environment(change):
    with pytest.raises(ValueError):
        validate_runtime(details(**change), require_venv=True)


@pytest.mark.parametrize("version", [[3, 13, 16], [3, 13, 17]])
def test_build_accepts_security_baseline_and_later_patch(version):
    validate_runtime(details(version=version), require_venv=True)


def test_release_dependency_mismatch_fails_before_build(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# actual baseline\nPySide6==6.11.2\n", encoding="utf-8")
    monkeypatch.setattr("scripts.python_runtime_policy.metadata.version", lambda _name: "6.11.1")
    with pytest.raises(ValueError, match="mismatch"):
        validate_release_dependencies(requirements)


def test_runtime_selection_and_output_isolation_are_explicit():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/build_portable.ps1").read_text(encoding="utf-8")
    assert '.venv313\\Scripts\\python.exe' in script
    assert '.venv311' not in script
    assert '$Python = "python"' not in script
    assert '--workpath $BuildDir --distpath $DistDir' in script
    assert 'Join-Path $BuildRoot $OutputName' in script
    assert 'Join-Path $DistRoot $OutputName' in script
