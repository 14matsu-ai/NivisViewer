# Large-folder history navigation responsiveness (2026-09-19)

The user reports a regression: returning with a mouse side button from a small
folder to a previously responsive large folder is slow, including clicks and
scrolling. TODO19's earlier restored-viewport benchmark did not establish that
this experience was fixed. This investigation uses synthetic files only.

## Observed blocking path and bounded correction

On the real global `py -3.11`, the small-folder -> BackButton -> 10,000-item
folder path stalls the GUI even when full cache maintenance is NOT due.
A sampling thread captured the GUI in:

`_emit_memory_hit -> _on_page_count_ready -> BrowserItemModel.set_page_count
-> _key / ntpath.normpath / pathlib.__fspath__`.

Every delivered page count walked every source item, including already equal
counts. Warm thumbnail results arrive together after history restoration,
so many O(folder size) GUI operations ran in one event-loop turn. Hidden-source
`page_count` lookup also had a linear fallback. In measured runs, 50 updates
took 66.04 ms at 1,000 rows and 662.38 ms at 10,000 rows. This identifies a
reproducible blocking mechanism, not the exact revision that introduced the
user's reported regression or proof of every bottleneck in their private folder.

`BrowserItemModel` now replaces its existing source-key membership set with a
source-position index. Full model publication and rename rebuild it; progressive
and final appends extend it; new generations clear it. Count updates/lookups
resolve just the requested path, including filtered-out sources. Duplicate
source-path update semantics and Windows case normalization are preserved.
This adds O(N) index memory, replacing the prior O(N) set. It removes the
O(results * folder size) delivery cost without changing sort/filter results,
history restoration, Qt layout contracts, thumbnail identity, cache transactions,
worker lifetime, capacity policy or generations. Existing unrelated tag changes
in this dirty file are retained.

No worker-count change or JPEG-only optimization was made. Additional workers
would not remove this GUI-side scan and could deliver count bursts faster.

## Reproduction and results

`scripts/benchmark_browser_navigation_latency.py` only accepts the marked
synthetic TODO19 fixture. It visits the mixed large folder, waits for its pending
work, visits three synthetic JPEGs, and sends an offscreen Qt BackButton event
through the real Browser event filter. It then repeatedly scrolls and clicks
the viewport. A 5 ms heartbeat measures GUI event-loop gaps; stage wrappers
record thread identity and maintenance invocation timestamps. A separate
sampler captures GUI stacks when the heartbeat is delayed over 80 ms.
No native input, real GUI, private media or OS cache flushing is used.

These are individual diagnostic runs, not statistically established latency
bounds. Time includes instrumentation and synthetic layout. Input-call timing
measures Qt processing, not physical input-to-screen latency. Heartbeat gaps
also expose periods when queued input could not run.

| 10,000 mixed rows, global Python | Max GUI gap ms | Count updates total ms | Row publication after Back ms | Click/scroll calls |
| --- | ---: | ---: | ---: | ---: |
| Before, maintenance current, Back at 2.8 s | 197.98 | 662.38 (50) | 271.00 | 61 |
| After, same condition | 49.39 | 3.54 (50) | 309.51 | 62 |
| Before, maintenance due, Back around 1.1 s | 203.84 | not instrumented | 302.37 | 72 |
| After, maintenance due, Back around 1.1 s | 56.40 | 2.52 (50) | 286.59 | 74 |
| After, Back before cleanup around 0.6 s | 47.59 | 2.16 (50) | 269.95 | 81 |
| After, Back after cleanup + Viewer | 64.46 | 2.17 (50) | 348.87 | 61 |

The primary before/after current-maintenance pair both reports 147 disk hits,
zero generated thumbnails and one due check without a prune. The earlier due
baseline also generated the three small-folder thumbnails; it is supporting
evidence rather than a perfectly controlled pair. Folder scan/sort and atomic
history layout still take time: this fix improves responsiveness, not all row
publication latency. Scan commit is roughly 45-55 ms in these 10,000-row runs.

