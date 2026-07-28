from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Event, Thread, get_ident

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.archive_backend import (
    ArchiveBackendError,
    ArchiveEntry,
    ArchiveErrorCode,
    ArchiveListing,
    MAX_IMAGE_ENTRY_BYTES,
)
from app.archive_backend_registry import ArchiveBackendRegistry
from app.book_session import BookSession
from app.image_source import (
    ImageSourceError,
    SevenZipImageSource,
    create_image_source,
)
from app.page_model import PageModel


def image_bytes(color: str = "red") -> bytes:
    output = BytesIO()
    with Image.new("RGB", (20, 30), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


class FakeBackend:
    def __init__(
        self,
        entries: tuple[ArchiveEntry, ...],
        *,
        encrypted: bool = False,
        data: bytes | None = None,
    ) -> None:
        self.entries = entries
        self.encrypted = encrypted
        self.data = data or image_bytes()
        self.list_calls = 0
        self.read_calls: list[tuple[str, str, int | None]] = []

    def is_available(self) -> bool:
        return True

    def list_entries(self, archive_path: str, *, cancel_token=None) -> ArchiveListing:
        self.list_calls += 1
        return ArchiveListing(
            archive_path,
            self.entries,
            "7z",
            True,
            self.encrypted,
        )

    def read_entry(
        self,
        archive_path: str,
        entry_path: str,
        *,
        cancel_token=None,
        maximum_bytes=None,
    ) -> bytes:
        self.read_calls.append((archive_path, entry_path, maximum_bytes))
        return self.data


def archive_entry(path: str, size: int = 100) -> ArchiveEntry:
    return ArchiveEntry(path, size, 50, False, False, original_path=path)


def test_source_lists_once_naturally_and_reads_selected_page(tmp_path: Path) -> None:
    archive = tmp_path / "日本語.cb7"
    archive.write_bytes(b"source")
    backend = FakeBackend(
        (
            archive_entry("page10.png"),
            archive_entry("page2.png"),
            archive_entry("page1.png"),
        )
    )

    source = SevenZipImageSource(archive, backend=backend)
    assert source.list_images() == ["page1.png", "page2.png", "page10.png"]
    assert source.list_images() == ["page1.png", "page2.png", "page10.png"]
    with source.open_image("page2.png") as image:
        assert image.size == (20, 30)

    assert backend.list_calls == 1
    assert backend.read_calls[0][1] == "page2.png"
    assert backend.read_calls[0][2] == 100
    assert source.solid is True
    source.close()


@pytest.mark.parametrize("extension", [".rar", ".cbr", ".7z", ".cb7", ".RAR"])
def test_factory_routes_external_extensions_to_injected_backend(
    tmp_path: Path,
    extension: str,
) -> None:
    archive = tmp_path / f"book{extension}"
    archive.write_bytes(b"source")
    backend = FakeBackend((archive_entry("1.png"),))
    registry = ArchiveBackendRegistry(seven_zip_backend=backend)

    source, selected = create_image_source(
        archive,
        archive_backend_registry=registry,
    )

    assert isinstance(source, SevenZipImageSource)
    assert selected is None
    source.close()


def test_book_session_prepares_external_archive_off_gui_thread(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"source")
    backend = FakeBackend(
        (
            archive_entry("page2.png"),
            archive_entry("page1.png"),
        )
    )
    listing_threads: list[int] = []
    original_list_entries = backend.list_entries

    def counted_list_entries(
        archive_path: str,
        *,
        cancel_token=None,
    ) -> ArchiveListing:
        listing_threads.append(get_ident())
        return original_list_entries(archive_path, cancel_token=cancel_token)

    backend.list_entries = counted_list_entries  # type: ignore[method-assign]
    registry = ArchiveBackendRegistry(seven_zip_backend=backend)
    session = BookSession(
        source_factory=lambda path, **kwargs: create_image_source(
            path,
            archive_backend_registry=registry,
            **kwargs,
        )
    )
    gui_thread = get_ident()

    session.open_book_async(archive)
    assert session.wait_for_async(2000)
    qapp.processEvents()

    assert listing_threads
    assert all(thread_id != gui_thread for thread_id in listing_threads)
    assert backend.list_calls == 1
    assert session.model.image_ids == ["page1.png", "page2.png"]
    assert session.current_path == archive
    assert session._open_workers == {}
    session.shutdown()
    registry.close()


def test_backend_missing_and_no_images_are_distinct_errors(tmp_path: Path) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"source")

    with pytest.raises(ImageSourceError) as missing:
        create_image_source(archive)
    assert missing.value.code == ArchiveErrorCode.BACKEND_NOT_FOUND.value

    with pytest.raises(ImageSourceError) as empty:
        SevenZipImageSource(archive, backend=FakeBackend(()))
    assert empty.value.code == "no_images"


