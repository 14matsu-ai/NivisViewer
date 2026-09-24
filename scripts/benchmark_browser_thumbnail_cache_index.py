from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from statistics import median
from time import perf_counter_ns

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.browser_thumbnail_memory_policy import cache_victims
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec


def _provider_with_entries(count: int) -> tuple[BrowserThumbnailProvider, object, QImage]:
    provider = BrowserThumbnailProvider(
        loader=lambda *_: None,
        disk_cache_enabled=False,
        cache_capacity=count + 1,
        cache_capacity_bytes=(count + 1) * 4,
    )
    spec = ThumbnailRenderSpec.from_settings(48, "square_1_1", "letterbox")
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    image.fill(0)
    revision = ("benchmark",)
    provider._browser_memory_managed = True
    provider._cache_specs[spec.cache_token] = spec
    for index in range(count):
        key = (f"fixture-{index}", spec.cache_token, (revision[0], index))
        provider._cache[key] = image
        provider._cache_bytes += int(image.sizeInBytes())
        provider._cache_index_add(key)
    provider._rebuild_cache_rank_index()
    return provider, spec, image


def _median_call(callable_, repetitions: int) -> float:
    samples = []
    for _ in range(7):
        start = perf_counter_ns()
        for _iteration in range(repetitions):
            callable_()
        samples.append((perf_counter_ns() - start) / repetitions)
    return median(samples)


def main() -> None:
    QApplication.instance() or QApplication([])
    results = []
    for count in (256, 4096, 16384):
        provider, spec, image = _provider_with_entries(count)
        target_path = f"fixture-{count - 1}"
        target_revision = ("benchmark", count - 1)
        request_repetitions = 500
        indexed_ns = _median_call(
            lambda: provider._memory_candidate(
                target_path, target_revision, spec
            ),
            request_repetitions,
        )
        legacy_ns = _median_call(
            lambda: next(
                (
                    cached_image
                    for (path, _token, revision), cached_image
                    in provider._cache.items()
                    if path == target_path and revision == target_revision
                ),
                None,
            ),
            50,
        )

        costs = {key: int(value.sizeInBytes()) for key, value in provider._cache.items()}
        ranks = {}
        legacy_trim_ns = _median_call(
            lambda: cache_victims(
                costs,
                ranks,
                byte_limit=(count - 1) * int(image.sizeInBytes()),
                entry_limit=count + 1,
            ),
            1,
        )
        def indexed_trim_one() -> None:
            provider._cache_capacity = len(provider._cache) - 1
            provider._cache_capacity_bytes = (
                provider._cache_bytes - int(image.sizeInBytes())
            )
            provider._trim_browser_memory()

        indexed_trim_ns = _median_call(indexed_trim_one, 1)
        results.append(
            {
                "entries": count,
                "indexed_candidate_ns": round(indexed_ns),
                "legacy_candidate_ns": round(legacy_ns),
                "indexed_candidate_keys_examined": 1,
                "legacy_candidate_keys_examined": count,
                "indexed_trim_ns": round(indexed_trim_ns),
                "legacy_trim_ns": round(legacy_trim_ns),
                "indexed_trim_sorts": 0,
                "legacy_trim_sorted_keys": count,
                "indexed_trim_victims": 1,
            }
        )
        provider.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
