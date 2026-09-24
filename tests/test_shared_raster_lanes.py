from threading import Event

from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource, ZipImageSource
from app.image_work_coordinator import ImageWorkCoordinator
from app.zip_raster_book_runtime import ZipRasterBookRuntime
from tests.test_folder_raster_book_runtime import _unit, _request, _write_folder, _wait_until
from tests.test_zip_raster_book_runtime import _write_zip


def test_zip_and_folder_share_two_slots_across_books(qapp, tmp_path):
    class Gate:
        def __init__(self, path):
            super().__init__(path)
            self.entered = Event()
            self.release = Event()

        def open_image(self, image_id):
            self.entered.set()
            assert self.release.wait(5)
            return super().open_image(image_id)

    class Folder(Gate, FolderImageSource):
        pass

    class Archive(Gate, ZipImageSource):
        pass

    archive = _write_zip(tmp_path, pages=1)
    sources = [Folder(_write_folder(tmp_path / "folder", pages=1)), Archive(archive), Archive(archive)]
    coordinator = ImageWorkCoordinator(folder_supplemental_workers=1)
    runtimes = [(FolderRasterBookRuntime if i == 0 else ZipRasterBookRuntime)(
        source, 1, max_active_jobs=2, image_work_coordinator=coordinator,
    ) for i, source in enumerate(sources)]
    try:
        for i in (0, 1):
            runtimes[i].request(_request(1, _unit(sources[i], 0)))
            assert sources[i].entered.wait(1)
        runtimes[2].request(_request(1, _unit(sources[2], 0)))
        qapp.processEvents()
        assert not sources[2].entered.is_set()
        assert sum(runtime.active_job_count for runtime in runtimes) == 2
        sources[0].release.set()
        _wait_until(qapp, sources[2].entered.is_set)
        assert not sources[1].release.is_set()
        assert sum(runtime.active_job_count for runtime in runtimes) <= 2
    finally:
        for source in sources:
            source.release.set()
        _wait_until(qapp, lambda: not any(runtime.has_unfinished_tasks() for runtime in runtimes))
        for runtime in runtimes:
            assert runtime.shutdown()
        assert coordinator.shutdown()
        for source in sources:
            source.close()
