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
    jpeg_decode_target_size,
    qt_jpeg_compatible_request_size,
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


def test_qt_jpeg_planner_uses_smallest_sufficient_m_over_eight_tier() -> None:
    assert qt_jpeg_compatible_request_size((2500, 3500), (1429, 2000)) == (
        1562,
        2187,
    )
    assert qt_jpeg_compatible_request_size((2501, 3501), (1251, 1751)) == (
        1563,
        2188,
    )
    assert qt_jpeg_compatible_request_size((2501, 3501), (1250, 1750)) == (
        1250,
        1750,
    )


@pytest.mark.parametrize("orientation", [1, 5, 6, 7, 8])
def test_zip_jpeg_qt_output_matches_planner_with_exif_axis_swaps(
    tmp_path: Path,
    orientation: int,
) -> None:
    path = tmp_path / f"source-{orientation}.jpg"
    exif = Image.Exif()
    exif[274] = orientation
    with Image.new("RGB", (1001, 1501), (80, 120, 160)) as image:
        image.save(path, "JPEG", quality=85, exif=exif)
    archive = tmp_path / f"book-{orientation}.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(path, "ページ/001.jpg")

    source = ZipImageSource(archive)
    try:
        logical = source.probe_jpeg_size("ページ/001.jpg")
        assert logical is not None
        maximum = (500, 750)
        expected = source.estimate_compatible_jpeg_size(logical, maximum)
        decoded = source.open_compatible_jpeg_at_most(
            "ページ/001.jpg",
            maximum,
        )
    finally:
        source.close()

    assert decoded is not None
    image, logical_size = decoded.qimage, decoded.original_size
    assert logical_size == logical
    assert (image.width(), image.height()) == expected
    target = jpeg_decode_target_size(logical, maximum)
    assert image.width() >= target[0]
    assert image.height() >= target[1]


