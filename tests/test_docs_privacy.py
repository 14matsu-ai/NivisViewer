from pathlib import Path

import pytest

from scripts.check_docs_privacy import contains_private_information, find_private_paths


@pytest.mark.parametrize("private_path", [
    r"C:\Users\example-user\project",
    "C:/Users/example-user/project",
    r"C:\\Users\\example-user\\project",
    "/home/example-user/project",
    "/Users/example-user/project",
])
def test_docs_privacy_reports_location_without_returning_private_values(tmp_path, private_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "record.md").write_text(f"Build record\n`{private_path}`\n", encoding="utf-8")
    assert find_private_paths(tmp_path) == [("docs/record.md", 2)]


def test_repository_documentation_contains_no_absolute_user_paths():
    assert find_private_paths(Path(__file__).resolve().parents[1]) == []


@pytest.mark.parametrize("masked_path", [
    "C:/Users/User/project", r"C:\Users\User\project",
    "C:/Users/<username>/project", "/home/<user>/project",
])
def test_masked_user_paths_are_allowed(masked_path):
    assert not contains_private_information(masked_path)


def test_root_readme_is_checked(tmp_path):
    (tmp_path / "README.md").write_text("C:/Users/private-owner/project", encoding="utf-8")
    assert find_private_paths(tmp_path) == [("README.md", 1)]


def test_local_username_without_directory_is_rejected(monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/home/private-owner"))
    assert contains_private_information("Build performed by private-owner")
