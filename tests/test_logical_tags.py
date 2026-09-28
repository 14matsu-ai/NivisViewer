from pathlib import Path

from app.browser_filter import BrowserFilterState
from app.browser_tag_dialogs import TagSelectionMenu
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_tags import (
    canonical_tag_key,
    expand_logical_tag_changes,
    normalize_tag_registry,
    unmanaged_tag_counts,
    visible_tags,
)


def _item(path: Path) -> BrowserItem:
    return BrowserItem(
        display_name=path.name,
        path=path,
        kind=BrowserItemKind.IMAGE,
        modified_at=None,
    )


def test_context_menu_can_remove_all_unmanaged_tags_without_closing(qapp):
    from PySide6.QtWidgets import QMenu

    root = QMenu()
    menu = TagSelectionMenu(
        ("a {zpi$t=managed,loose}.jpg",),
        [{"name": "managed", "color": "#80bfff"}],
        root,
    )
    menu._toggle(menu.remove_unknown_action)
    assert menu.changes()["loose"] is False
    assert not menu.cancelled


def test_legacy_registry_migrates_without_changing_filename_token():
    registry = normalize_tag_registry(
        [{"name": "AAA", "color": "#80BFFF"}]
    )
    assert len(registry) == 1
    entry = registry[0]
    assert entry["display_name"] == "AAA"
    assert entry["write_token"] == "AAA"
    assert entry["tokens"] == ["AAA"]
    assert entry["name"] == "AAA"
    assert str(entry["id"]).startswith("legacy-")


def test_legacy_id_is_stable_across_normalization():
    raw = [{"name": "AAA", "color": "#80bfff"}]
    assert normalize_tag_registry(raw)[0]["id"] == normalize_tag_registry(raw)[0]["id"]


def test_display_name_can_change_without_changing_tokens():
    first = normalize_tag_registry(
        [{"name": "AAA", "color": "#80bfff"}]
    )[0]
    changed = dict(first)
    changed["display_name"] = "表示だけ変更"
    changed["name"] = "表示だけ変更"

    normalized = normalize_tag_registry([changed])[0]

    assert normalized["id"] == first["id"]
    assert normalized["display_name"] == "表示だけ変更"
    assert normalized["write_token"] == "AAA"
    assert normalized["tokens"] == ["AAA"]


def test_multiple_physical_tokens_resolve_to_one_logical_tag(tmp_path):
    path = tmp_path / "page {zpi$t=old_tag}.jpg"
    registry = normalize_tag_registry(
        [
            {
                "id": "tag-one",
                "display_name": "表示名",
                "write_token": "new_tag",
                "tokens": ["new_tag", "old_tag"],
                "color": "#80bfff",
            }
        ]
    )

    assert visible_tags(str(path), registry) == [
        {"name": "表示名", "color": "#80bfff"}
    ]
    state = BrowserFilterState.normalized(
        include_tags=("new_tag",),
        tag_registry=registry,
    )
    assert state.matches(_item(path))


def test_model_preserves_logical_token_mapping(qapp, tmp_path):
    del qapp
    path = tmp_path / "page {zpi$t=old_tag}.jpg"
    registry = normalize_tag_registry(
        [
            {
                "id": "tag-one",
                "display_name": "表示名",
                "write_token": "new_tag",
                "tokens": ["new_tag", "old_tag"],
                "color": "#80bfff",
            }
        ]
    )
    model = BrowserItemModel()
    model.set_items([_item(path)])
    state = BrowserFilterState.normalized(
        include_tags=("new_tag",),
        tag_registry=registry,
    )
    assert model.configure_filter(state)
    assert model.rowCount() == 1
    assert model.filter_state.tag_token_groups == state.tag_token_groups


def test_physical_token_matching_remains_case_sensitive(tmp_path):
    path = tmp_path / "page {zpi$t=Read}.jpg"
    registry = normalize_tag_registry(
        [
            {
                "id": "tag-one",
                "display_name": "read",
                "write_token": "read",
                "tokens": ["read"],
                "color": "#80bfff",
            }
        ]
    )
    assert visible_tags(str(path), registry) == []
    assert unmanaged_tag_counts((path,), registry) == {"Read": 1}


def test_logical_tag_off_removes_all_physical_tokens():
    registry = normalize_tag_registry(
        [
            {
                "id": "tag-one",
                "display_name": "表示",
                "write_token": "new",
                "tokens": ["new", "old"],
                "color": "#80bfff",
            }
        ]
    )
    assert expand_logical_tag_changes(
        ("old", "other"),
        {"new": False},
        registry,
    ) == {"old": False}


def test_logical_tag_on_does_not_duplicate_secondary_token():
    registry = normalize_tag_registry(
        [
            {
                "id": "tag-one",
                "display_name": "表示",
                "write_token": "new",
                "tokens": ["new", "old"],
                "color": "#80bfff",
            }
        ]
    )
    assert expand_logical_tag_changes(
        ("old",),
        {"new": True},
        registry,
    ) == {}


def test_logical_tag_on_uses_standard_token_for_new_assignment():
    registry = normalize_tag_registry(
        [
            {
                "id": "tag-one",
                "display_name": "表示",
                "write_token": "new",
                "tokens": ["new", "old"],
                "color": "#80bfff",
            }
        ]
    )
    assert expand_logical_tag_changes(
        (),
        {"new": True},
        registry,
    ) == {"new": True}


def test_unmanaged_counts_exclude_every_registered_physical_token(tmp_path):
    paths = (
        tmp_path / "a {zpi$t=main,old,loose}.jpg",
        tmp_path / "b {zpi$t=loose}.jpg",
    )
    registry = [
        {
            "id": "tag-one",
            "display_name": "表示",
            "write_token": "main",
            "tokens": ["main", "old"],
            "color": "#80bfff",
        }
    ]

    assert unmanaged_tag_counts(paths, registry) == {"loose": 2}
    assert canonical_tag_key("old", registry) == "main"
