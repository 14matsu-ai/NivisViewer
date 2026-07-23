from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .image_cache import ImageCache
from .image_source import ImageSource, ImageSourceError, create_image_source
from .page_model import PageModel


SourceFactory = Callable[..., tuple[ImageSource, str | None]]


@dataclass(frozen=True)
class BookOpened:
    requested_path: Path
    source_path: Path
    selected_image: str | None
    total_pages: int
    generation: int


class BookSession(QObject):
    book_opened = Signal(object)
    book_closed = Signal()
    page_changed = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        cache_size: int = 10,
        parent: QObject | None = None,
        *,
        source_factory: SourceFactory = create_image_source,
    ) -> None:
        super().__init__(parent)
        self.model = PageModel()
        self.image_cache = ImageCache(cache_size, self)
        self.current_path: Path | None = None
        self.source: ImageSource | None = None
        self.generation = 0
        self._source_factory = source_factory
        self._retired_sources: dict[int, ImageSource] = {}
        self._shutdown = False
        self.image_cache.sourceIdle.connect(self._release_retired_source)

    @property
    def is_open(self) -> bool:
        return self.source is not None

    @property
    def book_key(self) -> str:
        if self.source is None:
            return ""
        return str(self.source.source_path)

    def open_book(
        self,
        path: str | Path,
        *,
        recursive_folder: bool = False,
        sort_descending: bool = False,
    ) -> BookOpened:
        requested_path = Path(path)
        self.generation += 1
        self._shutdown = False

        new_source: ImageSource | None = None
        try:
            new_source, selected_image = self._source_factory(
                requested_path,
                recursive_folder=recursive_folder,
                sort_descending=sort_descending,
            )
            self.model.set_source(new_source, selected_image)
        except ImageSourceError as exc:
            if new_source is not None:
                self._close_source(new_source)
            self.error_occurred.emit(str(exc))
            raise
        except Exception as exc:
            if new_source is not None:
                self._close_source(new_source)
            error = ImageSourceError(f"本を開けません: {requested_path}")
            self.error_occurred.emit(str(error))
            raise error from exc

        old_source = self.source
        self.source = new_source
        self.current_path = requested_path
        self.image_cache.set_source(new_source, self.model.image_ids)
        if old_source is not None and old_source is not new_source:
            self._retire_source(old_source)

        opened = BookOpened(
            requested_path=requested_path,
            source_path=new_source.source_path,
            selected_image=selected_image,
            total_pages=self.model.total_pages,
            generation=self.generation,
        )
        self.book_opened.emit(opened)
        return opened

    def close_book(self) -> None:
        old_source = self.source
        had_book = old_source is not None
        self.generation += 1
        self.image_cache.clear()
        self.model.clear_source()
        self.source = None
        self.current_path = None
        if old_source is not None:
            self._retire_source(old_source)
        if had_book:
            self.book_closed.emit()

    def notify_page_changed(self) -> None:
        if self.is_open:
            self.page_changed.emit()

    def shutdown(self, wait_msecs: int = 5000) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.close_book()
        if self.image_cache.wait_for_done(wait_msecs):
            for source in list(self._retired_sources.values()):
                self._close_source(source)
            self._retired_sources.clear()

    def _retire_source(self, source: ImageSource) -> None:
        if self.image_cache.has_in_flight_for_source(source):
            self._retired_sources[id(source)] = source
            return
        self._close_source(source)

    def _release_retired_source(self, source: ImageSource) -> None:
        retired = self._retired_sources.pop(id(source), None)
        if retired is source:
            self._close_source(source)

    def _close_source(self, source: ImageSource) -> None:
        try:
            source.close()
        except Exception as exc:
            self.error_occurred.emit(f"画像ソースを閉じられません: {exc}")
