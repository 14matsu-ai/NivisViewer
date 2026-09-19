# Browser startup thumbnail investigation — TODO19 phase 1

2026-09-19. Investigation only; no production change or design approval.

Follow-up: the user clarified that the issue occurs with saved thumbnails.
The current decision and implemented warm-startup fix are in
`BROWSER_WARM_STARTUP_INVESTIGATION.md`; the cold-first recommendation below is
historical and the JPEG prototype was not adopted.

## Conclusion

The measured cold path is dominated by thumbnail generation, executed on one
Browser worker by the default controller. This explains the approximately one
thumbnail per 110 ms appearance in the synthetic JPEG case. It is not evidence
of a per-item UI publication timer. Warm disk-cache results arrive much faster
and can appear together in a paint. Large-directory scan/sort and the remaining
row insertion add a separate startup/responsiveness cost.

Recommend retaining NivisViewer's asynchronous scan, visible-first requests,
generation guards, cache policy and Viewer isolation. First investigate a
target-size JPEG decode fast path, then compare bounded Browser concurrency of
one versus two if necessary. Neither speedup nor visual equivalence of these
proposals has been measured here. Do not simply adopt CPU-count-sized pools.

## Current production path

Source references use repository-relative paths and current working-tree lines.

* `app/browser_scanner.py:245`: background scan collects/stat/classifies entries,
  applies preparation and sorts the completed list (`:408`). Batches of 128
  provide progress; they are not initial thumbnail rows.
* `app/browser_window.py:1116`: scan batches do not publish initial rows.
  Completion (`:1155`) commits the ordered initial tranche (up to 80;
  `:1364`). The remaining rows are appended together after about 120 ms
  (`:1135`). Exact sorted order is preserved; streaming arbitrary rows would
  change selection/scroll and ordering behavior.
* Requests (`:6131`, `:6290`) use a 30 ms scheduling timer, visible items,
  selected item and bounded directional read-ahead. The visible request range
  includes an extra row: 30 requests versus 24 actually visible in this probe.
  Fast scrolling suppresses expensive misses until idle. This timer schedules
  requests; it is not a delay between completed thumbnails.
* `app/application_controller.py:130` constructs a coordinator with two total
  workers. `app/image_work_coordinator.py:38` reserves one for Viewer and leaves
  one for Browser. Viewer interaction pauses new Browser submissions. The
  provider's standalone fallback also has one worker.
* `app/thumbnail_provider.py:964`: disk lookup precedes generation. Maintenance
  runs once before the first lookup; this fresh small-cache probe does not
  establish large-existing-cache maintenance cost. Physical-folder/archive
  prefetch avoids cold generation; image read-ahead can generate.
* `:1545`, `:1620`, `:1670`: image files open directly, folders enumerate/natural
  sort candidate images, ZIP enumerates/natural sorts image names and reads a
  candidate entry before rendering. Folder/ZIP listing happens per container,
  not in the initial top-level scan. RAR/7z use the separate backend path and
  were not benchmarked.
* `app/thumbnail_render.py:484`: `image.copy()` materializes the original image
  before EXIF correction and final downsampling. There is no JPEG `draft()` in
  this path. Decode, copies, orientation and resize are included together in
  the measured generation stage; this report does not claim their individual
  shares. Viewer already has target-aware JPEG paths in
  `app/image_source.py:243,298`, worth evaluating for compatible reuse.
* PDF requests (`thumbnail_provider.py:908`) already request thumbnail-sized
  rendering through the prioritized single-thread `PdfiumService`. Increasing
  Browser workers will not turn PDFium into a parallel renderer. Preserve its
  serialization and document lifetime.
* `thumbnail_provider.py:1170`: persistent encoding/write/index completes
  before final worker publication. `thumbnail_disk_cache.py:233,339` holds its
  lock across cache reading or encoding/writing/index updates. More workers
  may contend here; cache hit time is not just disk I/O.
