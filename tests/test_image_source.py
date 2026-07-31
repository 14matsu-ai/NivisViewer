from __future__ import annotations

import zipfile
from pathlib import Path
from threading import Event, Thread

import pytest
from PIL import Image

from app.archive_backend import ArchiveErrorCode
from app.image_source import (
    FolderImageSource,
    FolderListingSnapshot,
    ImageSourceError,
    ZipImageSource,
    create_image_source,
)


def write_image(path: Path, *, size: tuple[int, int] = (8, 12)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, "white") as image:
        image.save(path)


def test_folder_lists_only_supported_images_in_natural_order(tmp_path: Path) -> None:
    for name in ("10.jpg", "2.jpg", "1.jpg", "cover.png"):
        write_image(tmp_path / name)
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")

    source = FolderImageSource(tmp_path)

    assert [Path(image_id).name for image_id in source.list_images()] == [
        "1.jpg",
        "2.jpg",
        "10.jpg",
        "cover.png",
    ]


def test_folder_jpeg_target_decode_preserves_logical_size(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "日本語 大画像.jpg"
    write_image(image_path, size=(4096, 6500))
    source = FolderImageSource(tmp_path)

    decoded = source.open_qimage_at_most(
        str(image_path),
        (1361, 2160),
    )

    assert decoded is not None
    image, logical_size = decoded
    assert logical_size == (4096, 6500)
    assert (image.width(), image.height()) == (1361, 2160)


def test_folder_jpeg_target_decode_applies_exif_orientation(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "回転 大画像.jpg"
    exif = Image.Exif()
    exif[274] = 6
    with Image.new("RGB", (1200, 800), "white") as image:
        image.save(image_path, "JPEG", exif=exif)
    source = FolderImageSource(tmp_path)

    decoded = source.open_qimage_at_most(
        str(image_path),
        (400, 600),
    )

    assert decoded is not None
    image, logical_size = decoded
    assert logical_size == (800, 1200)
    assert (image.width(), image.height()) == (400, 600)


def test_folder_nonrecursive_listing_uses_scandir_without_path_file_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "日本語 1.jpg"
    write_image(image)
    source = FolderImageSource(tmp_path)

    def unexpected_path_probe(*_args, **_kwargs):
        raise AssertionError("non-recursive listing must use DirEntry metadata")

    monkeypatch.setattr(Path, "iterdir", unexpected_path_probe)
    monkeypatch.setattr(Path, "is_file", unexpected_path_probe)

    assert source.list_images() == [str(image)]


def test_folder_recursive_listing_contract_is_unchanged(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    nested = tmp_path / "章" / "2.jpg"
    write_image(cover)
    write_image(nested)

    flat_source = FolderImageSource(tmp_path)
    recursive_source = FolderImageSource(tmp_path, recursive=True)

    assert flat_source.list_images() == [str(cover)]
    assert recursive_source.list_images() == [str(cover), str(nested)]


def test_folder_scandir_skips_entry_removed_during_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FolderImageSource(tmp_path)
    existing = tmp_path / "2.jpg"

    class FakeEntry:
        def __init__(
            self,
            path: Path,
            *,
            error: OSError | None = None,
        ) -> None:
            self.name = path.name
            self.path = str(path)
            self._error = error

        def is_file(self, *, follow_symlinks: bool) -> bool:
            assert follow_symlinks is True
            if self._error is not None:
                raise self._error
            return True

    class FakeScandir:
        def __enter__(self):
            return iter(
                (
                    FakeEntry(
                        tmp_path / "1.jpg",
                        error=FileNotFoundError("removed"),
                    ),
                    FakeEntry(
                        tmp_path / "restricted.jpg",
                        error=PermissionError("denied"),
                    ),
                    FakeEntry(existing),
                )
            )

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "app.image_source.os.scandir",
        lambda _folder: FakeScandir(),
    )

    assert source.list_images() == [str(existing)]


def test_folder_scandir_error_remains_an_image_source_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FolderImageSource(tmp_path)

    def denied(_folder):
        raise PermissionError("denied")

    monkeypatch.setattr("app.image_source.os.scandir", denied)

    with pytest.raises(ImageSourceError, match="フォルダを読み込めません"):
        source.list_images()


def test_single_image_opens_parent_folder_and_returns_selection(tmp_path: Path) -> None:
    selected = tmp_path / "日本語2.jpg"
    write_image(tmp_path / "日本語1.jpg")
    write_image(selected)

    source, selected_image = create_image_source(selected)

    assert isinstance(source, FolderImageSource)
    assert source.source_path == tmp_path
    assert selected_image == str(selected)
    assert selected_image in source.list_images()


def test_folder_snapshot_file_size_does_not_restat_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "1.jpg"
    selected = tmp_path / "2.jpg"
    write_image(first)
    write_image(selected)
    first_size = first.stat().st_size
    selected_size = selected.stat().st_size
    snapshot = FolderListingSnapshot(
        tmp_path,
        (str(first), str(selected)),
        str(selected),
        (
            (str(first), first_size, first.stat().st_mtime_ns),
            (str(selected), selected_size, selected.stat().st_mtime_ns),
        ),
    )
    source, selected_image = create_image_source(
        selected,
        folder_snapshot=snapshot,
    )

    def unexpected_stat(*_args, **_kwargs):
        raise AssertionError("snapshot file size must not be restated")

    monkeypatch.setattr(Path, "stat", unexpected_stat)

    assert selected_image == str(selected)
    assert source.file_size(str(first)) == first_size
    assert source.file_size(str(selected)) == selected_size
    source.close()


def test_folder_loaded_bytes_supply_file_size_without_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    expected_size = image_path.stat().st_size
    source = FolderImageSource(
        tmp_path,
        image_snapshot=(str(image_path),),
    )
    assert source.file_size(str(image_path)) is None

    def unexpected_stat(*_args, **_kwargs):
        raise AssertionError("loaded byte count must not require stat")

    monkeypatch.setattr(Path, "stat", unexpected_stat)
    with source.open_image(str(image_path)):
        pass

    assert source.file_size(str(image_path)) == expected_size
    source.close()


def test_zip_lists_images_in_subfolders_and_ignores_other_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image1 = tmp_path / "1.jpg"
    image2 = tmp_path / "10.jpg"
    write_image(image1)
    write_image(image2)
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image2, "chapter/10.jpg")
        output.writestr("chapter/readme.txt", "ignore")
        output.write(image1, "chapter/2/1.jpg")

    source = ZipImageSource(archive)
    infolist_calls = 0
    original_infolist = source._zip.infolist

    def counted_infolist():
        nonlocal infolist_calls
        infolist_calls += 1
        return original_infolist()

    monkeypatch.setattr(source._zip, "infolist", counted_infolist)
    try:
        assert source.list_images() == ["chapter/2/1.jpg", "chapter/10.jpg"]
        assert source.list_images() == ["chapter/2/1.jpg", "chapter/10.jpg"]
        assert infolist_calls == 1
        with source.open_image("chapter/2/1.jpg") as image:
            assert image.size == (8, 12)
    finally:
        source.close()


def test_zip_jpeg_target_decode_uses_display_bound(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "source.jpg"
    write_image(image_path, size=(4096, 6500))
    archive = tmp_path / "日本語 大画像.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image_path, "ページ/001.jpg")
    source = ZipImageSource(archive)
    try:
        decoded = source.open_qimage_at_most(
            "ページ/001.jpg",
            (1361, 2160),
        )
    finally:
        source.close()

    assert decoded is not None
    image, logical_size = decoded
    assert logical_size == (4096, 6500)
    assert (image.width(), image.height()) == (1361, 2160)


def test_zip_running_entry_read_can_be_cancelled_between_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path, size=(64, 96))
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image_path, "page.jpg")

    source = ZipImageSource(archive)
    original_open = source._zip.open
    read_started = Event()
    release_read = Event()

    class BlockingEntry:
        def __init__(self, entry) -> None:
            self._entry = entry
            self._blocked = False

        def __enter__(self):
            self._entry.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self._entry.__exit__(exc_type, exc, traceback)

        def read(self, size: int = -1) -> bytes:
            chunk = self._entry.read(size)
            if chunk and not self._blocked:
                self._blocked = True
                read_started.set()
                assert release_read.wait(2)
            return chunk

    def blocking_open(*args, **kwargs):
        return BlockingEntry(original_open(*args, **kwargs))

    monkeypatch.setattr(source._zip, "open", blocking_open)
    error_codes: list[str | None] = []

    def load() -> None:
        try:
            with source.open_image("page.jpg"):
                pass
        except ImageSourceError as exc:
            error_codes.append(exc.code)

    worker = Thread(target=load)
    worker.start()
    try:
        assert read_started.wait(1)
        source.cancel_image_request("page.jpg")
    finally:
        release_read.set()
    worker.join(2)
    try:
        assert not worker.is_alive()
        assert error_codes == [
            ArchiveErrorCode.PROCESS_CANCELLED.value
        ]
        assert source._active_requests == {}
    finally:
        source.close()


def test_corrupt_zip_raises_image_source_error(tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"this is not a zip file")

    with pytest.raises(ImageSourceError):
        ZipImageSource(archive)