The after-cleanup Viewer run decoded and displayed all eight synthetic pages
in 524.37 ms from navigation, with Browser requests and pause signals intact;
only Viewer-to-Browser folder synchronization was disconnected to keep the
measured shelf in place. This is a coexistence check, not a before/after Viewer
performance claim.

An isolated empty-cache profile exercises actual misses: 73 generated results,
zero disk hits, 61 input calls, maximum GUI gap 53.31 ms and input-call duration
3.15 ms. Initial cold preparation took about 6.26 seconds before the measured
Back navigation; thus this run does NOT establish low cold-thumbnail latency.
JXL is absent in this environment and its existing failure fallback is retained.
The first miss harness attempt navigated before the small scan committed and
ended on three rows; `navigation-after-misses.json` is INVALID and excluded.
The harness now waits for that commit and fails if final rows/input count are
wrong. Valid output: `out/navigation-after-misses-valid.json`.

Global runtime: Pillow 12.1.1, PySide6 6.11.1, no pillow_jxl. Existing .venv311:
Pillow 12.3.0, PySide6 6.11.2, plugin present. The same due/history probe in the
venv reports 56.35 ms maximum gap and 2.32 ms for 50 count updates. Dependencies
were only inspected, never installed or repaired.

Raw results: `out/navigation-before-history-after-clean.json`,
`out/navigation-after-history-clean.json`, `out/navigation-before-1000.json`,
`out/navigation-before-history-during.json`, `out/navigation-after-history-during.json`,
`out/navigation-after-before-cleanup.json`, `out/navigation-after-cleanup-viewer.json`,
`out/navigation-after-venv.json` and the valid misses result above.
The original `navigation-before-during.json` used an empty starting folder;
cache initialization had not happened, so it does NOT test full cleanup overlap.

## Cleanup/expiry entry-point audit

| Entry point | Trigger and work |
| --- | --- |
| Browser constructor -> `_run_idle_cache_cleanup` | One 1,000 ms timer per new Browser. Retries at 500 ms while scan/thumbnail work is pending or submission is refused. Successful submission does not schedule a navigation-driven repeat. |
| Provider `cleanup_caches_async(force=False)` -> `cleanup_if_due` | Touch active requested tokens, check daily timestamp; run full prune only when due and cache is initialized. |
| Changed `thumbnail_cache_max_unused_days` setting | `force=True`; no such change occurs on ordinary folder navigation. |
| Changed `thumbnail_cache_limit_mb` setting | Queue `prune()` directly. Ordinary navigation does not reapply it. |
| `ThumbnailDiskCache.put` | Every 32 successful saves calls full `prune(remove_orphans=True)`, independent of daily timestamp. This CAN repeat during cold generation; it is not an expiry check per folder. |
| `get`, `get_suitable`, `get_page_count` | Per-request source/cover/cache validation. Not a full-cache expiry walk. |
| Cache initialization/statistics | Initialization computes aggregates on a worker. Statistics/usage getters lock but return cached aggregates; Browser navigation/paint does not call them. Settings UI can still wait on that lock. |

Current-maintenance history run: three navigation invocations, one due check
at 989 ms, zero full passes. Due run: three navigations, one due check/full pass
at 1006 ms lasting 1424 ms; Back at 1108 ms overlaps it. Before-cleanup run:
one full pass starts 995 ms and lasts 1122 ms, Back was 597 ms. No per-folder
expiry repetition was observed. Empty-cache run: three prunes at 4659, 9503 and
10030 ms, two caused by save-count thresholds and one daily cleanup. Distinguish
these from one `cleanup_if_due` call; reporting only that call would miss them.

