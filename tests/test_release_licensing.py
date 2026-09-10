import hashlib
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.diagnostics_dialog import DiagnosticsDialog
from app.version import COPYRIGHT, LICENSE_IDENTIFIER, windows_version_info_text
from scripts import collect_licenses as collector
from scripts.verify_portable_build import verify


ROOT = Path(__file__).resolve().parents[1]


def test_project_license_about_and_future_package_sources(qapp, tmp_path):
    assert LICENSE_IDENTIFIER == "AGPL-3.0-or-later"
    assert hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest() == (
        "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
    )  # Verbatim official GNU agpl-3.0.txt, including its final newline.
    notice = (ROOT / "PROJECT_LICENSE.md").read_text(encoding="utf-8")
    assert COPYRIGHT in notice and "any later version" in notice
    assert "Permission is hereby granted" in (ROOT / "licenses/NivisViewer-Historical-MIT.txt").read_text()
    assert LICENSE_IDENTIFIER in windows_version_info_text()
    dialog = DiagnosticsDialog(tmp_path)
    try:
        text = dialog.text_edit.toPlainText()
        assert f"License: {LICENSE_IDENTIFIER}" in text
        assert COPYRIGHT in text and "License: MIT" not in text
    finally:
        dialog.close()
    for file in ("NivisViewer.spec", "scripts/build_portable.ps1", "scripts/verify_portable_build.py"):
        assert "PROJECT_LICENSE.md" in (ROOT / file).read_text(encoding="utf-8")
    assert "[MIT License](LICENSE)" not in (ROOT / "README.md").read_text(encoding="utf-8")


def fake_distribution(tmp_path, files, *, version="6.11.2"):
    meta = Message()
    meta["License"] = "LGPL-3.0-only OR GPL-3.0-only"
    meta["License-File"] = "LicenseRef-Qt-Commercial.txt"
    for file, content in files.items():
        path = tmp_path / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return SimpleNamespace(metadata=meta, version=version, files=tuple(files),
                           locate_file=lambda file: tmp_path / file)


def only_distribution(monkeypatch, distribution):
    monkeypatch.setattr(collector, "RUNTIME_DISTRIBUTIONS", ("PySide6",))
    monkeypatch.setattr(collector, "BUILD_DISTRIBUTIONS", ())
    monkeypatch.setattr(collector.metadata, "distribution", lambda _: distribution)


def test_qt_supplement_is_offline_version_matched_and_not_a_compliance_claim(tmp_path, monkeypatch):
    distribution = fake_distribution(tmp_path / "wheel", {
        "example.dist-info/licenses/LicenseRef-Qt-Commercial.txt": b"commercial reference fixture",
    })
    only_distribution(monkeypatch, distribution)
    output = tmp_path / "notices"
    result = collector.collect(output, strict=True)
    entry = result["runtime"][0]
    assert result["manual_review_required"] is True
    assert entry["declared_license"] == "LGPL-3.0-only OR GPL-3.0-only"
    assert entry["files"] == ["LicenseRef-Qt-Commercial.txt"]
    assert entry["supplement"]["path"] == "Qt/6.11.2/SOURCES.json"
    index = json.loads((output / entry["supplement"]["path"]).read_text())
    for item in index["files"]:
        assert hashlib.sha256((output / "Qt/6.11.2" / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    assert any("module/third-party/source review remains" in warning for warning in result["warnings"])
    assert any("differs from release pin" in warning for warning in result["warnings"])
    distribution.version = "99.0.0"
    future = collector.collect(tmp_path / "future")
    assert future["runtime"][0]["supplement"] is None
    assert not (tmp_path / "future/Qt").exists()
    assert any("not a commercial-only conclusion" in warning for warning in future["warnings"])


def test_collector_preserves_colliding_existing_and_wheel_notices(tmp_path, monkeypatch):
    distribution = fake_distribution(tmp_path / "wheel", {
        "one/LICENSE": b"first license", "two/LICENSE": b"second license",
        "three/NOTICE": b"upstream notice", "four/module.py": b"not a notice",
    })
    only_distribution(monkeypatch, distribution)
    output = tmp_path / "output"
    existing = output / "PySide6/LICENSE"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing user notice")
    first = collector.collect(output)["runtime"][0]
    second = collector.collect(output)["runtime"][0]
    assert first == second
    assert existing.read_bytes() == b"existing user notice"
    assert {path.read_bytes() for path in existing.parent.iterdir()} == {
        b"existing user notice", b"first license", b"second license", b"upstream notice",
    }
    for file, digest in first["file_sha256"].items():
        assert hashlib.sha256((existing.parent / file).read_bytes()).hexdigest() == digest


def test_qt_supplement_rejects_modified_text_without_overwriting_it(tmp_path):
    changed = tmp_path / "Qt/6.11.2/GPL-2.0-only.txt"
    changed.parent.mkdir(parents=True)
    changed.write_bytes(b"existing modified notice")
    with pytest.raises(RuntimeError, match="Conflicting Qt license supplement"):
        collector._qt_supplement(tmp_path, "6.11.2")
    assert changed.read_bytes() == b"existing modified notice"


def test_qt_supplement_rejects_corrupt_source_index(tmp_path, monkeypatch):
    folder = tmp_path / "source/licenses/Qt/6.11.2"
    folder.mkdir(parents=True)
    (folder / "GPL.txt").write_bytes(b"not the expected license")
    (folder / "SOURCES.json").write_text(json.dumps({
        "version": "6.11.2", "files": [{"file": "GPL.txt", "sha256": "0" * 64}],
    }))
    monkeypatch.setattr(collector, "ROOT", tmp_path / "source")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        collector._qt_supplement(tmp_path / "output", "6.11.2")


def test_collector_keeps_exact_interpreter_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "RUNTIME_DISTRIBUTIONS", ())
    monkeypatch.setattr(collector, "BUILD_DISTRIBUTIONS", ())
    interpreter = tmp_path / "python"
    interpreter.mkdir()
    payload = b"Python upstream notice\r\n"
    (interpreter / "LICENSE.txt").write_bytes(payload)
    monkeypatch.setattr(collector.sys, "base_prefix", str(interpreter))
    result = collector.collect(tmp_path / "output")
    notice = result["interpreter"]
    assert (tmp_path / "output" / notice["file"]).read_bytes() == payload
    assert notice["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("executable", ["ffmpeg.exe", "ffprobe.exe", "7z.exe", "WinRAR.exe"])
def test_external_tools_cannot_silently_enter_portable_bundle(tmp_path, executable):
    (tmp_path / executable).write_bytes(b"fixture, not executable")
    assert any(executable.casefold() in error for error in verify(tmp_path))
