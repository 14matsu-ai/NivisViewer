from __future__ import annotations

from pathlib import Path

from app.seven_zip_locator import SevenZipInfo, SevenZipLocator


def successful_probe(path: str) -> SevenZipInfo:
    return SevenZipInfo(path, True, "7-Zip 26.00", None)


def test_explicit_path_has_priority_over_automatic_candidates(tmp_path: Path) -> None:
    explicit = tmp_path / "manual" / "7z.exe"
    automatic = tmp_path / "7z.exe"
    explicit.parent.mkdir()
    explicit.write_bytes(b"manual")
    automatic.write_bytes(b"automatic")
    probes: list[str] = []

    locator = SevenZipLocator(
        application_dir=tmp_path,
        environment={},
        which=lambda _name: None,
        probe=lambda path: (
            probes.append(path) or successful_probe(path)
        ),
    )

    info = locator.locate(explicit)

    assert info.available
    assert info.executable_path == str(explicit)
    assert probes == [str(explicit)]


def test_invalid_explicit_path_does_not_silently_fall_back(tmp_path: Path) -> None:
    automatic = tmp_path / "7z.exe"
    automatic.write_bytes(b"automatic")
    locator = SevenZipLocator(
        application_dir=tmp_path,
        environment={},
        which=lambda _name: None,
        probe=successful_probe,
    )

    info = locator.locate(tmp_path / "missing" / "7z.exe")

    assert not info.available
    assert info.executable_path.endswith("missing\\7z.exe")


def test_auto_detection_checks_app_then_path_and_supports_7zz(tmp_path: Path) -> None:
    path_candidate = tmp_path / "bin" / "7zz.exe"
    path_candidate.parent.mkdir()
    path_candidate.write_bytes(b"fake")
    names: list[str] = []

    def which(name: str) -> str | None:
        names.append(name)
        return str(path_candidate) if name == "7zz.exe" else None

    locator = SevenZipLocator(
        application_dir=tmp_path / "app",
        environment={},
        which=which,
        probe=successful_probe,
    )

    info = locator.locate()

    assert info.available
    assert info.executable_path == str(path_candidate)
    assert names == ["7z.exe", "7zz.exe"]


def test_probe_failure_continues_to_next_automatic_candidate(tmp_path: Path) -> None:
    first = tmp_path / "7z.exe"
    second = tmp_path / "tools" / "7-Zip" / "7zz.exe"
    first.write_bytes(b"bad")
    second.parent.mkdir(parents=True)
    second.write_bytes(b"good")

    locator = SevenZipLocator(
        application_dir=tmp_path,
        environment={},
        which=lambda _name: None,
        probe=lambda path: SevenZipInfo(
            path,
            path == str(second),
            "7-Zip" if path == str(second) else None,
            None if path == str(second) else "invalid",
        ),
    )

    assert locator.locate().executable_path == str(second)


def test_results_are_cached_and_force_or_reset_redetects(tmp_path: Path) -> None:
    executable = tmp_path / "7z.exe"
    executable.write_bytes(b"fake")
    calls: list[str] = []
    locator = SevenZipLocator(
        application_dir=tmp_path,
        environment={},
        which=lambda _name: None,
        probe=lambda path: (
            calls.append(path) or successful_probe(path)
        ),
    )

    locator.locate()
    locator.locate()
    locator.locate(force=True)
    locator.reset()
    locator.locate()

    assert len(calls) == 3


def test_no_candidates_is_nonfatal(tmp_path: Path) -> None:
    info = SevenZipLocator(
        application_dir=tmp_path,
        environment={},
        which=lambda _name: None,
        probe=successful_probe,
    ).locate()

    assert not info.available
    assert info.error_message
