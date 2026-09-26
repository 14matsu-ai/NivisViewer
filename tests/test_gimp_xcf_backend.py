from __future__ import annotations

from pathlib import Path
import subprocess

from PIL import Image
import pytest

from app import gimp_xcf_backend as backend


def test_discovers_per_user_install_without_path_entry(tmp_path, monkeypatch):
    if backend.os.name != "nt":
        pytest.skip("Windows per-user installation")
    binary_dir = tmp_path / "Programs" / "GIMP 3" / "bin"
    binary_dir.mkdir(parents=True)
    console = binary_dir / "gimp-console-3.2.exe"
    console.touch()
    (binary_dir / "gimp-3.2.exe").touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for variable in ("NIVISVIEWER_GIMP_EXE", "ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(backend.shutil, "which", lambda _: None)
    monkeypatch.setattr(backend, "_probe_version", lambda _: (3, 2, 4))
    backend.clear_gimp_discovery_cache()
    try:
        assert backend.find_gimp3_executable() == str(console.resolve())
    finally:
        backend.clear_gimp_discovery_cache()


def test_parse_gimp_version_accepts_current_three_x() -> None:
    assert backend._parse_version(
        "GNU Image Manipulation Program version 3.2.6"
    ) == (3, 2, 6)


def test_parse_gimp_version_rejects_unrelated_output() -> None:
    assert backend._parse_version("not a version") is None


def test_batch_code_uses_public_gimp_file_save(tmp_path: Path) -> None:
    code = backend._batch_code(tmp_path / "render.png")

    assert "Gimp.file_save" in code
    assert "Gimp.RunMode.NONINTERACTIVE" in code
    assert "Gimp.get_images()" in code
    assert "Gio.File.new_for_path" in code


def test_render_backend_uses_noninteractive_new_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backend,
        "find_gimp3_executable",
        lambda: "gimp-console-3.2",
    )
    observed: list[list[str]] = []

    def fake_run(executable, input_path, output_path):
        observed.append([executable, str(input_path), str(output_path)])
        with Image.new("RGBA", (32, 48), (1, 2, 3, 255)) as image:
            image.save(output_path, "PNG")

    monkeypatch.setattr(backend, "_run_gimp", fake_run)

    source = tmp_path / "modern.xcf"
    source.write_bytes(b"gimp xcf v026\x00" + b"\x00" * 64)
    with backend.render_xcf_with_gimp(source) as image:
        assert image.size == (32, 48)

    assert observed
    assert observed[0][0] == "gimp-console-3.2"


def test_run_gimp_builds_safe_argument_vector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_subprocess_run(arguments, **kwargs):
        captured["arguments"] = list(arguments)
        output = tmp_path / "out.png"
        # _run_gimp checks the explicit output_path passed by the caller.
        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""
        return Completed()

    monkeypatch.setattr(backend, "_run_process", fake_subprocess_run)
    output = tmp_path / "out.png"
    output.write_bytes(b"x")

    backend._run_gimp(
        "gimp-console-3.2",
        tmp_path / "in.xcf",
        output,
    )

    arguments = captured["arguments"]
    assert "--new-instance" in arguments
    assert "--no-interface" in arguments
    assert "--batch-interpreter=python-fu-eval" in arguments
    assert "--quit" in arguments
    assert str(tmp_path / "in.xcf") in arguments


def test_render_cleans_temporary_files_and_preserves_source(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "find_gimp3_executable", lambda: "fake-gimp")
    source = tmp_path / "日本語.xcf"
    source.write_bytes(b"original")
    temporary_paths = []
    def fake_run(executable, input_path, output_path):
        assert input_path.read_bytes() == b"original"
        temporary_paths.append(output_path.parent)
        Image.new("RGB", (2, 3), "red").save(output_path)
    monkeypatch.setattr(backend, "_run_gimp", fake_run)
    for data in (source, b"original"):
        with backend.render_xcf_with_gimp(data) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)
    assert source.read_bytes() == b"original"
    assert all(not path.exists() for path in temporary_paths)


def test_render_failure_cleans_temp_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "find_gimp3_executable", lambda: "fake-gimp")
    temporary_paths = []
    def fail(executable, input_path, output_path):
        temporary_paths.append(input_path.parent)
        raise backend.GimpXcfBackendError("failed")
    monkeypatch.setattr(backend, "_run_gimp", fail)
    with pytest.raises(backend.GimpXcfBackendError):
        backend.render_xcf_with_gimp(b"original")
    assert all(not path.exists() for path in temporary_paths)


def test_process_timeout_reports_failure(tmp_path, monkeypatch):
    def timeout(arguments, **kwargs):
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])
    monkeypatch.setattr(backend, "_run_process", timeout)
    with pytest.raises(backend.GimpXcfBackendError, match="timed out"):
        backend._run_gimp("fake-gimp", tmp_path / "in.xcf", tmp_path / "out.png")


def test_gimp_output_pixel_limit_is_checked_before_loading(monkeypatch):
    monkeypatch.setattr(backend, "find_gimp3_executable", lambda: "fake-gimp")
    monkeypatch.setattr(backend, "GIMP_OUTPUT_MAX_PIXELS", 5)
    def fake_run(executable, input_path, output_path):
        with Image.new("RGB", (2, 3)) as image:
            image.save(output_path)
    monkeypatch.setattr(backend, "_run_gimp", fake_run)
    with pytest.raises(backend.GimpXcfBackendError) as failure:
        backend.render_xcf_with_gimp(b"original")
    assert "dimensions exceed" in str(failure.value.__cause__)