Whole-cache pruning still holds the disk-cache lock and occupies a Browser
worker for over one second at 10,000 entries. It can delay new thumbnails,
and settings statistics can wait. The reproduced GUI freeze stacks were in
page-count application, not that lock, so this bounded fix does not rewrite
maintenance or move its stall to another timer. Very large caches, network
storage and much larger folder layout remain limits requiring separate evidence.
The user's real-machine experience remains the acceptance criterion for TODO19.

## History Back/Forward versus tree navigation (2026-09-19)

The user then reported a route difference: opening a large folder from the
tree showed thumbnails promptly, while mouse Back/Forward first showed an
incomplete thumbnail surface. The route difference was in `BrowserWindow`'s
`atomic_restore` flag. Back/Forward set it so the old code used
`initial_count = len(items)` and synchronously created/layouted every row before
the first paint. Tree activation used the normal bounded initial tranche and
appended the remainder. This changed the time at which the first visible
thumbnail request could run, even though both routes used the same scanner,
generation and thumbnail scheduler.

The fix keeps history selection and scroll restoration. For an atomic history
scan, the initial tranche now includes the normal first-screen safety range,
the saved viewport range calculated from the saved vertical offset, and the
saved selected item when present. The remainder is appended through the
existing generation-safe batch path. The full list still appears before the
scan completes, and the final location restore remains in place. It does not
discard the saved position or force decode of the whole folder.

Same synthetic persisted 10,000-item mixed folder, global `py -3.11`, warm
cache, offscreen Qt. Values are individual diagnostic runs and include the
existing instrumentation. The benchmark sends an actual Qt BackButton for the
history route; the tree-equivalent route calls the same `navigate_to` body used
by the tree confirmation handler. The exact physical tree widget was covered
by the existing click-confirmation tests.

| Route | Initial visible request | Visible ready observation | First rows observable | Max heartbeat gap |
| --- | ---: | ---: | ---: | ---: |
| Before history (pending-count heuristic) | 475 ms | 545 ms | 282 ms | 129.59 ms |
| Before tree (same heuristic) | 368 ms | 368 ms | 368 ms | 147.14 ms |
| After history (actual VISIBLE provider request) | 244 ms | 367 ms | 367 ms | 154.73 ms |
| After tree (actual VISIBLE provider request) | 266 ms | 365 ms | 365 ms | 142.62 ms |

The before request column is explicitly labeled a heuristic: it only noticed a
nonzero pending count and could miss synchronous memory hits. The after column
records the actual `ThumbnailPriority.VISIBLE` provider call. Therefore the
strong evidence is the route's initial model behavior and the corrected actual
request timing, while visible-ready time remains close in this warm-cache
fixture. The user-visible gain is avoiding a full 10,000-row reset before the
first history paint. Selection/scroll regression tests pass for selected and
unselected saved anchors; repeated back/forward and tree mouse routes remain
covered by the navigation window suite.

Raw route probes: `out/route-history-before.json`, `out/route-tree-before.json`,
`out/route-history-final.json`, `out/route-tree-final.json`,
`out/route-history-after-request.json` and `out/route-tree-after-request.json`.

### Sparse filter correction (2026-09-19)

The bounded history tranche also has to account for an active browser filter.
The scanner's `items` sequence is the unfiltered sorted source, while the
first model reset publishes only matching rows. With a sparse rating or tag
filter, using the saved scroll row directly against the source sequence can
leave too few matching rows in the first reset; Qt then clamps the scrollbar
and the saved anchor appears only after the remainder is appended.

`_initial_restore_scan_item_count` now counts matching rows, calculates the
saved viewport in filtered-row coordinates, and walks the source only until
that filtered last row is present. The cutoff remains bounded by the saved
viewport (and selected item when one exists); it does not materialize the
whole directory. A regression test uses 600 source files with sparse ratings,
no selection, a deep scroll, and both actual Back and Forward restores. It
checks the first paint's anchor for each route while confirming the initial
filtered model is still partial.

## Large-folder return listing snapshot (2026-09-20)

