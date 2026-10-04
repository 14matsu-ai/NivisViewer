"""The update proposal must never broaden scope to unknown files or profiles."""
import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts.plan_portable_update import build_plan, inspect_target


def _archive(tmp_path, entries):
    archive = tmp_path / "old.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for name, content in entries:
            info = zipfile.ZipInfo("placeholder")
            info.filename = name  # Preserve raw ZIP names, including backslashes.
            stream.writestr(info, content)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_plan_and_inspection_only_classify_hash_matching_obsolete_files(tmp_path):
    archive, digest = _archive(tmp_path, [("NivisViewer/_internal/python311.dll", b"old"),
                                         ("NivisViewer/README.md", b"readme")])
    bundle = tmp_path / "new"
    bundle.mkdir()
    (bundle / "README.md").write_bytes(b"new readme")
    entries = build_plan(archive, bundle, digest)["obsolete_files"]
    assert [entry["path"] for entry in entries] == ["_internal/python311.dll"]
    target = bundle / "_internal/python311.dll"
    target.parent.mkdir()
    target.write_bytes(b"old")
    memo = bundle / "独自メモ.txt"
    memo.write_bytes(b"keep")
    assert inspect_target(bundle, entries)[0]["status"] == "known_obsolete_program_file"
    assert target.read_bytes() == b"old" and memo.read_bytes() == b"keep"
    target.write_bytes(b"changed")
    assert inspect_target(bundle, entries)[0]["status"] == "preserve_modified_or_unknown_file"
    target.unlink()  # Synthetic fixture only.
    assert inspect_target(bundle, entries)[0]["status"] == "absent"
    with pytest.raises(ValueError, match="checksum"):
        build_plan(archive, bundle, "0" * 64)


@pytest.mark.parametrize("name", ["../escape.dll", "./file.dll", "x//file.dll", "/absolute.dll",
                                 "x\\escape.dll", "x:stream", "data/history.db", "CONFIG.JSON",
                                 "x./file.dll", "x /file.dll"])
def test_untrusted_paths_and_profile_data_rejected(tmp_path, name):
    archive, digest = _archive(tmp_path, [("NivisViewer/" + name, b"old")])
    bundle = tmp_path / "new"
    bundle.mkdir()
    with pytest.raises(ValueError):
        build_plan(archive, bundle, digest)
    with pytest.raises(ValueError):
        inspect_target(bundle, [dict(path=name, size=3, sha256="bad")])


def test_case_aliases_rejected_in_release_zip(tmp_path):
    archive, digest = _archive(tmp_path, [("NivisViewer/old.dll", b"old"), ("NivisViewer/OLD.DLL", b"old")])
    bundle = tmp_path / "new"
    bundle.mkdir()
    with pytest.raises(ValueError, match="Duplicate"):
        build_plan(archive, bundle, digest)


def test_reparse_point_blocks_inspection_before_file_read(tmp_path, monkeypatch):
    bundle = tmp_path / "new"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    target = internal / "old.dll"
    target.write_bytes(b"old")
    original = Path.lstat

    def lstat(path):
        if path == internal:
            return SimpleNamespace(st_mode=original(path).st_mode,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    entry = dict(path="_internal/old.dll", size=3, sha256=hashlib.sha256(b"old").hexdigest())
    assert inspect_target(bundle, [entry])[0]["status"] == "blocked_reparse_point"
