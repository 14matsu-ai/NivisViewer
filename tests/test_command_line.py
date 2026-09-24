from __future__ import annotations

import pytest

from app.command_line import parse_command_line, version_text


def test_paths_support_quotes_unicode_relative_and_deduplication(tmp_path):
    options = parse_command_line(
        ['"日本語 folder\\book 1.cbz"', "日本語 folder\\book 1.cbz", r"\\server\share\本.pdf"],
        launch_directory=tmp_path,
    )

    assert len(options.paths) == 2
    assert options.paths[0].endswith(r"日本語 folder\book 1.cbz")
    assert options.paths[1] == r"\\server\share\本.pdf"


def test_request_only_flags_are_parsed_without_mutating_settings(tmp_path):
    options = parse_command_line(
        [
            "--reuse",
            "--browser-only",
            "--no-restore",
            "--no-single-instance",
            "--profile-dir",
            "profile",
            "book.pdf",
        ],
        launch_directory=tmp_path,
    )

    assert options.reuse
    assert options.browser_only
    assert options.no_restore
    assert options.no_single_instance
    assert options.profile_dir == str(tmp_path / "profile")


def test_new_window_and_reuse_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_command_line(["--new-window", "--reuse"])


def test_version_text_uses_central_version():
    assert version_text() == "NivisViewer 1.0.14"
