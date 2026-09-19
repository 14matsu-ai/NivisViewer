# TODO19: persisted warm startup and deferred maintenance

Follow-up: the user reports small-folder -> mouse Back -> large-folder GUI
stalls. TODO19 remains unresolved by this initial-startup result alone. See
`BROWSER_NAVIGATION_RESPONSIVENESS.md` for the separately reproduced per-count
GUI full-folder scan, source-index correction, navigation timing and complete
maintenance entry-point audit (including save-count-triggered pruning).

2026-09-19. This supersedes the cold-first recommendation in
`BROWSER_STARTUP_THUMBNAIL_INVESTIGATION.md` following the user's clarification:
the reported problem occurs with thumbnails already saved.

## Finding and implemented change

An existing-cache startup can wait for a whole-cache maintenance pass before
reading its first thumbnail. In the synthetic 10,000-entry cache that pass took
1.11–1.15 seconds. Two Browser workers did not remove this barrier because
maintenance holds the same cache lock as reads.

Removed initial whole-cache maintenance from thumbnail/page-count lookup.
Per-item source/cover/cache validation remains intact. Browser's existing idle
cleanup timer now waits while directory scan or thumbnail requests remain, and
retries if Viewer interaction temporarily rejects Browser work. Daily cleanup,
obsolete-payload retirement, LRU/age limits and explicit cleanup still exist.
There is no new thread, worker-count change, per-format priority, decoder
change, cache identity/schema change or wholesale cache clearing.

This targets the demonstrated maintenance barrier. It is not a claim that all
warm startup latency, including the user's exact machine/data, is resolved.

## Fresh-process method

`scripts/benchmark_browser_warm_startup.py` has separate `prepare` and `measure`
commands. Preparation creates a new marked synthetic workspace and isolated
profile; existing directories are refused. Measurement starts a NEW Python
process for every run, loads persisted config, calls the real controller's
`start()` and lets Browser restore `last_browser_path`. No explicit navigation
substitutes for startup restoration. `reopen_last_on_start` is false, so a saved
Viewer book does not obscure this Browser-only comparison.

The source folder contains repeating JPEG, PNG, AVIF, JXL, folder cover, ZIP,
PDF and WebP entries. All originate from the same 2400×3600 high-detail synthetic
image. Cache population uses the production renderer once per format and
production `ThumbnailDiskCache.put` for each source identity (identical fixture
pixels permit reuse during preparation). Sources and cache stay on local disk
across processes; no OS-cache flushing. Grouped hardlinks reduce fixture disk
cost. Folder covers and ZIP/PDF entry metadata are populated through their
normal loaders. Preparation time is excluded.

1,000 saved entries occupy 2,851,250 payload bytes; 10,000 occupy 28,512,500
bytes. These are genuinely indexed, individually fingerprinted saved thumbnails,
not placeholder database rows. They do not model a multi-GB/high-entropy cache,
network drive, antivirus variation or hundreds of thousands of entries.
Daily-maintenance runs change only the synthetic cache's last-cleanup timestamp.

Restored offscreen geometry exposes 12 actual visible items, unlike phase 1's
explicitly resized 24-item window. Comparisons below use the same restore path
and geometry. Time zero is before controller construction, after Python imports
and QApplication construction; this is not whole-process launch time. Explicit
render observations and method instrumentation add overhead. The extra audit
lock is reentrant and records acquisition wait; native filesystem/SQLite/Pillow
times are not separately attributed. Setup/shutdown are excluded.

## Results

Milliseconds, medians of independent fresh-process runs. Alternating worker
counts used the same persisted dataset and settings. No benchmark changes were
made to production worker configuration.

| Condition | N | First rendered | Visible range filled | Max event-loop heartbeat gap |
| --- | ---: | ---: | ---: | ---: |
| Before: 1,000, maintenance current, Browser 1 | 5 | 133.15 | 144.28 | 29.52 |
| Before: 1,000, maintenance current, Browser 2 | 5 | 129.32 | 140.58 | 29.38 |
| Before: 10,000, maintenance current, Browser 1 | 3 | 370.98 | 385.23 | 38.44 |
| Before: 10,000, maintenance current, Browser 2 | 3 | 345.75 | 359.41 | 35.04 |
| Before: 10,000, maintenance DUE, Browser 1 | 3 | 1447.13 | 1534.31 | 37.78 |
| Before: 10,000, maintenance DUE, Browser 2 | 3 | 1435.88 | 1532.66 | 34.59 |
| Fixed: 10,000, maintenance DUE, Browser 1 | 5 | 346.57 | 359.97 | 35.24 |

