from __future__ import annotations

import ctypes
from pathlib import Path

from app.windows_file_association import (
    WindowsFileAssociationResolver,
    identify_archive_application,
    resolve_archive_cli,
)


def test_identifies_only_known_archive_executables() -> None:
    assert identify_archive_application(r"C:\Apps\WinRAR.exe") == "winrar"
    assert identify_archive_application(r"C:\Apps\7z.exe") == "seven_zip"
    assert identify_archive_application(r"C:\Apps\7zz.exe") == "seven_zip"
    assert identify_archive_application(r"C:\Apps\7zFM.exe") == "seven_zip_gui"
    assert identify_archive_application(r"C:\Windows\explorer.exe") is None
    assert identify_archive_application(r"C:\Windows\OpenWith.exe") is None


def test_7zip_gui_association_resolves_only_to_sibling_cli(tmp_path: Path) -> None:
    gui = tmp_path / "7zFM.exe"
    cli = tmp_path / "7z.exe"
    gui.write_bytes(b"gui")
    cli.write_bytes(b"cli")

    assert resolve_archive_cli(str(gui)) == (str(cli), "seven_zip")
    cli.unlink()
    assert resolve_archive_cli(str(gui)) == (None, None)

    second_gui = tmp_path / "7zG.exe"
    second_cli = tmp_path / "7zz.exe"
    second_gui.write_bytes(b"gui")
    second_cli.write_bytes(b"cli")
    assert resolve_archive_cli(str(second_gui)) == (str(second_cli), "seven_zip")


def test_unicode_association_path_is_preserved(tmp_path: Path) -> None:
    executable = tmp_path / "日本語アプリ" / "WinRAR.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"x")
    result = WindowsFileAssociationResolver(
        query=lambda _extension, _verb: str(executable),
        platform_name="win32",
    ).resolve_executable(".rar")

    assert result.executable_path == str(executable)
    assert result.application_kind == "winrar"


def test_cbr_and_cb7_use_format_association_fallbacks(tmp_path: Path) -> None:
    winrar = tmp_path / "WinRAR.exe"
    seven_zip = tmp_path / "7z.exe"
    winrar.write_bytes(b"x")
    seven_zip.write_bytes(b"x")
    calls: list[str] = []

    def query(extension: str, _verb: str) -> str | None:
        calls.append(extension)
        return {
            ".rar": str(winrar),
            ".7z": str(seven_zip),
        }.get(extension)

    resolver = WindowsFileAssociationResolver(query=query, platform_name="win32")

    assert resolver.resolve_archive_extension(".cbr").application_kind == "winrar"
    assert calls[:2] == [".cbr", ".rar"]
    assert resolver.resolve_archive_extension(".cb7").application_kind == "seven_zip"
    assert calls[2:] == [".cb7", ".7z"]


def test_unknown_missing_and_unc_associations_are_rejected(tmp_path: Path) -> None:
    unknown = tmp_path / "OtherArchiver.exe"
    unknown.write_bytes(b"x")
    results = iter((str(unknown), str(tmp_path / "7z.exe"), r"\\server\app.exe"))
    resolver = WindowsFileAssociationResolver(
        query=lambda _extension, _verb: next(results),
        platform_name="win32",
    )

    assert resolver.resolve_executable(".rar").error_code == "unsupported_association"
    assert resolver.resolve_executable(".7z").error_code == "executable_not_found"
    assert resolver.resolve_executable(".cb7").error_code == "unsafe_association"


def test_non_windows_is_nonfatal() -> None:
    result = WindowsFileAssociationResolver(platform_name="linux").resolve_executable(
        ".rar"
    )

    assert result.executable_path is None
    assert result.error_code == "platform_not_supported"


def test_assoc_query_string_uses_two_pass_unicode_buffer(monkeypatch) -> None:
    calls: list[tuple[object, int]] = []

    class FakeFunction:
        def __init__(self) -> None:
            self.argtypes = None
            self.restype = None

        def __call__(self, _flags, _kind, extension, verb, buffer, length) -> int:
            calls.append((buffer, length._obj.value))
            assert extension == ".rar"
            assert verb == "open"
            if buffer is None:
                length._obj.value = len(r"C:\日本語\WinRAR.exe") + 1
                return 1
            buffer.value = r"C:\日本語\WinRAR.exe"
            return 0

    function = FakeFunction()

    class FakeLibrary:
        AssocQueryStringW = function

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: FakeLibrary())

    assert (
        WindowsFileAssociationResolver._assoc_query_string(".rar", "open")
        == r"C:\日本語\WinRAR.exe"
    )
    assert len(calls) == 2
    assert calls[0][0] is None
    assert calls[1][0] is not None