def test_encrypted_listing_is_password_required(tmp_path: Path) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"source")

    with pytest.raises(ImageSourceError) as captured:
        SevenZipImageSource(
            archive,
            backend=FakeBackend((archive_entry("1.jpg"),), encrypted=True),
        )

    assert captured.value.code == ArchiveErrorCode.PASSWORD_REQUIRED.value


def test_declared_oversized_entry_is_rejected_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "book.7z"
    archive.write_bytes(b"source")
    backend = FakeBackend(
        (archive_entry("huge.png", MAX_IMAGE_ENTRY_BYTES + 1),)
    )
    source = SevenZipImageSource(archive, backend=backend)

    with pytest.raises(ImageSourceError) as captured:
        source.open_image("huge.png")

    assert captured.value.code == ArchiveErrorCode.ENTRY_TOO_LARGE.value
    assert backend.read_calls == []


def test_source_close_cancels_running_backend_request(tmp_path: Path) -> None:
    archive = tmp_path / "book.7z"
    archive.write_bytes(b"source")
    started = Event()
    cancelled_seen = Event()

    class BlockingBackend(FakeBackend):
        def read_entry(self, archive_path, entry_path, *, cancel_token=None, maximum_bytes=None):
            started.set()
            assert cancel_token is not None
            if cancel_token.wait(2):
                cancelled_seen.set()
                raise ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
            return self.data

    source = SevenZipImageSource(
        archive,
        backend=BlockingBackend((archive_entry("1.png"),)),
    )
    failures: list[Exception] = []
    thread = Thread(
        target=lambda: (
            source.open_image("1.png")
            if False
            else _capture_failure(source, failures)
        )
    )
    thread.start()
    assert started.wait(1)

    source.close()
    thread.join(2)

    assert cancelled_seen.is_set()
    assert failures and isinstance(failures[0], ImageSourceError)


def _capture_failure(source: SevenZipImageSource, failures: list[Exception]) -> None:
    try:
        source.open_image("1.png")
    except Exception as exc:
        failures.append(exc)


def test_multiple_sources_do_not_share_page_state(tmp_path: Path) -> None:
    first_path = tmp_path / "first.rar"
    second_path = tmp_path / "second.7z"
    first_path.write_bytes(b"one")
    second_path.write_bytes(b"two")
    first = SevenZipImageSource(
        first_path,
        backend=FakeBackend((archive_entry("1.png"),)),
    )
    second = SevenZipImageSource(
        second_path,
        backend=FakeBackend((archive_entry("2.png"),)),
    )

    assert first.list_images() == ["1.png"]
    assert second.list_images() == ["2.png"]


def test_source_integrates_with_page_model_without_gui_dependencies(tmp_path: Path) -> None:
    archive = tmp_path / "book.cbr"
    archive.write_bytes(b"source")
    source = SevenZipImageSource(
        archive,
        backend=FakeBackend(
            tuple(archive_entry(f"{index}.png") for index in range(3))
        ),
    )
    model = PageModel()

    model.set_source(source)

    assert model.total_pages == 3
    assert sorted(slot.page_index for slot in model.spread_at().slots) == [0]
    model.next()
    assert sorted(slot.page_index for slot in model.spread_at().slots) == [1, 2]