Fixed due-maintenance viewport range: 354.46–380.48 ms; original 1-worker range:
1522.41–1561.08 ms. The maintenance-current 10,000/1-worker baseline has a
591.92 ms outlier; small differences between one and two workers should not be
treated as a proven universal concurrency win.

Requested saved thumbnails were full hits and generated count was zero in ALL
reported warm runs. Typically 30 requests completed before stopping, including
read-ahead; this does not mean every one of the 10,000 cache entries was read.
Seeded and restored request cache/family tokens match. The tested policy was
smart crop, 181×256 cache frame, logical edge 180, auto quality margin, WebP q60,
alpha flattened onto #ffffff, implementation version 3. No JPEG prototype
identity is present.

A representative per-item baseline/fixed pair shows PNG 1473.86→333.81 ms,
AVIF 1473.98→334.02, JXL 1481.82→334.09, folder 1497.10→334.24,
ZIP 1518.10→334.58, PDF 1518.25→334.83 and WebP 1526.41→342.35.
These individual observations demonstrate the mixed visible set, not separate
per-format statistical speedups. Warm reads decode cached WebP, not the original
PNG/AVIF/JXL/PDF source pixels.

Cache index initialization was about 3–7 ms. In ordinary 1,000-entry runs,
30 saved-image decodes totaled about 26–34 ms. With two workers, serialized
cache access added lock wait rather than parallel cache decoding. In due runs,
the second worker sometimes waited about 1.1 seconds; occasionally one result
escaped before the maintenance worker acquired the lock, but viewport fill
remained delayed. At 10,000 items, scan/sort before initial model publication
was about 270–293 ms, separate from maintenance.

Actual idle-timer completion check: visible range filled at 391.93 ms; cleanup
started at 1032.66 ms and completed after 1121.92 ms of work. Maintenance was
deferred, not removed. Foreground reads arriving during a later active cleanup
can still wait for the existing lock; incremental maintenance is a possible
follow-up, not part of this change.

## Viewer competition

The same controller/coordinator simultaneously opens an eight-page synthetic
2400×3600 ZIP and advances the offscreen Viewer through all pages. Only the
book-change-to-Browser navigation connection is disconnected in the harness,
so the unrelated synthetic Viewer book cannot replace the measured shelf.
Viewer scheduling, reserved lane, interaction pause, PDF service and generation
behavior remain connected. Browser stays on its expected restored path.

Three fresh processes per condition compared final code against a benchmark-only
reinstatement of the eager cleanup barrier at first cache lookup. This emulation
is explicitly NOT an untouched pre-patch Viewer benchmark. It includes the same
whole-cache operation but its stage timing includes cleanup inside audited get.

| Condition | Browser first | Browser full | Viewer first page | Viewer all 8 pages |
| --- | ---: | ---: | ---: | ---: |
| Eager barrier emulation | 2622.16 | 2704.15 | 353.02 | 683.05 |
| Final deferred cleanup | 1217.69 | 1296.25 | 357.99 | 669.32 |

Viewer first page median is 4.97 ms slower in this small sample, while completion
is 13.73 ms faster; do not claim a statistically established Viewer speedup or
zero possible regression. No stalled page, terminal error or source-format
starvation was observed. Browser waits longer than Browser-only because existing
Viewer-interaction pause behavior is intentionally preserved. In final runs the
idle cleanup starts after Browser fill, at about 1.52–1.54 seconds, and completes.

## Regeneration versus legitimate invalidation

Fresh-process tests establish that valid matching cache entries can be reused
without generation. They do not establish what happened to the user's unexamined
cache or prove that every restart/setting combination reuses every entry.

`thumbnail_disk_cache.py:get_suitable` checks normalized resolved source path,
kind, compatible family and encoding format identity, source size/mtime and
folder-cover size/mtime, cache-file presence and successful decoding. A smaller
compatible artifact can be a provisional placeholder while a larger one is
generated. Source changes, missing/corrupt cache files, changed frame/crop,
quality margin or algorithm version, WebP quality, alpha policy or matte can
legitimately invalidate an artifact. DPI/size changes can reuse sufficiently
large compatible artifacts; they do not unconditionally require regeneration.
Daily maintenance can retire obsolete, missing, expired or over-budget entries.

