from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.image_source import (
    FolderImageSource,
    FolderListingSnapshot,
    ImageSource,
    ImageSourceError,
)


def write_image(path: Path, *, size: tuple[int, int] = (8, 12)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, "white") as image:
        image.save(path)


def test_initial_state_has_no_open_book() -> None:
    session = BookSession()

    assert not session.is_open
    assert session.current_path is None
    assert session.source is None
    assert session.model.total_pages == 0


def test_open_folder_sets_source_model_and_current_path(tmp_path: Path) -> None:
    write_image(tmp_path / "1.jpg")
    session = BookSession()

    opened = session.open_book(tmp_path)

    assert session.is_open
    assert isinstance(session.source, FolderImageSource)
    assert session.current_path == tmp_path
    assert session.model.total_pages == 1
    assert opened.source_path == tmp_path
    session.shutdown()


def test_open_single_image_uses_parent_and_selected_start_page(tmp_path: Path) -> None:
    write_image(tmp_path / "1.jpg")
    selected = tmp_path / "2.jpg"
    write_image(selected)
    write_image(tmp_path / "3.jpg")
    session = BookSession()

    opened = session.open_book(selected)

    assert session.current_path == selected
    assert session.source is not None
    assert session.source.source_path == tmp_path
    assert opened.selected_image == str(selected)
    assert session.model.current_index == 1
    assert str(selected) in session.model.image_ids
    session.shutdown()


def test_switching_books_updates_generation(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_image(first / "1.jpg")
    write_image(second / "1.jpg")
    session = BookSession()

    session.open_book(first)
    first_generation = session.generation
    first_cache_generation = session.image_cache.generation
    session.open_book(second)

    assert session.generation > first_generation
    assert session.image_cache.generation > first_cache_generation
    assert session.current_path == second
    session.shutdown()


def test_close_book_clears_state(tmp_path: Path) -> None:
    write_image(tmp_path / "1.jpg")
    session = BookSession()
    session.open_book(tmp_path)

    session.close_book()

    assert not session.is_open
    assert session.current_path is None
    assert session.source is None
    assert session.model.source is None
    assert session.model.image_ids == []
    assert session.image_cache.source is None


def test_invalid_path_preserves_current_book(tmp_path: Path) -> None:
    valid = tmp_path / "valid"
    write_image(valid / "1.jpg")
    session = BookSession()
    session.open_book(valid)
    original_source = session.source
    original_images = list(session.model.image_ids)

    with pytest.raises(ImageSourceError):
        session.open_book(tmp_path / "missing")

    assert session.source is original_source
    assert session.current_path == valid
    assert session.model.image_ids == original_images
    session.shutdown()


def test_same_session_can_switch_books_repeatedly(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("一", "二", "三")]
    for path in paths:
        write_image(path / "1.jpg")
    session = BookSession()

    for path in paths + [paths[0]]:
        session.open_book(path)
        assert session.current_path == path
        assert session.model.total_pages == 1

    session.shutdown()


def test_same_zip_can_be_reopened_quickly(tmp_path: Path) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image_path, "page.jpg")
    session = BookSession()

    session.open_book(archive)
    first_source = session.source
    session.open_book(archive)

    assert session.current_path == archive
    assert session.source is not first_source
    assert session.model.image_ids == ["page.jpg"]
    session.shutdown()


