# Cached filtering and clearing

When `browser_folder_snapshot_cache_enabled` is ON, BrowserItemModel retains
the current unfiltered ordered list and its path-to-row map when filtering
starts. Clearing search/rating/tags can then restore both references directly,
without evaluating every predicate, sorting, or rebuilding every path key.
The existing Qt model reset and Browser selection/viewport restoration remain.
This does not bypass filesystem watcher reconciliation or rescan the folder.
Filter transitions also select matching items directly from the saved ordered
view, skipping redundant sorting and artifact screening. This fast path is
deliberately confined to configure_filter; mutation paths always use fresh data.

Only one current-folder view is retained, at most the configured
`browser_folder_snapshot_cache_max_entries` count. Existing immutable item
objects are shared; no image buffers or files are copied. This is a separate
temporary view-reference cache, not an additional folder in the snapshot LRU.
OFF or a lower limit releases the saved view immediately. A scan in progress
does not capture a view. Directory/visibility refresh, source additions,
sort/random-seed changes and physical rating/tag renames invalidate it.
Invalidated views use the ordinary rebuild path until an unfiltered view can
be captured again. Mutation handling adds no extra scan or second sort and
keeps the existing thumbnail reuse. Page-count metadata patches only the
affected cached row via its path index, including hidden rows; it does not
invalidate the full view or walk the folder per thumbnail. Counts do not
participate in the current filter/sort policies.

Validation: synthetic current-view tests cover repeated filtering/clearing,
disabled/over-limit fallback, changing the setting while filtered, current
sort/random order, scans, renames, metadata changes and source replacement.
Existing Browser model/tag/Esc behavior is checked alongside them.

`scripts/benchmark_browser_filter_restore.py` measures five filter/clear cycles
over 30,000 synthetic items, with identical inputs and no filesystem scan.
Before adding filtering reuse, observed median model-clear time: OFF 179.645 ms, ON 0.016 ms; filtering
53.087 ms versus 51.379 ms. These are model-only times: Qt layout, thumbnails,
painting and real Windows interaction are excluded. They are not a claim that
the complete screen refresh takes 0.016 ms.

With filtering reuse integrated, the same 30,000-item search benchmark measured
OFF 57.401 ms / ON 12.962 ms for filtering, and OFF 200.057 ms / ON 0.020 ms
for clearing (five-cycle medians; model only). 217 targeted tests passed,
including changed filters after tag/rating edits, source replacement, scans,
sort changes, hidden-row page-count updates and disabled/over-limit fallback.
The offscreen 12-page 3200x5000 JPEG ZIP navigation check settled all seven
scenarios (`out/filter-apply-navigation-check.json`). No native app was launched.
