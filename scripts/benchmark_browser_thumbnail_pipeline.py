"""Compare Browser thumbnail hot paths in a committed snapshot and live tree.

The script creates synthetic image/cache data only under --scratch. It imports
the actual modules from both source roots and never starts the application.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import importlib.util
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageChops, ImageStat
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication


def _load_package(alias: str, root: Path) -> SimpleNamespace:
    package_path = root / "app" / "__init__.py"
    package_spec = importlib.util.spec_from_file_location(
        alias,
        package_path,
        submodule_search_locations=[str(package_path.parent)],
    )
    if package_spec is None or package_spec.loader is None:
        raise RuntimeError(f"Cannot load application package from {root}")
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[alias] = package
    package_spec.loader.exec_module(package)
    names = (
        "browser_model",
        "thumbnail_disk_cache",
        "thumbnail_provider",
        "thumbnail_render",
    )
    modules = {
        name: importlib.import_module(f"{alias}.{name}") for name in names
    }
    return SimpleNamespace(
        root=root,
        BrowserItem=modules["browser_model"].BrowserItem,
        BrowserItemKind=modules["browser_model"].BrowserItemKind,
        ThumbnailDiskCache=modules["thumbnail_disk_cache"].ThumbnailDiskCache,
        BrowserThumbnailProvider=modules["thumbnail_provider"].BrowserThumbnailProvider,
        ThumbnailRenderSpec=modules["thumbnail_render"].ThumbnailRenderSpec,
        thumbnail_render=modules["thumbnail_render"],
    )


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": _percentile(samples, 0.95),
        "samples": len(samples),
    }


def _make_jpeg(path: Path, size: tuple[int, int], seed: int, orientation: int = 1) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = random.Random(seed).randbytes(size[0] * size[1] * 3)
    exif = Image.Exif()
    exif[274] = orientation
    with Image.frombytes("RGB", size, data) as image:
        image.save(path, format="JPEG", quality=90, optimize=False, exif=exif)


def _qimage_to_pil(image: QImage) -> Image.Image:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(converted.constBits()[: converted.sizeInBytes()])
    return Image.frombytes("RGBA", (converted.width(), converted.height()), data)


def _psnr(left: Image.Image, right: Image.Image) -> float | None:
    if left.size != right.size:
        return 0.0
    difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    rms = ImageStat.Stat(difference).rms
    mse = sum(value * value for value in rms) / len(rms)
    return None if mse == 0 else round(10 * math.log10(255**2 / mse), 3)


def _render_phase_sample(owner, path: Path, spec) -> dict[str, float]:
    phases = {
        "open_header_ms": 0.0,
        "image_load_call_ms": 0.0,
        "source_copy_inclusive_ms": 0.0,
        "jpeg_draft_ms": 0.0,
        "smart_crop_analysis_total_ms": 0.0,
        "smart_crop_pillow_ops_ms": 0.0,
        "final_crop_ms": 0.0,
        "final_resize_ms": 0.0,
        "qimage_conversion_ms": 0.0,
    }
    in_smart_crop = [False]
    thumbnail_depth = [0]
    original_open = Image.open
    original_load = Image.Image.load
    original_copy = Image.Image.copy
    original_crop = Image.Image.crop
    original_resize = Image.Image.resize
    original_thumbnail = Image.Image.thumbnail
    original_detect = owner.thumbnail_render.detect_smart_crop
    original_qimage = owner.thumbnail_render.pil_to_qimage

    def timed_open(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_open(*args, **kwargs)
        finally:
            if args and isinstance(args[0], (str, os.PathLike)) and Path(args[0]) == path:
                phases["open_header_ms"] += (time.perf_counter() - started) * 1000

    def timed_load(image, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_load(image, *args, **kwargs)
        finally:
            phases["image_load_call_ms"] += (time.perf_counter() - started) * 1000

    def timed_copy(image, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_copy(image, *args, **kwargs)
        finally:
            key = (
                "smart_crop_pillow_ops_ms"
                if in_smart_crop[0]
                else "source_copy_inclusive_ms"
            )
            phases[key] += (time.perf_counter() - started) * 1000

    def timed_crop(image, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_crop(image, *args, **kwargs)
        finally:
            key = "smart_crop_pillow_ops_ms" if in_smart_crop[0] else "final_crop_ms"
            phases[key] += (time.perf_counter() - started) * 1000

    def timed_resize(image, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_resize(image, *args, **kwargs)
        finally:
            if thumbnail_depth[0] == 0:
                key = "smart_crop_pillow_ops_ms" if in_smart_crop[0] else "final_resize_ms"
                phases[key] += (time.perf_counter() - started) * 1000

    def timed_thumbnail(image, *args, **kwargs):
        started = time.perf_counter()
        thumbnail_depth[0] += 1
        try:
            return original_thumbnail(image, *args, **kwargs)
        finally:
            thumbnail_depth[0] -= 1
            key = "smart_crop_pillow_ops_ms" if in_smart_crop[0] else "final_resize_ms"
            phases[key] += (time.perf_counter() - started) * 1000

    def timed_detect(*args, **kwargs):
        started = time.perf_counter()
        in_smart_crop[0] = True
        try:
            return original_detect(*args, **kwargs)
        finally:
            in_smart_crop[0] = False
            phases["smart_crop_analysis_total_ms"] += (time.perf_counter() - started) * 1000

    def timed_qimage(image):
        started = time.perf_counter()
        try:
            return original_qimage(image)
        finally:
            phases["qimage_conversion_ms"] += (time.perf_counter() - started) * 1000

    with ExitStack() as stack:
        stack.enter_context(patch.object(Image, "open", timed_open))
        stack.enter_context(patch.object(Image.Image, "load", timed_load))
        stack.enter_context(patch.object(Image.Image, "copy", timed_copy))
        stack.enter_context(patch.object(Image.Image, "crop", timed_crop))
        stack.enter_context(patch.object(Image.Image, "resize", timed_resize))
        stack.enter_context(patch.object(Image.Image, "thumbnail", timed_thumbnail))
        stack.enter_context(
            patch.object(owner.thumbnail_render, "detect_smart_crop", timed_detect)
        )
        stack.enter_context(
            patch.object(owner.thumbnail_render, "pil_to_qimage", timed_qimage)
        )
        draft = getattr(owner.thumbnail_render, "_draft_jpeg_before_copy", None)
        if draft is not None:
            def timed_draft(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return draft(*args, **kwargs)
                finally:
                    phases["jpeg_draft_ms"] += (time.perf_counter() - started) * 1000

            stack.enter_context(
                patch.object(
                    owner.thumbnail_render,
                    "_draft_jpeg_before_copy",
                    timed_draft,
                )
            )
        with Image.open(path) as opened:
            image, _crop = owner.thumbnail_render.render_pil_thumbnail(opened, spec)
    image = QImage()
    return {key: round(value, 3) for key, value in phases.items()}


def _render_metrics(base, current, scratch: Path, rounds: int) -> list[dict[str, object]]:
    cases = (
        ("landscape-letterbox", (2016, 1152), 1, "letterbox"),
        ("portrait-center-crop", (1152, 2016), 2, "center_crop"),
        ("large-portrait-smart-crop", (4000, 6000), 3, "smart_crop"),
        ("exif-rotated-smart-crop", (2016, 1152), 4, "smart_crop"),
    )
    results = []
    for name, size, seed, crop_mode in cases:
        path = scratch / "fixtures" / f"{name}.jpg"
        _make_jpeg(path, size, seed, orientation=6 if name.startswith("exif-") else 1)
        spec = current.ThumbnailRenderSpec.from_settings(
            256,
            "square_1_1",
            crop_mode,
            quality_mode="high",
        )

        def decode_size(owner) -> tuple[int, int]:
            with Image.open(path) as opened:
                helper = getattr(owner.thumbnail_render, "_draft_jpeg_before_copy", None)
                if helper is not None:
                    helper(opened, spec, None)
                return tuple(int(value) for value in opened.size)

        decode_dimensions = {
            "baseline_full_decode": decode_size(base),
            "live_after_draft_request": decode_size(current),
        }

        def render(owner) -> tuple[QImage, float]:
            started = time.perf_counter()
            with Image.open(path) as opened:
                image, _crop = owner.thumbnail_render.render_pil_thumbnail(
                    opened,
                    spec,
                )
            return image, (time.perf_counter() - started) * 1000

        baseline_image, _ = render(base)
        current_image, _ = render(current)
        baseline_pixels = _qimage_to_pil(baseline_image)
        current_pixels = _qimage_to_pil(current_image)
        assert baseline_pixels.size == current_pixels.size
        samples = {"baseline": [], "live": []}
        phase_samples = {"baseline": [], "live": []}
        for index in range(rounds):
            order = ("baseline", "live") if index % 2 == 0 else ("live", "baseline")
            for version in order:
                owner = base if version == "baseline" else current
                _image, elapsed = render(owner)
                samples[version].append(elapsed)
                phase_samples[version].append(
                    _render_phase_sample(owner, path, spec)
                )
        results.append(
            {
                "case": name,
                "source_size": size,
                "jpeg_bytes": path.stat().st_size,
                "decode_dimensions": decode_dimensions,
                "output_size": baseline_pixels.size,
                "output_qimage_bytes": baseline_image.sizeInBytes(),
                "draft_vs_full_psnr_db": _psnr(baseline_pixels, current_pixels),
                "timing": {
                    version: _summary(times)
                    for version, times in samples.items()
                },
                "instrumented_phase_timing_ms": {
                    version: {
                        phase: _summary([sample[phase] for sample in timings])
                        for phase in timings[0]
                    }
                    for version, timings in phase_samples.items()
                },
            }
        )
        baseline_pixels.close()
        current_pixels.close()
        baseline_image = QImage()
        current_image = QImage()
    return results


def _entry_values(cache, scratch: Path, index: int, now: float) -> dict[str, object]:
    return {
        "cache_key": f"preexisting-{index:05d}",
        "source_path": str(scratch / f"unrelated-source-{index:05d}.jpg"),
        "item_kind": "image",
        "source_size": 1,
        "source_mtime_ns": index + 1,
        "cover_path": "",
        "cover_size": 0,
        "cover_mtime_ns": 0,
        "entry_path": "",
        "thumbnail_size": 96,
        "family_token": 96,
        "frame_width": 96,
        "frame_height": 96,
        "format_version": cache.format_version,
        "file_name": f"preexisting-{index:05d}.{cache._extension}",
        "byte_size": 1,
        "created_at": now,
        "last_used": now + index,
        "page_count": 1,
    }


def _populate_cache(cache, scratch: Path, count: int) -> None:
    cache.files_dir.mkdir(parents=True, exist_ok=True)
    connection = cache._connection
    assert connection is not None
    columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(entries)")]
    known = _entry_values(cache, scratch, 0, time.time())
    insert_columns = [column for column in columns if column in known]
    sql = (
        f"INSERT INTO entries ({', '.join(insert_columns)}) VALUES "
        f"({', '.join('?' for _ in insert_columns)})"
    )
    now = time.time()
    rows = []
    for index in range(count):
        values = _entry_values(cache, scratch, index, now)
        rows.append(tuple(values[column] for column in insert_columns))
    connection.executemany(sql, rows)
    connection.commit()
    for index in range(count):
        (cache.files_dir / f"preexisting-{index:05d}.{cache._extension}").write_bytes(b"x")
    cache._refresh_cached_statistics()


def _cache_metrics(owners, scratch: Path, counts: list[int], rounds: int) -> list[dict[str, object]]:
    results = []
    for count in counts:
        for owner in owners:
            cache = owner.ThumbnailDiskCache(scratch / f"cache-{owner.root.name}-{count}")
            _populate_cache(cache, scratch, count)
            cache._saves_since_cleanup = max(0, cache.cleanup_interval - 1)
            connection = cache._connection
            assert connection is not None
            metrics = {
                "select_queries": 0,
                "cache_file_exists_checks": 0,
                "prune_ms": 0.0,
                "encode_temp_write_ms": 0.0,
                "file_publish_ms": 0.0,
                "database_commit_ms": 0.0,
            }

            def trace(statement: str) -> None:
                if statement.lstrip().upper().startswith("SELECT"):
                    metrics["select_queries"] += 1

            connection.set_trace_callback(trace)
            original_is_file = Path.is_file
            files_dir = cache.files_dir

            def counted_is_file(path: Path) -> bool:
                if path.parent == files_dir:
                    metrics["cache_file_exists_checks"] += 1
                return original_is_file(path)

            original_prune = cache.prune

            def timed_prune(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return original_prune(*args, **kwargs)
                finally:
                    metrics["prune_ms"] += (time.perf_counter() - started) * 1000

            cache.prune = timed_prune
            original_connection = connection

            class TimedConnection:
                def __getattr__(self, name):
                    return getattr(original_connection, name)

                def commit(self):
                    started = time.perf_counter()
                    try:
                        return original_connection.commit()
                    finally:
                        metrics["database_commit_ms"] += (
                            time.perf_counter() - started
                        ) * 1000

            cache._connection = TimedConnection()
            original_save = Image.Image.save
            original_replace = os.replace

            def timed_save(image, fp, *args, **kwargs):
                is_cache_file = isinstance(fp, (str, os.PathLike)) and Path(fp).parent == files_dir
                started = time.perf_counter()
                try:
                    return original_save(image, fp, *args, **kwargs)
                finally:
                    if is_cache_file:
                        metrics["encode_temp_write_ms"] += (
                            time.perf_counter() - started
                        ) * 1000

            def timed_replace(src, dst):
                is_cache_file = Path(dst).parent == files_dir
                started = time.perf_counter()
                try:
                    return original_replace(src, dst)
                finally:
                    if is_cache_file:
                        metrics["file_publish_ms"] += (
                            time.perf_counter() - started
                        ) * 1000

            spec = owner.ThumbnailRenderSpec.from_settings(
                96, "square_1_1", "letterbox"
            )
            image = QImage(96, 96, QImage.Format.Format_RGBA8888)
            image.fill(0xFF557799)
            put_times = []
            try:
                with (
                    patch.object(Path, "is_file", counted_is_file),
                    patch.object(Image.Image, "save", timed_save),
                    patch.object(os, "replace", timed_replace),
                ):
                    for iteration in range(rounds):
                        source = scratch / f"put-source-{owner.root.name}-{count}-{iteration}.jpg"
                        source.write_bytes(b"small synthetic source")
                        stat = source.stat()
                        item = owner.BrowserItem(
                            source.name,
                            source,
                            owner.BrowserItemKind.IMAGE,
                            stat.st_mtime,
                            file_size=stat.st_size,
                            modified_time_ns=stat.st_mtime_ns,
                        )
                        started = time.perf_counter()
                        saved = cache.put(item, spec, image, page_count=1)
                        put_times.append((time.perf_counter() - started) * 1000)
                        if not saved:
                            raise RuntimeError(f"cache put failed: {cache.last_error}")
                        if hasattr(cache, "_saves_since_cleanup"):
                            cache._saves_since_cleanup = max(0, cache.cleanup_interval - 1)
            finally:
                connection.set_trace_callback(None)
                cache.close()
            results.append(
                {
                    "source_version": owner.root.name,
                    "preexisting_entries": count,
                    "put": _summary(put_times),
                    "select_queries_per_put": round(metrics["select_queries"] / rounds, 2),
                    "cache_file_exists_checks_per_put": round(
                        metrics["cache_file_exists_checks"] / rounds, 2
                    ),
                    "cleanup_ms_per_put": round(metrics["prune_ms"] / rounds, 3),
                    "encode_temp_write_ms_per_put": round(
                        metrics["encode_temp_write_ms"] / rounds, 3
                    ),
                    "file_publish_ms_per_put": round(
                        metrics["file_publish_ms"] / rounds, 3
                    ),
                    "database_commit_ms_per_put": round(
                        metrics["database_commit_ms"] / rounds, 3
                    ),
                }
            )
    return results


def _latency_metrics(owners, scratch: Path, qapp: QApplication, rounds: int) -> list[dict[str, object]]:
    results = []
    delay_seconds = 0.12
    for owner in owners:
        ready_times = []
        persistence_times = []
        queue_times = []
        for iteration in range(rounds):
            source = scratch / f"latency-{owner.root.name}-{iteration}.jpg"
            if not source.exists():
                with Image.new("RGB", (64, 64), "#456789") as image:
                    image.save(source, format="JPEG")
            stat = source.stat()
            item = owner.BrowserItem(
                source.name,
                source,
                owner.BrowserItemKind.IMAGE,
                stat.st_mtime,
                file_size=stat.st_size,
                modified_time_ns=stat.st_mtime_ns,
            )
            cache = owner.ThumbnailDiskCache(scratch / f"latency-cache-{owner.root.name}-{iteration}")
            original_put = cache.put
            put_started = Event()
            put_start_time: list[float] = []

            def delayed_put(*args, **kwargs):
                put_start_time.append(time.perf_counter())
                put_started.set()
                time.sleep(delay_seconds)
                return original_put(*args, **kwargs)

            cache.put = delayed_put
            image = QImage(64, 64, QImage.Format.Format_RGBA8888)
            image.fill(0xFF456789)
            provider = owner.BrowserThumbnailProvider(
                loader=lambda *_args: image.copy(),
                disk_cache=cache,
            )
            ready = QSignalSpy(provider.thumbnail_ready)
            generation = provider.begin_generation()
            spec = owner.ThumbnailRenderSpec.from_settings(
                96, "square_1_1", "letterbox"
            )
            started = time.perf_counter()
            if not provider.request(item, spec, generation=generation):
                raise RuntimeError("thumbnail request was rejected")
            deadline = started + 5
            while ready.count() == 0 and time.perf_counter() < deadline:
                qapp.processEvents()
                time.sleep(0.001)
            if ready.count() == 0:
                provider.close()
                raise TimeoutError("thumbnail did not reach the ready signal")
            ready_at = time.perf_counter()
            if not put_started.wait(2):
                provider.close()
                raise TimeoutError("cache writer did not start")
            put_at = put_start_time[0]
            if not provider.wait_for_done(5000):
                provider.close()
                raise TimeoutError("thumbnail persistence did not finish")
            persisted_at = time.perf_counter()
            ready_times.append((ready_at - started) * 1000)
            queue_times.append((put_at - started) * 1000)
            persistence_times.append((persisted_at - started) * 1000)
            provider.close()
            qapp.processEvents()
        results.append(
            {
                "source_version": owner.root.name,
                "artificial_put_delay_ms": delay_seconds * 1000,
                "request_to_put_start": _summary(queue_times),
                "request_to_ready_signal": _summary(ready_times),
                "request_to_persistence_complete": _summary(persistence_times),
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--current-root", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--cache-counts", type=int, nargs="+", default=[128, 1024, 4096, 16384])
    args = parser.parse_args()
    baseline_root = args.baseline_root.resolve()
    current_root = args.current_root.resolve()
    scratch = args.scratch.resolve()
    output = args.output.resolve()
    for protected in (baseline_root, current_root):
        if scratch == protected or protected in scratch.parents:
            parser.error("--scratch must be outside both source roots")
    if output == current_root or current_root in output.parents:
        parser.error("--output must be outside the repository")
    if not 1 <= args.rounds <= 20 or any(not 1 <= count <= 16384 for count in args.cache_counts):
        parser.error("Use rounds=1..20 and cache counts=1..16384")

    scratch.mkdir(parents=True, exist_ok=True)
    qapp = QApplication.instance() or QApplication([])
    baseline = _load_package("nivis_audit_baseline", baseline_root)
    current = _load_package("nivis_audit_current", current_root)
    owners = (baseline, current)
    report = {
        "method": "actual package modules, synthetic temp fixtures, same process and Pillow/Qt runtime",
        "baseline_root": str(baseline_root),
        "current_root": str(current_root),
        "limitations": [
            "Offscreen ready signal is not a real Windows paint or physical-display measurement.",
            "Render timings combine image open/decode, crop/resize and QImage conversion; instrumented source-copy time includes Pillow's lazy decode, while decode dimensions expose draft reduction.",
            "The controlled 120 ms cache delay isolates lifecycle ordering, not a storage-device benchmark.",
            "The cache population uses valid temporary index rows and 1-byte placeholder files; it measures maintenance work counts and latency, not realistic compression ratios.",
            "Peak native memory, PDF rendering, GPU paint and real-drive latency are not measured.",
        ],
    }
    with TemporaryDirectory(
        prefix="nivis-browser-pipeline-",
        dir=scratch,
    ) as fixture_dir:
        fixture_root = Path(fixture_dir)
        report["render"] = _render_metrics(
            baseline, current, fixture_root, args.rounds
        )
        report["disk_cache_put"] = _cache_metrics(
            owners, fixture_root, args.cache_counts, min(3, args.rounds)
        )
        report["display_vs_save"] = _latency_metrics(
            owners, fixture_root, qapp, min(5, args.rounds)
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