* `thumbnail_provider.py:1359` and `browser_window.py:6322` reject stale
  generations before memory/model publication. Pending requests are keyed by
  path/size/generation with priority promotion and cancellation. Cancellation
  does not interrupt every native decode: folder/ZIP helper APIs have optional
  cancellation but the current result-loading calls do not pass that token
  into enumeration. Stale-result rejection must not be mistaken for immediate
  resource cancellation.
* Publication is per completed item through `thumbnail_ready`; Qt can coalesce
  paints. No evidence here justifies delaying the first thumbnail to collect
  a UI batch.

## ZipPlaFork primary-source comparison

Reference: <https://github.com/himamon/ZipPlaFork>, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.
Local reference is `../ZipPlaViewer/source/ZipPla`. Although its HEAD differs,
`CatalogForm.cs`, `CatalogForm.Designer.cs`, and `GenerarClasses.cs` were checked
against this revision with `git diff --exit-code` and were identical.
No code was copied or translated for this investigation.

* `CatalogForm.cs:8185,8205`: preparation enumerates file/directory arrays in a
  worker with cancellation checks. It is not proof of an instant streaming
  first viewport independent of directory enumeration.
* `:8733`: two work slots per entry permit header and thumbnail phases.
* `:10914–11194`: active `SetBackgroundMode` orders shown/header/thumbnail and
  hidden work, influenced by which views are visible. The alternative at
  `:10703` is under `#if FALSE`; do not use it as the active implementation.
  `SetWorksOrder` is applied at `:11162`.
* `CatalogForm.Designer.cs:3444` initializes ThreadCount to 1, but runtime
  initialization at `CatalogForm.cs:1874` uses half the CPU count. Active
  scheduling at `:11168,11187` adjusts to idle/full-power or visible waiting
  work and interaction, including process-priority changes. ZipPla is not a
  fixed-single-worker comparator.
* `:5853,6154`: archive thumbnail path tries its persistent thumbnail cache and
  saves generated data. This is not a disk-free generation pipeline.
* `:9024`: each completion checks cancellation, `WorkSetGuid == loadingGuid`,
  item identity and mask before applying results; unused bitmaps are disposed.
  Publication is per result, not a requirement to wait for a whole viewport.

| Approach | Benefit | Cost / assessment |
| --- | --- | --- |
| ZipPlaFork | Adaptive work order and concurrency, separate header/thumbnail phases | CPU-scaled concurrency and process-priority changes are not directly appropriate for current cache locks, shared PDF service or large decode memory. No native comparative timing was performed. |
| NivisViewer | Visible-first bounded requests, async scan, generation checks, Viewer separation | Default Browser lane serializes expensive generation; full-image materialization and whole-list startup barrier remain. |
| Hybrid (recommended) | Preserve current contracts; evaluate target-aware decode and a small visible-work concurrency budget | Requires image-quality, memory, cancellation and Viewer-contention validation. Reuse principles rather than WinForms structure. |
| New design | Separate decode, cache I/O, persistence and batched publication lanes | Can remove contention, but adds ownership/backpressure/shutdown complexity. Current evidence does not justify replacing the complete pipeline. |

## Synthetic measurements

`scripts/benchmark_browser_startup_probe.py`; fresh process for each dataset;
fresh controller/provider for cold and subsequent warm run. Qt offscreen,
1200×800 Browser, 24 actual visible items, default one Browser worker.
2400×3600 deterministic high-detail JPEG (4,613,096 bytes). Images and folder
contents use synthetic hardlinks. Each folder contains 8 images; each ZIP_STORED
CBZ contains the large first image and 7 tiny valid images. PDFs contain one
synthetic image page. This is not a many-entry archive stress test.

All times below are milliseconds, single observations, not statistical claims.

| Dataset | Cold first rendered | Cold viewport filled | Warm first rendered | Warm viewport filled | Cold / warm max heartbeat gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Images, 1,000 | 213.43 | 2833.24 | 92.43 | 275.32 | 37.08 / 59.65 |
| Images, 10,000 | 476.14 | 3370.67 | 370.33 | 611.47 | 86.12 / 78.34 |
| Folders, 32 | 200.94 | 2850.85 | 59.19 | 257.14 | 43.93 / 57.27 |
| ZIP/CBZ, 32 | 199.72 | 2972.09 | 66.49 | 277.05 | 42.68 / 66.54 |
| PDF, 24 | 113.83 | 1153.44 | 63.24 | 250.08 | 41.23 / 52.93 |

