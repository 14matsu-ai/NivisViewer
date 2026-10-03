from scripts.publication_policy import find_publication_violations, publishable


def test_source_package_rejects_internal_records_and_review_archives(tmp_path):
    for name in ("main.py", "README.md", "AGENTS.md", "docs/PYTHON313_MIGRATION.md", "review.zip"):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    assert find_publication_violations(tmp_path) == ["AGENTS.md", "docs/PYTHON313_MIGRATION.md", "review.zip"]


def test_build_source_and_legal_provenance_remain_publishable():
    for name in ("app/viewer_widget.py", "scripts/build_portable.ps1", "LICENSE",
                 "docs/ZIPPLAFORK_COMPARISON.md", "licenses/ZipPlaFork/About.txt"):
        assert publishable(name)


def test_private_profiles_environments_and_new_internal_notes_are_excluded():
    for name in ("config.json", "data/favorites.json", ".venv313/pyvenv.cfg",
                 "out/report.json", "INTERNAL_NOTES.md", ".codex/settings.json"):
        assert not publishable(name)
