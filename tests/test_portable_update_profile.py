"""Validate the documented fresh-directory update using synthetic files only."""
from pathlib import Path
import shutil

import pytest

from app.app_paths import resolve_app_paths


@pytest.mark.parametrize("profile_mode", ["portable", "explicit", "localappdata"])
def test_fresh_bundle_swap_preserves_profile_and_final_exe_path(tmp_path, profile_mode):
    installed = tmp_path / "日本語のアプリ" / "NivisViewer"
    installed.mkdir(parents=True)
    exe = installed / "NivisViewer.exe"
    exe.write_bytes(b"old exe")
    old_runtime = installed / "_internal"
    old_runtime.mkdir()
    (old_runtime / "python311.dll").write_bytes(b"old runtime")
    (old_runtime / "decoder.cp311-win_amd64.pyd").write_bytes(b"old extension")
    (installed / "利用者の独自メモ.txt").write_text("keep", encoding="utf-8")
    environ = {"LOCALAPPDATA": str(tmp_path / "local-profile")}
    override = None
    if profile_mode == "portable":
        (installed / "portable.flag").touch()
    elif profile_mode == "explicit":
        override = str(tmp_path / "別置きのプロフィール")
    before = resolve_app_paths(frozen=True, executable_path=exe, environ=environ,
                               profile_override=override, check_writable=False)
    before.profile_dir.mkdir(parents=True, exist_ok=True)
    before.config_path.write_bytes(b"settings")
    before.data_dir.mkdir()
    for name in ("metadata.sqlite3", "thumbnail_cache/cache.webp", "logs/history.log"):
        target = before.data_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(name.encode("utf-8"))
    original = {str(path.relative_to(before.profile_dir)): path.read_bytes()
                for path in [before.config_path, *before.data_dir.rglob("*")] if path.is_file()}

    fresh = installed.with_name("NivisViewer.new")
    fresh.mkdir()
    (fresh / "NivisViewer.exe").write_bytes(b"new exe")
    (fresh / "_internal").mkdir()
    (fresh / "_internal/python313.dll").write_bytes(b"new runtime")
    if profile_mode == "portable":
        (fresh / "portable.flag").touch()
        shutil.copy2(before.config_path, fresh / "config.json")
        shutil.copytree(before.data_dir, fresh / "data")
    # No recursive delete. The old program and any custom files stay in the
    # clearly named backup until the user verifies the update and inspects it.
    backup = installed.with_name("NivisViewer.previous")
    installed.rename(backup)
    fresh.rename(installed)
    after = resolve_app_paths(frozen=True, executable_path=exe, environ=environ,
                              profile_override=override, check_writable=False)
    assert after.executable_path == before.executable_path
    assert after.profile_dir == before.profile_dir
    assert after.config_path.read_bytes() == b"settings"
    assert original == {name: (after.profile_dir / name).read_bytes() for name in original}
    assert (backup / "利用者の独自メモ.txt").read_text(encoding="utf-8") == "keep"
    assert not (installed / "_internal/python311.dll").exists()
    assert not list(installed.rglob("*cp311*"))
    # Documented rollback uses renames and retains the new profile too.
    installed.rename(installed.with_name("NivisViewer.failed"))
    backup.rename(installed)
    assert exe.read_bytes() == b"old exe"
