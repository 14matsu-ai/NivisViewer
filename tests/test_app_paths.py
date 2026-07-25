from __future__ import annotations

from pathlib import Path

from app.app_paths import resolve_app_paths
from app.config_manager import ConfigManager
from app.metadata_store import MetadataStore


def test_source_paths_are_independent_of_working_directory(tmp_path, monkeypatch):
    source = tmp_path / "source root"
    source.mkdir()
    (source / "portable.flag").write_text("", encoding="utf-8")
    elsewhere = tmp_path / "別の 作業場所"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    paths = resolve_app_paths(source_root=source)

    assert paths.executable_path == source / "main.py"
    assert paths.resource_dir == source
    assert paths.profile_dir == source
    assert paths.portable
    assert paths.config_path == source / "config.json"
    assert paths.metadata_path == source / "data" / "metadata.sqlite3"


def test_profile_override_precedes_environment_and_portable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "portable.flag").write_text("", encoding="utf-8")
    explicit = tmp_path / "明示 profile"
    environment = tmp_path / "environment"

    paths = resolve_app_paths(
        source_root=source,
        profile_override=str(explicit),
        environ={"NIVISVIEWER_PROFILE_DIR": str(environment)},
    )

    assert paths.profile_dir == explicit
    assert paths.writable


def test_frozen_uses_executable_for_mutable_data_and_bundle_for_resources(tmp_path):
    executable_dir = tmp_path / "日本語 portable"
    executable_dir.mkdir()
    executable = executable_dir / "NivisViewer.exe"
    bundle = executable_dir / "_internal"
    bundle.mkdir()
    (executable_dir / "portable.flag").write_text("", encoding="utf-8")

    paths = resolve_app_paths(
        frozen=True,
        executable_path=executable,
        resource_dir=bundle,
    )

    assert paths.executable_path == executable
    assert paths.resource_dir == bundle
    assert paths.profile_dir == executable_dir
    assert paths.data_dir != bundle / "data"


def test_writability_failure_does_not_fall_back(tmp_path, monkeypatch):
    profile = tmp_path / "read only"

    def fail_mkdir(self: Path, *args, **kwargs):
        if self == profile:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    original = Path.mkdir
    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    paths = resolve_app_paths(
        profile_override=str(profile),
        source_root=tmp_path,
    )

    assert paths.profile_dir == profile
    assert not paths.writable
    assert "denied" in (paths.write_error or "")


def test_read_only_services_do_not_create_mutable_files(tmp_path):
    profile = tmp_path / "read-only-profile"
    config = ConfigManager(profile / "config.json", writable=False)
    config.save({"background_color": "#123456"})
    metadata = MetadataStore(
        profile / "data" / "metadata.sqlite3",
        initialize=False,
    )
    try:
        assert not config.path.exists()
        assert not metadata.enabled
        assert not profile.exists()
    finally:
        metadata.close()
