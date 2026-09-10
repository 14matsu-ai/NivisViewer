"""String-only A/B of removed per-action derivation and snapshot-owned indexes.

No filesystem listing, Viewer launch, or production navigation override. Both
implementations use identical prebuilt entries in the same Python process.
The baseline below preserves the pre-index algorithms for reproducibility only.
"""
from __future__ import annotations

from dataclasses import dataclass
import gc
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
import tracemalloc

from app.adjacent_book_search import (
    AdjacentBookBrowserSnapshot,
    AdjacentBookSnapshotEntry,
    AdjacentBookSearchStatus,
    lexical_absolute,
    path_key,
)
from app.application_controller import ApplicationController
from app.image_source import FolderListingSnapshot


@dataclass(frozen=True)
class _BeforeSnapshot:
    parent_folder: str
    scan_generation: int
    entries: tuple[AdjacentBookSnapshotEntry, ...]
    sort_identity: str = "name:ascending"
    filter_identity: str = "browser-visible-items"

    @property
    def viewer_paths(self):
        return tuple(
            lexical_absolute(entry.absolute_path) for entry in self.entries
            if entry.openable_by_nivisviewer and entry.item_kind != "other"
        )

    def adjacent_viewer_path(self, current_path, direction, *, loop=False):
        paths = self.viewer_paths
        keys = tuple(path_key(path) for path in paths)
        try:
            current_index = keys.index(path_key(current_path))
        except ValueError:
            return AdjacentBookSearchStatus.UNAVAILABLE, None
        next_index = current_index + (-1 if direction < 0 else 1)
        if not 0 <= next_index < len(paths):
            if loop and paths:
                next_index %= len(paths)
            else:
                return AdjacentBookSearchStatus.BOUNDARY, None
        return AdjacentBookSearchStatus.FOUND, paths[next_index]

    def contains_viewer_path(self, path):
        return path_key(path) in {path_key(candidate) for candidate in self.viewer_paths}


def _before_image_projection(snapshot, selected_path):
    selected_key = path_key(selected_path)
    image_entries = tuple(
        entry for entry in snapshot.entries
        if entry.openable_by_nivisviewer and entry.item_kind == "image"
    )
    image_ids = tuple(lexical_absolute(entry.absolute_path) for entry in image_entries)
    image_keys = tuple(path_key(path) for path in image_ids)
    try:
        selected_index = image_keys.index(selected_key)
    except ValueError:
        return None
    return FolderListingSnapshot(
        Path(snapshot.parent_folder), image_ids, image_ids[selected_index],
        tuple((lexical_absolute(e.absolute_path), e.file_size, e.modified_time_ns) for e in image_entries),
        generation=snapshot.scan_generation, sort_identity=snapshot.sort_identity,
        selected_index=selected_index, filter_identity=snapshot.filter_identity,
    )


def _median_ms(call, *, batch=1):
    samples = []
    for _ in range(7):
        started = perf_counter()
        for _ in range(batch):
            call()
        samples.append((perf_counter() - started) * 1000 / batch)
    return round(median(samples), 6)


def _memory(call):
    gc.collect()
    tracemalloc.start()
    result = call()  # Keep the returned object alive for retained allocation.
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del result
    return {"retained_bytes": retained, "peak_bytes": peak}


def run_benchmark():
    results = []
    for count in (1000, 10000, 50000):
        entries = tuple(
            AdjacentBookSnapshotEntry(
                f"C:/Books/日本語-{i}.{'jpg' if i % 2 else 'zip'}",
                "image" if i % 2 else "archive",
                ".jpg" if i % 2 else ".zip", str(i), 123, 456,
            ) for i in range(count)
        )
        target = entries[count // 2 + 1].absolute_path
        row = {"entries": count}
        snapshots = []
        for label, cls, projection in (
            ("before", _BeforeSnapshot, _before_image_projection),
            ("after", AdjacentBookBrowserSnapshot, ApplicationController._folder_snapshot_from_browser_navigation),
        ):
            factory = lambda: cls("C:/Books", 1, entries)
            snapshot = factory()
            snapshots.append(snapshot)
            neighbor = lambda: snapshot.adjacent_viewer_path(target, 1)
            membership = lambda: snapshot.contains_viewer_path(target)
            image = lambda: projection(snapshot, target)
            row[label] = {
                "construction_median_ms": _median_ms(factory),
                "construction_allocations": _memory(factory),
                # Batch the cheap indexed operations to reduce clock noise;
                # report per-call medians, not a wall-clock pass threshold.
                "neighbor_median_ms": _median_ms(neighbor, batch=1000 if label == "after" else 1),
                "membership_median_ms": _median_ms(membership, batch=1000 if label == "after" else 1),
                "image_projection_median_ms": _median_ms(image, batch=1000 if label == "after" else 1),
                "neighbor_allocations": _memory(neighbor),
                "membership_allocations": _memory(membership),
                "image_projection_allocations": _memory(image),
            }
        before, after = snapshots
        assert before.adjacent_viewer_path(target, 1) == after.adjacent_viewer_path(target, 1)
        assert before.contains_viewer_path(target) == after.contains_viewer_path(target)
        assert _before_image_projection(before, target) == ApplicationController._folder_snapshot_from_browser_navigation(after, target)
        results.append(row)
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "method": "7 sample medians; prebuilt alternating image/archive entries; tracemalloc separately from timing; after lookup samples use batches of 1000",
        "limitations": "Synthetic string-only measurements, no end-to-end UI or real-device speedup claim. Memory excludes shared input entries; includes derived data and temporary allocation.",
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2, ensure_ascii=True))