The probe counts full hits, placeholders, generated/saved items and reports
coarse miss categories (no valid source entry; family/encoding mismatch;
matching policy but invalid data). Removed invalid rows cannot be retrospectively
distinguished by this coarse audit. No miss-reason telemetry was added to the app.
Existing focused tests cover source/cover changes, size/quality/alpha identities
and metadata reuse. Additional diagnosis of a real unexplained miss should log
the requested and stored identity/validation reason, rather than assume a decode
or worker-count problem.

## Design decision and remaining work

The fixed ZipPlaFork `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` active
`CatalogForm.cs:SetBackgroundMode` (10914–11194, not the #if FALSE alternative)
does adapt work order and concurrency. It is relevant, but adding workers while
a cache-wide lock is held did not resolve this measured barrier.

* ZipPla-style adaptive concurrency: worth bounded evaluation for independent
  cold/uncached jobs after identifying bottlenecks; do not copy CPU-count scaling
  or process priority changes wholesale.
* Nivis unchanged: protects current resource/UX contracts but retained the eager
  cleanup barrier.
* Hybrid, adopted: preserve all current queue/Viewer contracts and move whole-cache
  cleanup out of the first visible read, using the existing idle cleanup path.
* New read/I/O/maintenance lanes or incremental cleanup: may help later requests
  during cleanup, but need transaction, lifetime and cancellation design review.

No further worker increase is justified as the first response to the demonstrated
warm barrier. Remaining common-format costs include scan/filter/sort, initial
row publication and remaining-row layout; cache reads are still serialized.
A bounded adaptive 1↔2 comparison can be revisited for a workload where decode
jobs are independent and its benefit exceeds contention/Viewer/memory costs.
TODO19 as a whole is not declared fully resolved. Stop for review before another
phase or TODO20/21.

## Cold JPEG prototype — NOT ADOPTED

The earlier letterbox-only experiment materialized fewer JPEG pixels. Nine
same-process iterations per fresh-process variant gave medians 49.63→29.27 ms
on an odd-size synthetic JPEG; MAE 1.76/255 and PSNR 41.33 dB, so output was
not pixel-identical. Its 56 focused tests covered EXIF1–8, odd/asymmetric fit,
grayscale/progressive/CMYK fallback, owned versus reused images, PNG alpha,
folder/ZIP, malformed input and lazy cache identity.

User clarification arrived before adoption. Its production edits, including
cache identity change, were withdrawn while preserving the patch and prototype
tests/benchmark as non-executable text under `docs/prototypes/`. No JPEG priority
or decoder fast path is shipped here. An initial mixed/Viewer prototype run
navigated the Browser away via normal book synchronization and timed out; it is
excluded from evidence. The corrected warm harness disconnects only that
navigation connection and asserts the final shelf path.

## Validation and artifacts

Syntax checks passed for changed app/test modules and both active probes;
scoped `git diff --check` passed (only repository CRLF notices).

* 50 tests: new warm startup ordering/retry tests, provider, disk cache, scheduler.
* 73 tests in a separate fresh process: alpha policy, compression setting,
  asynchronous Browser navigation. All passed.
* Required offscreen Viewer check: 9 high-detail 2400×3600 JPEG pages,
  1280×900 viewport, 256 MiB cache, completed with terminal errors 0.
  Initial paint 54.752 ms; sequential 1.069, reverse 1.042, direction reversal
  1.349, ping-pong median/max 1.075/1.255, rapid final 1.793 ms. Supplementary
  synthetic results, not native UX evidence.
* Raw outputs: `out/todo19-warm1000-w*-*.json`, `todo19-warm10000-w*-*.json`,
  `todo19-due10000-w*-*.json`, `todo19-fixed-due10000-*.json`,
  `todo19-eager-viewer10000-*.json`, `todo19-final-viewer10000-*.json`,
  `todo19-final-idle10000.json`, `todo19-warm-fix-navigation.json` (all under out).

No native app/input, private media, new dependencies, build, commit or push.
