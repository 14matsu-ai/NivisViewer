from __future__ import annotations

from pathlib import Path

from app.windows_file_association import FileAssociationResult
from app.winrar_locator import WinRARInfo, WinRARLocator


class FakeAssociationResolver:
    def __init__(self, result: FileAssociationResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def resolve_archive_extension(self, extension: str) -> FileAssociationResult:
        self.calls.append(extension)
        return self.result

    def reset(self) -> None:
        pass


def successful_probe(path: str) -> WinRARInfo:
    return WinRARInfo(path, True, "7.13 x64", None)


def make_install(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    gui = root / "WinRAR.exe"
    cli = root / "UnRAR.exe"
    gui.write_bytes(b"gui")
    cli.write_bytes(b"cli")
    return gui, cli


def test_explicit_winrar_path_maps_to_official_console_cli(tmp_path: Path) -> None:
    gui, cli = make_install(tmp_path / "manual")
    locator = WinRARLocator(
        association_resolver=FakeAssociationResolver(
            FileAssociationResult(".rar", None, None, "association_not_found")
        ),  # type: ignore[arg-type]
        environment={},
        which=lambda _name: None,
        probe=successful_probe,
    )

    info = locator.locate(gui)

    assert info.available
    assert info.executable_path == str(cli)
    assert info.installation_executable_path == str(gui)
    assert info.discovery_source == "explicit"


def test_association_precedes_standard_install(tmp_path: Path) -> None:
    associated_gui, associated_cli = make_install(tmp_path / "associated")
    standard_gui, _standard_cli = make_install(tmp_path / "Program Files" / "WinRAR")
    resolver = FakeAssociationResolver(
        FileAssociationResult(".rar", str(associated_gui), "winrar")
    )
    locator = WinRARLocator(
        association_resolver=resolver,  # type: ignore[arg-type]
        environment={"ProgramW6432": str(tmp_path / "Program Files")},
        which=lambda _name: None,
        probe=successful_probe,
    )

    info = locator.locate(extension=".rar")

    assert info.executable_path == str(associated_cli)
    assert info.installation_executable_path != str(standard_gui)
    assert info.discovery_source == "association"


def test_invalid_explicit_path_falls_back_to_automatic_detection(
    tmp_path: Path,
) -> None:
    gui, cli = make_install(tmp_path / "associated")
    locator = WinRARLocator(
        association_resolver=FakeAssociationResolver(
            FileAssociationResult(".rar", str(gui), "winrar")
        ),  # type: ignore[arg-type]
        environment={},
        which=lambda _name: None,
        probe=successful_probe,
    )

    info = locator.locate(tmp_path / "missing" / "WinRAR.exe")

    assert info.available
    assert info.executable_path == str(cli)


def test_program_files_x86_then_path_and_probe_failover(tmp_path: Path) -> None:
    _bad_gui, bad_cli = make_install(tmp_path / "x86" / "WinRAR")
    path_gui, path_cli = make_install(tmp_path / "path")
    locator = WinRARLocator(
        association_resolver=FakeAssociationResolver(
            FileAssociationResult(".rar", None, None, "association_not_found")
        ),  # type: ignore[arg-type]
        environment={"ProgramFiles(x86)": str(tmp_path / "x86")},
        which=lambda name: str(path_gui) if name == "WinRAR.exe" else None,
        probe=lambda path: WinRARInfo(
            path,
            path == str(path_cli),
            "7.13" if path == str(path_cli) else None,
            None if path == str(path_cli) else "bad",
        ),
    )

    info = locator.locate()

    assert info.executable_path == str(path_cli)
    assert bad_cli.exists()
    assert info.discovery_source == "path"


def test_results_are_cached_and_reset_redetects(tmp_path: Path) -> None:
    gui, _cli = make_install(tmp_path / "install")
    calls: list[str] = []
    locator = WinRARLocator(
        association_resolver=FakeAssociationResolver(
            FileAssociationResult(".rar", str(gui), "winrar")
        ),  # type: ignore[arg-type]
        environment={},
        which=lambda _name: None,
        probe=lambda path: calls.append(path) or successful_probe(path),
    )

    locator.locate()
    locator.locate()
    locator.locate(force=True)
    locator.reset()
    locator.locate()

    assert len(calls) == 3
