from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app import cloud_files
from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.browser_model import BrowserItem, BrowserItemKind, browser_item_from_scan_entry
from app.browser_scanner import BrowserScanEntry, scan_entry_from_dir_entry
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.image_source import FolderImageSource, FolderListingSnapshot, ImageSourceError, create_image_source
from app.viewer_window import ViewerWindow


def listing(folder, paths, states=None):
    ids = tuple(map(str, paths))
    return FolderListingSnapshot(
        folder, ids, ids[0], generation=23, sort_identity="random:seed=19",
        filter_identity="search=日本語", selected_index=0,
        image_availability=tuple(zip(ids, states if states is not None else [False] * len(ids))),
    )


def write_image(path, size=(16, 24), color="red"):
    with Image.new("RGB", size, color) as image:
        image.save(path)


@pytest.mark.parametrize("retry_attributes", [0, cloud_files.OFFLINE, None])
def test_scanner_final_metadata_and_default_unknown(tmp_path, retry_attributes):
    calls = []

    def stat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1 or retry_attributes is None:
            raise PermissionError("metadata unavailable")
        return SimpleNamespace(st_file_attributes=retry_attributes, st_mtime_ns=123, st_size=9)

    entry = SimpleNamespace(name="日本語.jpg", path=str(tmp_path / "日本語.jpg"),
                            stat=stat, is_dir=lambda **kw: False, is_file=lambda **kw: True)
    scanned = scan_entry_from_dir_entry(entry)
    item = browser_item_from_scan_entry(scanned)
    assert len(calls) == 2
    assert scanned.availability_known is (retry_attributes is not None)
    assert item.availability_known is scanned.availability_known
    assert item.online_only is bool(retry_attributes)
    assert not BrowserScanEntry(str(tmp_path / "legacy.jpg"), "legacy", "image", None, None).availability_known
    assert not BrowserItem("legacy", tmp_path / "legacy.jpg", BrowserItemKind.IMAGE, 0).availability_known