The remaining slow case was different from thumbnail work. Small folders were
already fast, and thumbnails requested normally after the list appeared. With
about 15,000 entries, the first useful row waited for directory preparation,
metadata conversion, sorting and the final model publication. The existing
diagnostic probes showed that the GUI-side reset was the visible cost: warm
1,000-row data reached its first reset at 71.38 ms and first visible render at
133.45 ms; warm 10,000-row data reached its first reset at 276.43 ms and first
visible render at 465.13 ms. The 10,000-row sort itself was about 34.36 ms,
so adding thumbnail workers would not address this return-navigation wait.

`BrowserFolderSnapshotCache` now keeps completed, sorted folder items for the
current session. It is keyed by normalized path, visibility policy and sort
policy, uses an LRU budget of at most 60,000 total items and an estimated 96 MiB,
skips an individual snapshot that exceeds either limit, and does not retain an
empty result because it provides no return-list benefit. It never pre-scans an
unvisited folder and is cleared when disabled. The settings dialog exposes the
session-only option `フォルダ一覧をメモリに一時保存する` and a total item
cap with choices 15,000 / 30,000 / 60,000 / 120,000 / 240,000; the default is
60,000. The cap changes the LRU retention immediately, while the 96 MiB safety
budget remains internal and always applies. The UI shows rounded conservative
estimates of about 12 / 23 / 46 / 92 / 184 MiB for those choices; actual
retention can stop earlier at the internal safety cap. A compact circular `?`
button follows the option label, with a concise multi-line tooltip and a
click-open detailed help dialog.

When Back/Forward or another return navigation finds a snapshot, the current
folder is committed immediately and only the first visible tranche is reset.
The rest is appended in bounded chunks through the event loop, then a normal
scanner refresh reconciles additions, deletions, metadata and sort changes.
Selection, saved scroll position, generation checks and the existing thumbnail
cache remain separate. File operations are held while this provisional listing
is being reconciled, so a stale snapshot cannot authorize rename, move,
recycle, copy or create actions. A missing or failed refresh leaves the cached
view visible and reports the normal access status; it does not treat the
snapshot as authoritative filesystem state.

The reconciliation completion also schedules the bounded visible-range
thumbnail pass. This is required when the refresh finishes before the
snapshot's first-paint callback: that callback is correctly rejected by the
newer scan generation, but rejecting it must not leave the restored rows
without a request. Provider memory/disk hits still return asynchronously and
uncached items use the same visible, selected, read-ahead and safety plan as
tree navigation; the fix does not enumerate or decode the whole folder.

Focused coverage is in `tests/test_browser_folder_snapshot_cache.py` and
`tests/test_browser_navigation_window.py`. The latter checks that a 160-item
history return publishes a partial cached model before reconciliation and then
settles on the complete refreshed listing. The combined focused regression set
for cache, navigation, async navigation, settings, help presentation, UI
language and directory watching is 179 passed; changed modules also pass `py_compile` and
`git diff --check`.

## Verification

- New count-index tests cover filtered sources, both append routes, duplicate
  inputs, case-insensitive rename, reset/stale generations and bounded path
  resolution for 30 results among 10,000 sources.
- Index/model/page-count/metadata/tags: 84 passed in .venv311.
- Async navigation/history/warm startup/download refresh: 79 passed in .venv311.
- Index/model: 15 passed in the actual global `py -3.11`.
- Changed Python files compile. Standard nine-page 2400x3600 high-detail Viewer
  forward/reverse/reversal/ping-pong/rapid offscreen evaluation completed with
  zero terminal errors: `out/browser-page-count-index-navigation.json` and
  `out/history-route-navigation-filtered.json`.
- Latest focused navigation window suite: 33 passed; related navigation,
  async, warm-start and page-count suites: 76 passed; base navigation/model:
  20 passed. The sparse-filter Back/Forward regression is included in the
  33-test window suite.
- No install, real app/native input, user media, build, commit or push.
