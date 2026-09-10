"""Read-only production diagnosis using disposable, matching ZIP/RAR fixtures.

Requires an existing Rar.exe console encoder and UnRAR.exe decoder. No user
archives or configuration are used. This does not change production scheduling.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import platform
from contextlib import ExitStack
from unittest.mock import patch
from tempfile import TemporaryDirectory
from time import perf_counter as monotonic
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image, ImageOps
from PySide6.QtWidgets import QApplication

from app.archive_backend_registry import ArchiveBackendRegistry
from app.config_manager import ConfigManager
from app.image_source import SevenZipImageSource
from app.image_source import ImageSourceError
from app.archive_backend import ArchiveBackendError
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_window import ViewerWindow
from app.winrar_backend import WinRARBackend
from scripts.benchmark_viewer_navigation import _jpeg_payload


class TimedBackend(WinRARBackend):
    def __init__(self, executable):
        super().__init__(executable)
        self.reads = []
        self.listings = []
        self.attempts = []

    def list_entries(self, *args, **kwargs):
        start = monotonic()
        result = super().list_entries(*args, **kwargs)
        self.listings.append((monotonic() - start) * 1000)
        return result

    def read_entry(self, *args, **kwargs):
        start = monotonic()
        attempt = {"entry": args[1], "state": "started"}
        self.attempts.append(attempt)
        try:
            result = super().read_entry(*args, **kwargs)
        except Exception as exc:
            attempt["state"] = str(getattr(exc, "code", type(exc).__name__))
            raise
        attempt["state"] = "completed"
        self.reads.append({"entry": args[1], "bytes": len(result), "ms": round((monotonic() - start) * 1000, 3)})
        return result


def safety_probe(rar: Path, unrar: Path):
    """Actual console failure/volume checks, entirely inside disposable storage."""
    results = {}
    with TemporaryDirectory(prefix="nivis-rar-safety-") as directory:
        root = Path(directory)
        page = root / "page.jpg"
        page.write_bytes(_jpeg_payload((2400, 3600), 0, fixture_mode="high-detail"))
        def encode(name, *options):
            target = root / name
            subprocess.run([str(rar), "a", "-idq", "-ma5", "-m0", "-ep", *options,
                            str(target), str(page)], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return target
        encrypted = encode("encrypted.rar", "-hpNivisDisposableFixture")
        encode("split.rar", "-v1m")
        parts = sorted(root.glob("split.part*.rar"))
        assert len(parts) > 1
        backend = WinRARBackend(unrar)
        try:
            source = SevenZipImageSource(parts[0], backend=backend)
            source.set_payload_cache_budget(32 * 1024 * 1024)
            assert source.payload_cache_budget == 0
            with source.open_image("page.jpg") as image:
                assert image.size == (2400, 3600)
            source.close()
            results["multivolume_complete"] = "decoded; retention disabled"
            # Move only this explicitly known temporary fixture volume and
            # restore it in finally; no user archive/path is touched.
            last, hidden = parts[-1], root / "missing-volume.fixture"
            last.rename(hidden)
            try:
                backend.read_entry(str(parts[0]), "page.jpg")
                raise AssertionError("Missing volume unexpectedly succeeded")
            except ArchiveBackendError as exc:
                results["missing_volume"] = exc.code.value
            finally:
                hidden.rename(last)
            corrupt = root / "corrupt.rar"
            corrupt.write_bytes(b"not an archive")
            for label, target in (("encrypted", encrypted), ("corrupt", corrupt)):
                try:
                    SevenZipImageSource(target, backend=backend)
                    raise AssertionError(f"{label} unexpectedly opened")
                except (ArchiveBackendError, ImageSourceError) as exc:
                    results[label] = str(exc.code)
        finally:
            backend.close()
    return results


def run(rar: Path, unrar: Path):
    # Observational wrappers only: no scheduler/source behavior is replaced.
    from app import image_source, zip_raster_book_runtime
    phases = []
    def timed(label, function):
        def observe(*args, **kwargs):
            start = monotonic()
            try:
                return function(*args, **kwargs)
            finally:
                phases.append({"phase": label, "ms": round((monotonic() - start) * 1000, 3)})
        return observe
    with ExitStack() as stack:
        for module, name, label in (
            (image_source, "_read_folder_compatible_jpeg_at_most", "jpeg_tier_decode_and_qimage"),
            (zip_raster_book_runtime, "render_qimage", "resize_rotate"),
        ):
            stack.enter_context(patch.object(module, name, timed(label, getattr(module, name))))
        return _run(rar, unrar, phases)


def _run(rar: Path, unrar: Path, phases):
    app = QApplication.instance() or QApplication([])
    report = {"python": platform.python_version(), "os_cache": "warm; application cache initially empty",
              "fixture": {"pages": 6, "size": [2400, 3600], "mode": "high-detail"}, "cases": {}}
    with TemporaryDirectory(prefix="nivis-rar-diagnosis-") as directory:
        root = Path(directory)
        files = []
        archive = root / "fixture.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
            for index in range(6):
                name = f"{index:03}.jpg"
                payload = _jpeg_payload((2400, 3600), index, fixture_mode="high-detail")
                path = root / name
                path.write_bytes(payload)
                files.append(path)
                output.writestr(name, payload)
        cases = {"zip": archive}
        for name, solid_flag in (("rar_non_solid", "-s-"), ("rar_solid", "-s")):
            target = root / (name + ".rar")
            subprocess.run([str(rar), "a", "-idq", "-ma5", "-m3", solid_flag,
                            "-ep", str(target), *map(str, files)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            cases[name] = target
        for name, target in cases.items():
            backend = TimedBackend(unrar)
            case = {"archive_bytes": target.stat().st_size}
            if name != "zip":
                case["backend_version"] = backend.info.version_text
                listing = backend.list_entries(str(target))
                case["listing_ms"] = backend.listings[-1]
                case["detected_solid"] = listing.solid
                # Extraction and Pillow decoding measured separately, without
                # claiming these synchronous probes are Viewer timings.
                components = []
                for index in (0, 1, 2, 5, 4, 1):
                    payload = backend.read_entry(str(target), files[index].name)
                    start = monotonic()
                    with Image.open(io.BytesIO(payload)) as image:
                        with ImageOps.exif_transpose(image).copy():
                            pass
                    components.append({"page": index, "extract_ms": backend.reads[-1]["ms"],
                                       "decode_ms": round((monotonic() - start) * 1000, 3)})
                case["components"] = components
                probes = []
                for budget in (0, 32 * 1024 * 1024):
                    source = SevenZipImageSource(target, backend=backend)
                    source.set_payload_cache_budget(budget)
                    before = len(backend.reads)
                    start = monotonic()
                    try:
                        for bounds in ((640, 800), (None, None), (1280, 1600)):
                            source.open_compatible_jpeg_at_most("000.jpg", bounds)
                        probes.append({"budget": budget, "three_tiers_ms": round((monotonic() - start) * 1000, 3),
                                       "extractions": len(backend.reads) - before,
                                       "retained_bytes": source.payload_cache_bytes})
                    finally:
                        source.close()
                    assert source.payload_cache_bytes == 0
                case["payload_reuse_probe"] = probes
                backend.reads.clear()
                backend.listings.clear()
                backend.attempts.clear()
            phase_start = len(phases)
            config = ConfigManager(root / (name + ".json"))
            config.load()
            config.apply({"view_mode": "single", "viewer_memory_mode": "256"})
            registry = ArchiveBackendRegistry(config_manager=config, winrar_backend=backend)
            window = ViewerWindow(config_manager=config, archive_backend_registry=registry)
            window.resize(1280, 800)
            paints = []
            window.viewer.contentPainted.connect(lambda *_: paints.append((window.presentation_state.displayed_page, monotonic())))
            window.show()
            app.processEvents()

            def await_paint(page, since):
                deadline = monotonic() + 30
                while not any(p == page and when >= since for p, when in paints):
                    if monotonic() >= deadline:
                        raise TimeoutError((name, page, window.status.currentMessage()))
                    app.processEvents()
                return round((next(t for p, t in paints if p == page and t >= since) - since) * 1000, 3)

            try:
                start = monotonic()
                assert window.open_path(target)
                case["open_to_first_paint_ms"] = await_paint(0, start)
                case["viewer_runtime"] = type(window.book_session.viewer_runtime).__name__
                case["decode_demand_delay_ms"] = window._decode_demand_timer.interval()
                legs = []
                for label, page in (("forward", 1), ("forward", 2), ("cold_jump", 5),
                                    ("reverse", 4), ("backtrack", 1), ("cached", 2), ("cached", 1)):
                    start = monotonic()
                    before = len(backend.reads)
                    window._go_to_index_with_history(page)
                    legacy_timer_active = window._decode_demand_timer.isActive()
                    elapsed = await_paint(page, start)
                    legs.append({"label": label, "page": page, "request_to_paint_ms": elapsed,
                                 "legacy_timer_active": legacy_timer_active,
                                 "extractions_completed_during_leg": len(backend.reads) - before})
                case["navigation"] = legs
                case["viewer_extractions"] = list(backend.reads)
                case["viewer_listing_count"] = len(backend.listings)
                case["viewer_attempts"] = [dict(item) for item in backend.attempts]
                case["phase_samples"] = list(phases[phase_start:])
                source = window.book_session.source
                case["payload_retained_bytes"] = getattr(source, "payload_cache_bytes", 0)
                # Fresh source/book epoch: no artifact reuse from measured legs.
                start = monotonic()
                assert window.open_path(archive if name != "zip" else cases["rar_non_solid"])
                case["book_switch_to_first_paint_ms"] = await_paint(0, start)
                start = monotonic()
                assert window.open_path(target)
                await_paint(0, start)
                commits = []
                def record_commit(commit):
                    commits.append(commit.frame.unit.focused_index)
                window.presentationCommitted.connect(record_commit)
                start = monotonic()
                attempts_before = len(backend.attempts)
                for _ in range(5):
                    window.next_page(input_kind=NavigationInputKind.WHEEL)
                window._finish_wheel_navigation()
                case["rapid_final"] = {"ms": await_paint(5, start), "commits": list(commits),
                                       "attempts": [dict(item) for item in backend.attempts[attempts_before:]]}
                window.presentationCommitted.disconnect(record_commit)
                report["cases"][name] = case
            finally:
                window.prepare_shutdown()
                window.close()
                app.processEvents()
                registry.close()
                backend.close()
        # Inspect actual automatic discovery on a disposable archive, with
        # defaults identical to the readable repository config (auto, no path).
        config = ConfigManager(root / "discovery.json")
        config.load()
        registry = ArchiveBackendRegistry(config_manager=config)
        try:
            backend = registry.backend_for_path(cases["rar_non_solid"])
            backend.list_entries(str(cases["rar_non_solid"]))
            winners = getattr(backend, "_selected_by_archive", {})
            winner = next(iter(winners.values()), backend)
            info = winner.info
            report["automatic_discovery"] = {"backend": type(winner).__name__,
                "executable": info.executable_path, "version": info.version_text}
        finally:
            registry.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rar", type=Path, required=True)
    parser.add_argument("--unrar", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--safety-only", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.safety_only:
        print(json.dumps(safety_probe(args.rar, args.unrar), indent=2))
        raise SystemExit(0)
    report = run(args.rar, args.unrar)
    if args.compact:
        for case in report["cases"].values():
            case["navigation_ms"] = [leg["request_to_paint_ms"] for leg in case.pop("navigation")]
            case["phase_ms"] = {
                phase: [p["ms"] for p in case["phase_samples"] if p["phase"] == phase]
                for phase in {p["phase"] for p in case["phase_samples"]}}
            for key in ("components", "phase_samples", "viewer_extractions", "viewer_attempts"):
                case.pop(key, None)
    print(json.dumps(report, indent=2))
