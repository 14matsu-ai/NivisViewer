from __future__ import annotations

from pathlib import Path

from scripts.verify_portable_build import verify


ROOT = Path(__file__).resolve().parents[1]


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


def test_official_branding_assets_exist():
    icons = ROOT / "assets" / "icons"
    assert (icons / "nivisviewer_logo.png").is_file()
    assert (icons / "nivisviewer_icon.png").is_file()
    assert (icons / "nivisviewer.ico").is_file()


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