@pytest.mark.parametrize("count", [1, 600, 20000])
def test_known_local_open_has_constant_filesystem_work(tmp_path, monkeypatch, count):
    selected = tmp_path / "00000-日本語.jpg"
    selected.touch()
    paths = (str(selected),) + tuple(str(tmp_path / f"{i:05}-日本語.jpg") for i in range(1, count))
    snapshot = listing(tmp_path, paths)
    calls = {"lstat": [], "stat": []}
    real_lstat, real_stat = os.lstat, os.stat

    def counted(kind, original):
        def call(path, *args, **kwargs):
            # A slow/failed sibling probe is forbidden, independent of N.
            assert Path(path) in (tmp_path, selected), "known-local sibling was probed"
            calls[kind].append(str(path))
            return original(path, *args, **kwargs)
        return call

    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot admission must not enumerate or resolve")

    with monkeypatch.context() as patch:
        patch.setattr(os, "lstat", counted("lstat", real_lstat))
        patch.setattr(os, "stat", counted("stat", real_stat))
        patch.setattr(os, "scandir", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        source, target = create_image_source(selected, folder_snapshot=snapshot)
        assert target == str(selected)
        assert source.list_images() == list(paths)
        fork = source.fork_for_thumbnail()
        assert fork.list_images() == list(paths)
        assert fork is not source and fork._metadata_lock is not source._metadata_lock
        reloaded, target = create_image_source(selected, folder_snapshot=source.listing_snapshot)
        assert reloaded.list_images() == list(paths)
        assert reloaded.listing_snapshot is snapshot
        source.close()
        fork.close()
        reloaded.close()
    # Two opens plus a fork: only target/folder checks, with no N term.
    assert len(calls["lstat"]) == 5
    assert len(calls["stat"]) == 7


def test_only_unknown_and_cloud_subset_is_rechecked_and_can_recover(tmp_path, monkeypatch):
    paths = tuple(str(tmp_path / name) for name in ("local.jpg", "cloud.jpg", "unknown.jpg", "downloaded.jpg"))
    snapshot = listing(tmp_path, paths, (False, True, None, True))
    calls = []

    def online(path):
        calls.append(str(path))
        return str(path) == paths[1]

    monkeypatch.setattr(cloud_files, "is_online_only", online)
    source = FolderImageSource(tmp_path, image_snapshot=paths, listing_snapshot=snapshot)
    assert source.list_images() == [paths[0], paths[2], paths[3]]
    assert calls == [str(tmp_path), *paths[1:]]
    calls.clear()
    fork = source.fork_for_thumbnail()
    assert calls == [str(tmp_path)]
    assert fork.list_images() == source.list_images()


@pytest.mark.parametrize("bad", ["missing", "wrong_path", "wrong_folder", "wrong_images", "malformed", "invalid_bool", "conflicting_alias", "raw"])
def test_missing_malformed_or_mismatched_evidence_uses_fallback(tmp_path, monkeypatch, bad):
    path = str(tmp_path / "日本語.jpg")
    snapshot = listing(tmp_path, (path,))
    if bad == "missing":
        snapshot = replace(snapshot, image_availability=())
    elif bad == "wrong_path":
        snapshot = replace(snapshot, image_availability=((str(tmp_path / "other.jpg"), False),))
    elif bad == "wrong_folder":
        snapshot = replace(snapshot, folder=tmp_path / "elsewhere")
    elif bad == "wrong_images":
        snapshot = replace(snapshot, image_ids=("other.jpg",))
    elif bad == "malformed":
        snapshot = replace(snapshot, image_availability=((path, False, "extra"),))
    elif bad == "invalid_bool":
        snapshot = replace(snapshot, image_availability=((path, 0),))
    elif bad == "conflicting_alias":
        snapshot = replace(snapshot, image_availability=((path, False), (path.upper(), True), (path, False)))
    else:
        snapshot = None
    calls = []
    monkeypatch.setattr(cloud_files, "is_online_only", lambda p: calls.append(str(p)) or str(p) == path)
    source = FolderImageSource(tmp_path, image_snapshot=(path,), listing_snapshot=snapshot)
    assert source.list_images() == []
    assert calls == [str(tmp_path), path]


@pytest.mark.parametrize("change", ["cloud", "deleted", "unreadable"])
def test_captured_local_keeps_page_slot_but_read_guard_reports_failure(tmp_path, monkeypatch, change):
    first, sibling = tmp_path / "選択.png", tmp_path / "後続.png"
    write_image(first)
    write_image(sibling)
    snapshot = listing(tmp_path, (first, sibling))
    if change == "deleted":
        sibling.unlink()
    original = os.lstat

    def changed(path, *args, **kwargs):
        if Path(path) == sibling and change == "unreadable":
            raise PermissionError("unavailable metadata")
        info = original(path, *args, **kwargs)
        if Path(path) == sibling and change == "cloud":
            return SimpleNamespace(st_file_attributes=cloud_files.OFFLINE)
        return info

    monkeypatch.setattr(os, "lstat", changed)
    source, selected = create_image_source(first, folder_snapshot=snapshot)
    fork = source.fork_for_thumbnail()
    for current in (source, fork):
        assert current.list_images() == list(snapshot.image_ids)
        assert current.index_for_path(sibling) == 1
        with pytest.raises(ImageSourceError):
            current.open_image(str(sibling))
    source.close()
    with fork.open_image(str(first)) as image:
        assert image.size == (16, 24)
    assert selected == str(first)


@pytest.mark.parametrize("captured", [False, True, None])
def test_selected_cloud_is_never_replaced_and_downloaded_selected_recovers(tmp_path, monkeypatch, captured):
    first, selected = tmp_path / "first.png", tmp_path / "selected.png"
    write_image(first)
    write_image(selected)
    snapshot = listing(tmp_path, (first, selected), (False, captured))
    blocked = {selected}
    monkeypatch.setattr(cloud_files, "is_online_only", lambda p: Path(p) in blocked)
    with pytest.raises(ImageSourceError) as error:
        create_image_source(selected, folder_snapshot=snapshot)
    assert error.value.code == "online_only"
    blocked.clear()
    source, target = create_image_source(selected, folder_snapshot=snapshot)
    assert target == str(selected) and source.index_for_path(target) == 1


@pytest.mark.parametrize("suffix,method", [
    (".png", "open_image"), (".png", "logical_size"), (".png", "probe_image_size"),
    (".gif", "probe_image_is_animated"), (".webp", "open_qimage"),
    (".webp", "open_static_webp_qimage"), (".jpg", "open_qimage_at_most"),
    (".jpg", "open_compatible_jpeg_at_most"), (".svg", "logical_size"),
    (".psd", "logical_size"), (".kra", "open_image"),
])
def test_known_local_evidence_never_authorizes_payload_read(tmp_path, monkeypatch, suffix, method):
    import builtins

    path = tmp_path / ("later-cloud" + suffix)
    path.touch()
    snapshot = listing(tmp_path, (path,))
    source = FolderImageSource(tmp_path, image_snapshot=snapshot.image_ids, listing_snapshot=snapshot)
    monkeypatch.setattr(cloud_files, "is_online_only", lambda p: Path(p) == path)
    payload_attempts = []

    def forbidden(*args, **kwargs):
        payload_attempts.append(args)
        raise AssertionError("cloud placeholder payload must not be opened")

    monkeypatch.setattr(builtins, "open", forbidden)
    args = (str(path), (32, 32)) if method.endswith("at_most") else (str(path),)
    try:
        result = getattr(source, method)(*args)
    except (ImageSourceError, cloud_files.OnlineOnlyError):
        pass
    else:
        assert result is None
    assert payload_attempts == []


@pytest.mark.parametrize("use_cover", [False, True])
def test_unc_lexical_order_duplicates_and_cover_exclusion(monkeypatch, use_cover):
    from app.folder_cover import NIVIS_COVER_NAME, ZIPPLA_COVER_NAME

    folder = Path(r"\\server\share\日本語")
    ids = tuple(str(folder / name) for name in ("Z.png", "a.png", "A.PNG", "Z.png", NIVIS_COVER_NAME, ZIPPLA_COVER_NAME))
    snapshot = listing(folder, ids)
    probes = []
    monkeypatch.setattr(cloud_files, "is_online_only", lambda p: probes.append(str(p)) or False)
    monkeypatch.setattr(Path, "is_dir", lambda p: p == folder)

    def forbidden(*args, **kwargs):
        raise AssertionError("lexical UNC fixture must not access a share")

    with monkeypatch.context() as patch:
        patch.setattr(os, "lstat", forbidden)
        patch.setattr(os, "stat", forbidden)
        patch.setattr(os, "scandir", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        source = FolderImageSource(folder, image_snapshot=ids, listing_snapshot=snapshot, use_zippla_cover=use_cover)
        expected = list(ids[:4]) + ([] if use_cover else [ids[5]])
        assert source.list_images() == expected
        assert source.index_for_path(folder / "A.png") == 1
        assert source.index_for_path(ids[0]) == 0
        assert source.fork_for_thumbnail().list_images() == expected
    assert probes == [str(folder), str(folder)]


def test_missing_selected_never_falls_back_to_another_image(tmp_path):
    first, selected = tmp_path / "first.png", tmp_path / "missing.png"
    write_image(first)
    snapshot = listing(tmp_path, (first, selected))
    with pytest.raises((ImageSourceError, FileNotFoundError)):
        create_image_source(selected, folder_snapshot=snapshot)


@pytest.mark.parametrize("captured", [True, None])
@pytest.mark.parametrize("change", ["cloud", "missing", "unreadable"])
def test_selected_becoming_unavailable_during_admission_never_opens_another_page(
    tmp_path, monkeypatch, captured, change,
):
    first, selected = tmp_path / "first.png", tmp_path / "selected.png"
    write_image(first)
    write_image(selected)
    snapshot = listing(tmp_path, (first, selected), (False, captured))
    selected_probes = []

    def changing(path):
        if Path(path) != selected:
            return False
        selected_probes.append(str(path))
        if len(selected_probes) == 1:
            return False  # Requested-target guard succeeded just before admission.
        if change == "missing":
            raise FileNotFoundError("removed during open")
        if change == "unreadable":
            raise PermissionError("unreadable during open")
        return True

    monkeypatch.setattr(cloud_files, "is_online_only", changing)
    with pytest.raises(ImageSourceError):
        create_image_source(selected, folder_snapshot=snapshot)
    assert len(selected_probes) == 2


@pytest.mark.parametrize("recursive", [False, True])
def test_direct_and_recursive_opens_keep_fresh_cloud_filtering(tmp_path, monkeypatch, recursive):
    selected, cloud = tmp_path / "selected.png", tmp_path / "cloud.png"
    write_image(selected)
    write_image(cloud)
    snapshot = listing(tmp_path, (cloud, selected))
    probes = []
    monkeypatch.setattr(cloud_files, "is_online_only", lambda p: probes.append(str(p)) or Path(p) == cloud)
    source, target = create_image_source(selected, recursive_folder=recursive,
                                         folder_snapshot=snapshot if recursive else None)
    assert target == str(selected)
    assert source.list_images() == [str(selected)]
    assert str(cloud) in probes
    assert source.listing_snapshot is None


def test_evidence_does_not_supply_cached_bytes(tmp_path):
    path = tmp_path / "changed.png"
    write_image(path)
    snapshot = listing(tmp_path, (path,))
    source, _ = create_image_source(path, folder_snapshot=snapshot)
    write_image(path, (31, 47), "blue")
    with source.open_image(str(path)) as image:
        assert image.size == (31, 47) and image.getpixel((0, 0)) == (0, 0, 255)


def test_both_browser_snapshot_routes_preserve_evidence_without_io(tmp_path, qapp, monkeypatch):
    window = BrowserWindow(config_manager=ConfigManager(tmp_path / "config.json"), restore_initial_location=False)
    window.current_path = tmp_path
    items = tuple(BrowserItem(name, tmp_path / name, BrowserItemKind.IMAGE, 0,
                             online_only=cloud, availability_known=known)
                  for name, cloud, known in (("日本語.png", False, True), ("cloud.png", True, True), ("unknown.png", False, False)))
    try:
        def forbidden(*args, **kwargs):
            raise AssertionError("Browser snapshot capture must not touch filesystem")
        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", forbidden)
            patch.setattr(os, "lstat", forbidden)
            patch.setattr(os, "scandir", forbidden)
            patch.setattr(Path, "resolve", forbidden)
            direct = window._folder_snapshot_for_item(items[0], snapshot_items=items)
            mixed = window.adjacent_book_snapshot(tmp_path, snapshot_items=items)
            projected = ApplicationController._folder_snapshot_from_browser_navigation(mixed, str(items[0].path))
        expected = tuple((str(item.path), state) for item, state in zip(items, (False, True, None)))
        assert direct.image_availability == mixed.image_availability == projected.image_availability == expected
        assert projected.image_availability is mixed.image_availability
    finally:
        window.close()
        qapp.processEvents()


def test_reload_passes_original_evidence_and_async_session_retains_selection(tmp_path, qapp, monkeypatch):
    first, selected = tmp_path / "日本語.png", tmp_path / "selected.png"
    write_image(first)
    write_image(selected)
    snapshot = listing(tmp_path, (selected, first, selected))
    calls = []
    monkeypatch.setattr(cloud_files, "is_online_only", lambda p: calls.append(str(p)) or False)
    session = BookSession()
    try:
        session.open_book_async(selected, folder_snapshot=snapshot)
        assert session.wait_for_async(3000)
        qapp.processEvents()
        assert session.model.image_ids == list(snapshot.image_ids)
        assert session.model.focused_index == 0
        forwarded = []
        viewer = SimpleNamespace(_opened_path=selected, model=session.model, book_session=session,
                                 _browser_navigation_snapshot=None, open_path=lambda *a, **kw: forwarded.append((a, kw)))
        ViewerWindow.reload_current_book(viewer)
        assert forwarded[0][1]["folder_snapshot"] is snapshot
        calls.clear()
        session.open_book_async(selected, folder_snapshot=forwarded[0][1]["folder_snapshot"])
        assert session.wait_for_async(3000)
        qapp.processEvents()
        assert str(first) not in calls
        assert session.model.image_ids == list(snapshot.image_ids)
        assert session.model.focused_index == 0
    finally:
        session.shutdown()