def test_zip_animation_probe_uses_bounded_header_read_before_decode(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "page.png"
    with Image.new("RGB", (64, 64), (40, 80, 120)) as image:
        image.save(image_path)
    archive = tmp_path / "probe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image_path, "page.png")

    class CountingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.full_entry_reads = 0

        def _read_entry_stream(self, image_id, cancelled):
            self.full_entry_reads += 1
            return super()._read_entry_stream(image_id, cancelled)

    source = CountingSource(archive)
    try:
        assert source.probe_image_is_animated("page.png") is False
        assert source.full_entry_reads == 0
        with source.open_image("page.png") as image:
            assert image.size == (64, 64)
        assert source.full_entry_reads == 1
    finally:
        source.close()


def test_zip_streamed_jpeg_decodes_without_full_payload_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "source.jpg"
    write_image(image_path, size=(1200, 800))
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(image_path, "ページ/001.jpg")

    source = ZipImageSource(archive)
    entry_open_calls = 0
    original_open = source._zip.open

    def counted_open(*args, **kwargs):
        nonlocal entry_open_calls
        entry_open_calls += 1
        return original_open(*args, **kwargs)

    def unexpected_materialization(*_args, **_kwargs):
        raise AssertionError("streamed JPEG decode must not materialize the payload")

    monkeypatch.setattr(source._zip, "open", counted_open)
    monkeypatch.setattr(source, "_read_entry_stream", unexpected_materialization)
    monkeypatch.setattr("app.image_source.Image.open", unexpected_materialization)
    try:
        decoded = source.open_streamed_jpeg_at_most(
            "ページ/001.jpg",
            (600, 600),
        )
        entry_size = source._zip.NameToInfo["ページ/001.jpg"].file_size
    finally:
        source.close()

    assert decoded is not None
    assert decoded.original_size == (1200, 800)
    assert (decoded.qimage.width(), decoded.qimage.height()) == (600, 400)
    assert 0 < decoded.bytes_read <= entry_size
    assert decoded.read_calls >= 1
    assert entry_open_calls == 1


def test_zip_compatible_jpeg_uses_one_qbytearray_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "source.jpg"
    write_image(image_path, size=(1200, 800))
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(image_path, "ページ/001.jpg")

    source = ZipImageSource(archive)
    entry_open_calls = 0
    original_open = source._zip.open

    def counted_open(*args, **kwargs):
        nonlocal entry_open_calls
        entry_open_calls += 1
        return original_open(*args, **kwargs)

    def unexpected_bytesio(*_args, **_kwargs):
        raise AssertionError("compatible JPEG decode must not build BytesIO")

    monkeypatch.setattr(source._zip, "open", counted_open)
    monkeypatch.setattr(source, "_read_entry_stream", unexpected_bytesio)
    try:
        decoded = source.open_compatible_jpeg_at_most(
            "ページ/001.jpg",
            (500, None),
        )
        entry_size = source._zip.NameToInfo["ページ/001.jpg"].file_size
    finally:
        source.close()

    assert decoded is not None
    assert decoded.backend == "qbytearray-qbuffer"
    assert decoded.full_payload_materializations == 1
    assert decoded.bytes_read == entry_size
    assert decoded.read_calls >= 2
    assert decoded.original_size == (1200, 800)
    # The compatible source is the smallest native JPEG tier still above the
    # 500px physical target, not an arbitrary decoder-resized final frame.
    assert (decoded.qimage.width(), decoded.qimage.height()) == (600, 400)
    assert source.estimate_compatible_jpeg_size(
        decoded.original_size,
        (500, None),
    ) == (600, 400)
    assert entry_open_calls == 1


def test_zip_compatible_jpeg_applies_exif_axis_swap(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    with Image.new("RGB", (1200, 800), "white") as image:
        image.save(image_path, "JPEG", exif=exif)
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(image_path, "rotated.jpg")

    source = ZipImageSource(archive)
    try:
        probed_size = source.probe_jpeg_size("rotated.jpg")
        decoded = source.open_compatible_jpeg_at_most(
            "rotated.jpg",
            (None, 600),
        )
    finally:
        source.close()

    assert decoded is not None
    assert probed_size == (800, 1200)
    assert decoded.original_size == (800, 1200)
    assert (decoded.qimage.width(), decoded.qimage.height()) == (400, 600)


def test_zip_generic_header_probe_reads_non_jpeg_dimensions(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "page.png"
    with Image.new("RGB", (321, 654), "navy") as image:
        image.save(image_path, "PNG")
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(image_path, "page.png")

    source = ZipImageSource(archive)
    try:
        assert source.probe_image_size("page.png") == (321, 654)
        assert not source._active_requests
    finally:
        source.close()


def test_folder_header_probe_avoids_pillow_sniffing_for_common_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "page.png"
    with Image.new("RGB", (321, 654), "navy") as image:
        image.save(image_path, "PNG")
    source = FolderImageSource(tmp_path)

    monkeypatch.setattr(
        Image,
        "open",
        lambda *_args, **_kwargs: pytest.fail(
            "Qt header probing must not enter Pillow's generic plugin scan"
        ),
    )

    assert source.probe_image_size(str(image_path)) == (321, 654)


def test_folder_decode_limits_malformed_png_to_its_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "broken.png"
    image_path.write_bytes(b"broken image")
    source = FolderImageSource(tmp_path)
    original_open = Image.open
    formats: list[tuple[str, ...] | None] = []

    def record_formats(*args, **kwargs):
        formats.append(kwargs.get("formats"))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", record_formats)

    with pytest.raises(ImageSourceError):
        source.open_image(str(image_path))

    assert formats == [("PNG",)]


def test_folder_decode_recognizes_a_common_mislabeled_image(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "jpeg_named_png.png"
    with Image.new("RGB", (13, 17), "navy") as image:
        image.save(image_path, "JPEG")
    source = FolderImageSource(tmp_path)

    with source.open_image(str(image_path)) as decoded:
        assert decoded.size == (13, 17)
        red, green, blue = decoded.getpixel((0, 0))
        assert blue > red and blue > green


def test_folder_header_probe_swaps_exif_rotated_axes_and_rejects_bad_headers(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    with Image.new("RGB", (1200, 800), "white") as image:
        image.save(image_path, "JPEG", exif=exif)
    broken_path = tmp_path / "broken.png"
    broken_path.write_bytes(b"broken image")
    source = FolderImageSource(tmp_path)

    assert source.probe_image_size(str(image_path)) == (800, 1200)
    assert source.probe_image_size(str(broken_path)) is None


def test_zip_streamed_jpeg_applies_exif_axis_swap(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    with Image.new("RGB", (1200, 800), "white") as image:
        image.save(image_path, "JPEG", exif=exif)
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(image_path, "rotated.jpg")

    source = ZipImageSource(archive)
    try:
        decoded = source.open_streamed_jpeg_at_most(
            "rotated.jpg",
            (400, 600),
        )
    finally:
        source.close()

    assert decoded is not None
    assert decoded.original_size == (800, 1200)
    assert (decoded.qimage.width(), decoded.qimage.height()) == (400, 600)


@pytest.mark.parametrize(
    "method_name",
    (
        "open_streamed_jpeg_at_most",
        "open_compatible_jpeg_at_most",
    ),
)
def test_zip_jpeg_fast_paths_validate_entry_size_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("page.jpg", b"x" * 64)

    source = ZipImageSource(archive)
    entry_open_calls = 0
    original_open = source._zip.open

    def counted_open(*args, **kwargs):
        nonlocal entry_open_calls
        entry_open_calls += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr(source._zip, "open", counted_open)
    monkeypatch.setattr("app.image_source.MAX_IMAGE_ENTRY_BYTES", 32)
    try:
        with pytest.raises(ImageSourceError) as exc_info:
            getattr(source, method_name)("page.jpg", (100, 100))
    finally:
        source.close()

    assert exc_info.value.code == ArchiveErrorCode.ENTRY_TOO_LARGE.value
    assert entry_open_calls == 0


@pytest.mark.parametrize(
    "method_name",
    (
        "open_streamed_jpeg_at_most",
        "open_compatible_jpeg_at_most",
    ),
)
def test_zip_jpeg_fast_paths_cancel_with_existing_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path, size=(1200, 800))
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
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

    monkeypatch.setattr(
        source._zip,
        "open",
        lambda *args, **kwargs: BlockingEntry(original_open(*args, **kwargs)),
    )
    error_codes: list[str | None] = []

    def load() -> None:
        try:
            getattr(source, method_name)("page.jpg", (600, 400))
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
        assert error_codes == [ArchiveErrorCode.PROCESS_CANCELLED.value]
        assert source._active_requests == {}
    finally:
        source.close()


@pytest.mark.parametrize(
    "method_name",
    (
        "open_streamed_jpeg_at_most",
        "open_compatible_jpeg_at_most",
    ),
)
def test_zip_close_defers_while_jpeg_fast_path_owns_archive_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path, size=(1200, 800))
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
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

    monkeypatch.setattr(
        source._zip,
        "open",
        lambda *args, **kwargs: BlockingEntry(original_open(*args, **kwargs)),
    )
    error_codes: list[str | None] = []

    def load() -> None:
        try:
            getattr(source, method_name)("page.jpg", (600, 400))
        except ImageSourceError as exc:
            error_codes.append(exc.code)

    worker = Thread(target=load)
    worker.start()
    assert read_started.wait(1)

    close_worker = Thread(target=source.close)
    close_worker.start()
    try:
        close_worker.join(0.5)
        assert not close_worker.is_alive()
        assert source._closed.is_set()
        assert not source._zip_closed
    finally:
        release_read.set()

    worker.join(2)
    close_worker.join(2)
    assert not worker.is_alive()
    assert not close_worker.is_alive()
    assert error_codes == [ArchiveErrorCode.PROCESS_CANCELLED.value]
    assert source._active_requests == {}
    assert source._zip_closed
    assert source._zip.fp is None


def test_zip_streamed_corrupt_jpeg_returns_none_for_legacy_fallback(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("broken.jpg", b"this is not a jpeg")

    source = ZipImageSource(archive)
    try:
        streamed = source.open_streamed_jpeg_at_most(
            "broken.jpg",
            (400, 400),
        )
        legacy = source.open_qimage_at_most(
            "broken.jpg",
            (400, 400),
        )
    finally:
        source.close()

    assert streamed is None
    assert legacy is None


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