In the 1,000-image cold run, 24 completed generation calls total 2502.89 ms,
disk saves 244.99 ms, and pipeline 2766.04 ms. Model publication totals 0.90 ms
(this excludes Qt painting/layout). Generation is about 90% of measured worker
pipeline time, not proof that the decoder alone consumes 90%. Folder/ZIP
generation totals are 2539.39/2668.35 ms and saves 238.35/236.42 ms. PDF generation
totals 879.29 ms and saves 195.06 ms.

At 10,000 images, initial model reset occurs at 220.95 ms cold / 306.08 ms warm,
versus 25.12 / 30.22 ms for 1,000. Sort takes 37.00 / 54.53 ms. Remaining-row
append takes 36.03 / 26.54 ms; subsequent layout/paint is not included in those
slot timings. This is a second bottleneck for large warm directories.

Raw results: `out/todo19-image1000.json`, `out/todo19-image10000.json`,
`out/todo19-folder32.json`, `out/todo19-archive32.json`, `out/todo19-pdf24.json`.
The output directory is local, not a committed benchmark dataset.

Limitations: cold means empty app cache, not flushed OS/storage caches. Warm
reuses the process and disk cache but not provider memory. Repeated payloads,
hardlinks, local storage and a tiny new cache are not representative of every
collection. Explicit viewport renders and heartbeat observation add overhead.
No native app, user media, external archive tools or ZipPla executable was used
for comparative measurement. Initial harness results affected by automatic
home-folder restoration were discarded; restoration is now disabled before
Browser creation. The 10,000-link fixture limit was fixed by using groups of
512 hardlinks. `first_rows` in raw output means remaining-row insertion;
`first_reset` is the initial row publication. All reported runs completed.

## Smallest next phase, pending design review

1. Profile decode/copy/resize separately and A/B a guarded JPEG target-decode
   path for letterbox thumbnails before materialization. Initially fall back
   for smart crop, unsupported formats and uncertain orientation/size cases.
   Preserve EXIF correctness, final dimensions, alpha and cache identity;
   determine whether changed pixel output requires a cache-policy revision.
   Validate file, folder-cover and ZIP-entry inputs. Do not extrapolate this
   proposal to PDF or external archives.
2. If viewport fill remains too slow, compare one versus two bounded Browser
   workers for visible generation while retaining the Viewer lane and pause
   policy. Measure first-thumbnail latency, viewport fill, UI gaps, peak memory,
   cache-lock wait and cancellation on navigation. Avoid CPU-count scaling.
   This is an experiment proposal, not an established speedup.
3. Independently test smaller time-budgeted remaining-row additions for large
   warm folders, preserving final sorted order, selection and scroll. Do not
   start a broad streaming scan rewrite without evidence it improves first
   useful display under those contracts.

Deferring persistent writes can recover only part of the measured cold time
and introduces shutdown/queue/resource work; it is not the first change.
UI result batching is lower priority given the measured model-update cost.
Acceptance must include real-user feel after authorization, plus fresh-process
offscreen quality/cancellation checks and equivalent large-image forward,
reverse, back-and-forth and rapid-input Viewer regression checks if production
resource scheduling is changed. No production patch was made in phase 1, so
unrelated full-suite/Viewer benchmark repetition was not performed.

Validation: probe `py_compile` passed; existing
`tests/test_browser_thumbnail_scheduler.py` passed (7 tests, fresh process).
The fixed-source diff check passed. Five dataset runs each completed both cold
and warm cases. Final harness safeguards reject nonpositive counts, bound folder
hardlink groups as well as image groups, and return failure on measurement
timeout; those safeguards do not alter the measured 32-folder fixture.