class BlockingImageSource(ImageSource):
    def __init__(
        self,
        source_path: Path,
        started: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(source_path)
        self.started = started
        self.release = release
        self.closed = False

    def list_images(self) -> list[str]:
        return ["page.png"]

    def open_image(self, image_id: str) -> Image.Image:
        self.started.set()
        if not self.release.wait(2):
            raise ImageSourceError("test image load timed out")
        return Image.new("RGB", (8, 12), "white")

    def display_path(self, image_id: str) -> str:
        return image_id

    def close(self) -> None:
        self.closed = True


def test_source_close_is_deferred_until_old_load_finishes(qapp: QApplication) -> None:
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()
    second_release = threading.Event()
    second_release.set()
    sources = {
        "first": BlockingImageSource(Path("first"), first_started, first_release),
        "second": BlockingImageSource(Path("second"), second_started, second_release),
    }

    def source_factory(path: Path, **_kwargs: object) -> tuple[ImageSource, str | None]:
        return sources[path.name], None

    session = BookSession(source_factory=source_factory)
    delivered_pages: list[str] = []
    session.image_cache.pageLoaded.connect(lambda cached: delivered_pages.append(cached.image_id))
    session.open_book("first")
    session.image_cache.ensure_loaded(0)
    assert first_started.wait(1)

    session.open_book("second")

    assert not sources["first"].closed
    first_release.set()
    assert session.image_cache.wait_for_done(2000)
    qapp.processEvents()
    assert sources["first"].closed
    assert delivered_pages == []
    session.shutdown()


def test_cancel_pending_open_releases_queued_worker_tracking(
    qapp: QApplication,
) -> None:
    running_started = threading.Event()
    release_running = threading.Event()
    factory_calls: list[str] = []

    def source_factory(path: Path, **_kwargs: object) -> tuple[ImageSource, str | None]:
        factory_calls.append(path.name)
        if path.name == "running":
            running_started.set()
            assert release_running.wait(2)
        source = BlockingImageSource(path, threading.Event(), threading.Event())
        return source, None

    session = BookSession(source_factory=source_factory)
    opened_generations: list[int] = []
    failed_generations: list[int] = []
    session.async_opened.connect(
        lambda opened: opened_generations.append(opened.generation)
    )
    session.async_open_failed.connect(
        lambda failed: failed_generations.append(failed.generation)
    )
    running_generation = session.open_book_async("running")
    assert running_started.wait(1)
    queued_generation = session.open_book_async("queued")
    assert set(session._open_workers) == {
        running_generation,
        queued_generation,
    }

    session.cancel_pending_open()
    session.cancel_pending_open()

    assert running_generation in session._open_workers
    assert queued_generation not in session._open_workers
    assert factory_calls == ["running"]

    release_running.set()
    assert session.wait_for_async(2000)
    qapp.processEvents()
    assert session._open_workers == {}

    session.open_book_async("queued")
    assert session.wait_for_async(2000)
    qapp.processEvents()
    assert factory_calls == ["running", "queued"]
    assert session._open_workers == {}
    assert opened_generations == [session.generation]
    assert failed_generations == []
    session.shutdown()


def test_async_folder_snapshot_avoids_directory_relisting(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "1.jpg"
    selected = tmp_path / "2.jpg"
    write_image(first)
    write_image(selected)
    snapshot = FolderListingSnapshot(
        tmp_path,
        (str(first), str(selected)),
        str(selected),
    )
    original_iterdir = Path.iterdir

    def reject_book_relisting(path: Path):
        if path == tmp_path:
            raise AssertionError("Browser snapshot must avoid folder relisting")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", reject_book_relisting)
    session = BookSession()

    session.open_book_async(selected, folder_snapshot=snapshot)
    assert session.wait_for_async(2000)
    qapp.processEvents()

    assert session.current_path == selected
    assert session.model.image_ids == [str(first), str(selected)]
    assert session.model.focused_index == 1
    assert session._open_workers == {}
    session.shutdown()


def test_running_stale_open_closes_source_and_cannot_replace_latest(
    qapp: QApplication,
) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    first_source = BlockingImageSource(
        Path("first"),
        threading.Event(),
        threading.Event(),
    )
    second_source = BlockingImageSource(
        Path("second"),
        threading.Event(),
        threading.Event(),
    )

    def source_factory(
        path: Path,
        **_kwargs: object,
    ) -> tuple[ImageSource, str | None]:
        if path.name == "first":
            first_started.set()
            assert release_first.wait(2)
            return first_source, None
        return second_source, None

    session = BookSession(source_factory=source_factory)
    opened_paths: list[Path] = []
    session.async_opened.connect(
        lambda opened: opened_paths.append(opened.requested_path)
    )

    session.open_book_async("first")
    assert first_started.wait(1)
    session.open_book_async("second")
    release_first.set()
    assert session.wait_for_async(2000)
    qapp.processEvents()

    assert first_source.closed
    assert not second_source.closed
    assert session.source is second_source
    assert session.current_path == Path("second")
    assert opened_paths == [Path("second")]
    assert session._open_workers == {}
    session.shutdown()


def test_async_open_failure_preserves_current_book_and_clears_tracking(
    qapp: QApplication,
) -> None:
    current_source = BlockingImageSource(
        Path("current"),
        threading.Event(),
        threading.Event(),
    )

    def source_factory(
        path: Path,
        **_kwargs: object,
    ) -> tuple[ImageSource, str | None]:
        if path.name == "broken":
            raise ImageSourceError("broken source")
        return current_source, None

    session = BookSession(source_factory=source_factory)
    session.open_book("current")
    original_images = list(session.model.image_ids)
    failures = []
    session.async_open_failed.connect(failures.append)

    session.open_book_async("broken")
    assert session.wait_for_async(2000)
    qapp.processEvents()

    assert session.source is current_source
    assert session.current_path == Path("current")
    assert session.model.image_ids == original_images
    assert len(failures) == 1
    assert failures[0].message == "broken source"
    assert session._open_workers == {}
    session.shutdown()
