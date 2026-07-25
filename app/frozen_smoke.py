from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from .app_paths import AppPaths
from .application_controller import ApplicationController
from .config_manager import ConfigManager
from .file_operation_service import FileOperationService
from .image_source import ZipImageSource
from .metadata_store import MetadataStore
from .pdfium_backend import PdfiumBackend
from .pdfium_service import PdfiumService
from .thumbnail_disk_cache import ThumbnailDiskCache
from .version import __version__


def run_frozen_smoke(
    application: QApplication,
    app_paths: AppPaths,
    output_path: str | Path,
) -> bool:
    checks = {
        "qt": False,
        "browser": False,
        "viewer": False,
        "jpeg": False,
        "png": False,
        "webp": False,
        "gif": False,
        "tiff": False,
        "ico": False,
        "zip": False,
        "pdf": False,
        "metadata": False,
        "thumbnail_cache": False,
        "file_operations": False,
        "shutdown": False,
    }
    errors: list[str] = []
    controller: ApplicationController | None = None
    pdf_service: PdfiumService | None = None
    metadata: MetadataStore | None = None
    cache: ThumbnailDiskCache | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="NivisViewer-smoke-") as temporary:
            root = Path(temporary)
            checks["qt"] = application is not None
            images = root / "images"
            images.mkdir()
            for extension, format_name in (
                (".jpg", "JPEG"),
                (".png", "PNG"),
                (".webp", "WEBP"),
                (".gif", "GIF"),
                (".tiff", "TIFF"),
                (".ico", "ICO"),
            ):
                path = images / f"日本語 image{extension}"
                size = (32, 32) if extension == ".ico" else (24, 32)
                Image.new("RGB", size, (30, 80, 140)).save(
                    path, format=format_name
                )
                with Image.open(path) as image:
                    image.load()
                    checks[extension.lstrip(".").replace("jpg", "jpeg")] = True
            operation_source = root / "rename source.txt"
            operation_source.write_text("NivisViewer", encoding="utf-8")
            operation = FileOperationService().rename(
                operation_source,
                "名前変更済み.txt",
            )
            checks["file_operations"] = bool(
                operation.successes
                and (root / "名前変更済み.txt").read_text(encoding="utf-8")
                == "NivisViewer"
            )
            archive = root / "book.cbz"
            with zipfile.ZipFile(archive, "w") as output:
                output.write(images / "日本語 image.jpg", "page 1.jpg")
            source = ZipImageSource(archive)
            try:
                checks["zip"] = source.list_images() == ["page 1.jpg"]
            finally:
                source.close()

            pdf = root / "book.pdf"
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument.new()
            try:
                page = document.new_page(200, 300)
                page.close()
                document.save(pdf)
            finally:
                document.close()
            backend = PdfiumBackend()
            info = backend.open_document(str(pdf))
            try:
                checks["pdf"] = info.page_count == 1
            finally:
                backend.close_document(info.document_id)
                backend.close_all()

            config = ConfigManager(app_paths.config_path, writable=True)
            config.load()
            config.save({"background_color": "#102030"})
            metadata = MetadataStore(app_paths.metadata_path)
            checks["metadata"] = metadata.enabled
            cache = ThumbnailDiskCache(app_paths.thumbnail_cache_dir, enabled=True)
            checks["thumbnail_cache"] = cache.enabled
            pdf_service = PdfiumService()
            controller = ApplicationController(
                application,
                config_manager=config,
                metadata_store=metadata,
                pdfium_service=pdf_service,
            )
            browser = controller.start(restore=False)
            viewer = controller.create_viewer_window()
            checks["browser"] = browser is not None
            checks["viewer"] = viewer is not None
            application.processEvents()
            controller.shutdown()
            checks["shutdown"] = True
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if controller is not None:
            controller.shutdown()
        elif pdf_service is not None:
            pdf_service.shutdown()
        if cache is not None:
            cache.close()
        if metadata is not None:
            metadata.close()
    result = {
        "success": all(checks.values()) and not errors,
        "version": __version__,
        "frozen": app_paths.frozen,
        "checks": checks,
        "errors": errors,
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return bool(result["success"])
