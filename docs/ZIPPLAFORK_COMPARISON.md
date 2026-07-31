# ZipPlaFork comparison record

## Scope and provenance

This performance audit used ZipPlaFork as an external design reference at:

- Repository: https://github.com/himamon/ZipPlaFork
- Fixed upstream revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`
- Upstream license: AGPL-3.0-or-later
- Upstream copyright notice:
  `Copyright © 2016-2017 Rio's Toolbox`

The following upstream files and history were inspected:

- `source/ZipPla/ViewerForm.cs`
  - original/decoded and display-ready image arrays
  - current/next/previous work priority assignment
  - memory-bound eviction
- `source/ZipPla/GenerarClasses.cs`
  - queued background-work reordering
- `source/ZipPla/PackedImageLoader.cs`
  - archive entry enumeration and optional memory-backed archive access
- `source/ZipPla/BitmapResizer.cs`
- `source/ZipPla/QuickGraphic.cs`
- `source/ZipPla/ImageLoader.cs`
  - `BitmapEx` / `VirtualBitmapEx` display artifacts
- `source/ZipPla/LongVectorImageResizer.cs`
- `source/ZipPla/CatalogForm.cs`
  - visible-range thumbnail scheduling
- base import/core commit
  `5e942ef0006decf01c21d9dbe037dba50e763420`
- visible-thumbnail commit
  `8ea492821efa95ac66483246c5b17fa71b400f0e`
- one-thumbnail-worker commit
  `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`

## Code-use and provenance policy

NivisViewer is a private personal application and is not being prepared for
publication or distribution. Direct copying, translation, and porting from
the fixed ZipPlaFork source is permitted for Viewer performance work. The
former repository rule limiting work to design-only reference has been
removed.

Every direct port must record the fixed revision, source file and method,
ported process, upstream license/copyright, and NivisViewer destination here.
If a later change copies or translates copyrightable source expression, the
affected code must be identified as AGPL-3.0-or-later-derived and the
applicable license text and notices must be retained before that change is
considered complete.

This 2026-07-31 change ports the processing decisions listed below, but does
not copy or translate a C# expression, pixel loop, GDI operation, or upstream
source file. The Qt decoder-sized JPEG implementation is newly written
Python/PySide6 code.

| Fixed upstream source and process | NivisViewer destination | Port boundary |
| --- | --- | --- |
| `source/ZipPla/ViewerForm.cs:3177`, `bmwLoadEachPage_DoWork`: load/decode and create a display-sized result in one background page job | `app/image_source.py`, `open_qimage_at_most`; `app/image_cache.py`, `_ImageLoadTask.run` | Processing structure port. JPEG decoding uses `QImageReader.setScaledSize()` so full source pixels are not materialized first. No source expression copied. |
| `source/ZipPla/ViewerForm.cs:5558`, `SetBackgroundMode`; `source/ZipPla/GenerarClasses.cs:247`, `SetWorksOrder`: replace queued order around the new current page | `app/viewer_window.py`, `_queue_decode_demand` / `_apply_pending_decode_demand`; `app/image_cache.py`, `preload_around` | Processing structure port. A 16 ms request-ID/generation guarded input-idle boundary replaces crossed wheel targets before raster work is registered. No source expression copied. |
| `source/ZipPla/ViewerForm.cs:5451`, `SetNewResizedImage`; `:5471`, `ReduceUsingMemory`: publish completed resized artifacts and evict by a memory bound | `app/image_cache.py`, target-preview byte accounting and LRU; existing `app/viewer_widget.py` prepared-display cache | Algorithm/structure reference. Existing Qt cache contracts and generation checks are retained; no upstream eviction implementation copied. |
| `source/ZipPla/ViewerForm.cs:6438`, `showCurrentPage`; `:6990`, `pbView_PaintToCanvas`: page changes present a completed display-sized artifact | Existing `app/viewer_widget.py`, prepared-display commit/paint path | Processing structure reference. The Qt implementation predates this decoder-size change and remains independently implemented. |

All rows derive from ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later with the copyright notice shown above.

The broader independent adaptations remain:

- keep decoded source artifacts separate from display-ready artifacts;
- prefer current, immediate forward, and immediate backward display units;
- reorder queued work when navigation direction changes;
- use a byte budget as the hard cache bound;
- avoid multiplying workers for memory-bandwidth-heavy image work.

ZipPlaFork is not a runtime or build dependency and none of its binaries or
source files are stored in this repository. This change adds provenance and
attribution but no copied or translated source expression. A later direct
source-expression port is permitted by repository policy, but must update
this boundary and add the applicable AGPL-3.0-or-later license materials.

## NivisViewer-specific implementation

The referenced ideas were adapted to NivisViewer's existing contracts:

- `ImageCache` retains generation and source identity checks.
- `ViewerWidget` applies only a complete single-page or spread display unit.
- The previous complete frame remains visible until the selected mode's final
  render is ready, including `standard`.
- Folder, ZIP, and PDF decode/render stay off the GUI thread.
- Configured forward/backward display-unit counts drive both decoded-source
  prefetch and display-ready prefetch, including `standard`.
- Immediate forward and backward units are planned before farther work.
- Prepared pixmaps can be applied after their raw `QImage` was evicted,
  without accepting stale source generations.
- ZIP cancellation is cooperative and request-local; no shared global cancel
  state was introduced.
- Standard fit-window JPEGs in folders and ZIP files are decoded at no more
  than the physical Viewer size by Qt's JPEG decoder. Logical original
  dimensions remain attached for layout, status, and page modeling.
- Actual-size, manual zoom, non-standard resampling, color adjustment, and
  unsupported image formats retain the full-resolution decode path.
- The magnifier upgrades a decoder-sized JPEG source to full resolution before
  requesting its crop.
- Raster preload registration waits for one 16 ms input-idle boundary after
  the first frame. Rapid wheel input replaces the pending request, and both
  request ID and source generation are checked before starting work.
- Spread cache admission and eviction preserve display-unit boundaries.
- Render-worker failure terminates as an explicit error slot instead of a
  permanent loading placeholder.
- Split PDF pages crop the rendered QImage in physical pixel coordinates for
  0/90/180/270-degree pre-rotated bitmaps while retaining logical half-page
  dimensions for layout.

## Standard navigation re-audit

The selectable-resampling change entered NivisViewer at
`33f7cd0bbf1e9a27eab52b9832e407f6f03c8a5d`; the last committed revision
before it was `8a6c7d411f9268eb2079f16966fc20e5a8715a20`. The prepared-display work was
still an uncommitted local change during this audit. Git history was inspected
with `git show` and `git diff`; no checkout or worktree replacement was used.

Both committed revisions converted every raw-cache `QImage` to a full-size
`QPixmap` in the GUI-thread page-change slot. The newer `standard` path
correctly bypassed Pillow, but it also bypassed the display-ready cache, so a
raw cache hit still paid this conversion on every forward, reverse, and revisit
operation. Repeated paints then scaled the source-size pixmap to the viewport;
rotation additionally transformed the source-size pixmap in each paint.

At the fixed ZipPlaFork revision, static JPEG pages take a different route:

- decode/filter output is held in `PreFilteredImageArray`;
- the worker computes `ResizedSizeArray` and materializes a display-size
  `VirtualBitmapEx` in `ResizedImageArray`;
- `SetNewResizedImage` publishes that completed artifact;
- `showCurrentPage` changes state and invalidates the view without decoding or
  resizing;
- `pbView_Paint` copies the display-size artifact into a reusable viewport
  canvas and presents it without source-size scaling;
- current, immediate-next, and immediate-previous work precede farther work,
  and direction reversal reuses an already-complete previous artifact.

NivisViewer now applies the same high-level separation using native Qt
operations written independently for PySide6:

- `standard` uses worker-side `QImage.scaled()` with the existing Qt
  Fast/Smooth setting and native `RGB32` / `ARGB32_Premultiplied` output; it
  never enters Pillow;
- `standard` never prepares an artifact larger than the rotated/cropped source
  above 100% manual zoom; that source-sized pixmap is reused across completed
  zoom steps and QPainter performs the historical upscale;
- one GUI-thread `QPixmap.fromImage` occurs when a worker result becomes a
  display-ready cache entry, not in the wheel handler or paint event;
- the pixmap records its device pixel ratio, so a physical-pixel render is not
  treated as an oversized DPR-1 image;
- ready navigation swaps the cached display unit by reference;
- cache miss keeps the previous completed page until the final target is ready;
- stale or non-visible results are neither applied nor retained;
- resize debounce keeps the newest page request instead of reviving an older
  or empty snapshot;
- a cold display request is held for one 16 ms input-idle interval, so rapid
  wheel input coalesces to the newest target before native scaling begins;
- superseding a cold request invalidates its pending display generation
  immediately, so a worker already running for the old target cannot commit;
- direction reversal changes the priorities of already-queued prepared tasks,
  while an active display demand retains priority 100;
- partial spread results required by a pending display are protected from
  eviction; an unprotected far request is cancelled and evicted as an atomic
  unit instead of being left permanently half-ready;
- device-pixel-ratio changes invalidate physical-size render planning even
  when the widget receives no logical resize;
- only the current unit's decoded source handle is reattached for magnifier and
  resize use; prepared neighbors do not pin all decoded source rasters.

No ZipPlaFork source expression, pixel loop, GDI operation, or worker
implementation was copied or translated. In particular, NivisViewer does not
use ZipPlaFork's `LockBits`, `CopyMemory`, reusable GDI canvas, WPF
`TransformedBitmap`, or `BackgroundWorker` implementation.

The audit deliberately did not copy ZipPlaFork's vector resizer, background
worker implementation, PDF decoder, archive-in-memory mode, or catalog
thumbnail patch. Those implementations do not map safely and directly to
Pillow, PySide6, PDFium, and NivisViewer's generation-based lifecycle.

## 2026-07-31 offscreen measurements

These measurements used generated in-memory data and offscreen Qt. They are
regression evidence, not a substitute for testing the packaged application on
the user's 4K display and storage device.

### Decoder-sized large-JPEG ZIP comparison

The reproducible `scripts/benchmark_viewer_navigation.py` benchmark uses the
production `ViewerWindow`, a temporary ZIP with 21 distinct entries, Japanese
entry/path names, 4096 x 6500 JPEG pages, a 3840 x 2106 Viewer, single-page
mode, standard fit-window rendering, a 6-forward/4-backward plan, and a
512 MiB decoded-source budget. It does not launch the real application or
generate native input.

Direct decode comparison for the same 4096 x 6500 JPEG payload:

- Pillow full decode: 48.726 ms median.
- Qt full decode: 76.699 ms median.
- Qt JPEG decoder-scaled decode to 1361 x 2160: 26.101 ms median.

Production-window results with decoder-sized JPEG enabled:

- forward: 0.329 ms median handler, 5.294 ms median request-to-paint;
- reverse: 0.282 ms median handler, 5.510 ms median request-to-paint;
- repeated forward/backward: 0.292 ms median handler, 6.055 ms median
  request-to-paint;
- eight uninterrupted requests crossing the prepared frontier: 1.466 ms total
  handler time and 70.297 ms from first request to final-page paint;
- each decoded preview source is about 10.12 MiB; ten were resident
  (101.21 MiB) at the final instantaneous cache snapshot.

The same run with decoder-sized JPEG disabled:

- forward sequence: 0.347 ms median handler, 59.983 ms median
  request-to-paint, 169.851 ms maximum;
- reverse sequence: 0.311 ms median handler, 5.467 ms median
  request-to-paint, 210.486 ms maximum;
- repeated forward/backward: 0.385 ms median handler, 21.128 ms median
  request-to-paint;
- eight uninterrupted frontier requests: 1.296 ms total handler time and
  230.075 ms to final-page paint;
- final decoded cache: six full sources totaling 457.03 MiB.

The 512 MiB full-decode run can retain only about six 4096 x 6500 RGB sources,
so it cannot keep the configured 11-page working set resident. This is itself
the observed working-set difference; the sequence timings must not be read as
an all-ready cache microbenchmark. Decoder-sized mode can retain the complete
planned window and avoids that raw-raster pressure. Before decode-demand
coalescing, the decoder-sized rapid-frontier result was 148.747 ms; starting
only the latest idle target reduced it to 70.297 ms.

- Source raster: 4096 x 6500 RGB888 (76.17 MiB)
- 4K-fit target: 1361 x 2160 (8.41 MiB output)
- Previous unconditional RGBA boundary:
  - 101.56 MiB intermediate raster
  - 319.404 ms high-quality render in the same comparison run
- Format-preserving RGB boundary:
  - 76.17 MiB intermediate raster
  - 202.855 ms high-quality render in the same comparison run
- Exact-target fast path: 0.039 ms, no Pillow conversion
- Current cold renders:
  - moire reduction: 136.059 ms
  - high quality: 205.385 ms
  - smooth: 123.854 ms
  - pixel: 64.886 ms
- GUI `QPixmap.fromImage` for the full source: 33.825 ms
- Repeated smooth 4K paint: 5.832 ms median in that run
- Preparing 11 configured high-quality display units:
  - 11 worker requests and 11 Pillow conversions
  - 1920.280 ms total
  - 123.36 MiB retained display-ready pixmaps
- Applying 17 already-ready forward/reverse targets:
  - 0.035 ms median, 0.055 ms p95
  - zero new workers and zero new Pillow conversions
  - one atomic commit per navigation
- A cold non-standard miss: 173.143 ms to atomic commit, one worker and one
  Pillow conversion
- Process working-set peak in the render benchmark: 426.95 MiB. The 20 logical
  pages shared one injected source raster, so this is intentionally not an
  estimate of a real 20-page decoded-source cache.

### Standard ready/cold comparison

An additional temporary benchmark created a ZIP containing 21 actual
4096 x 6500 JPEG entries (832,263 bytes each; 17,479,603-byte ZIP). Temporary
scripts, archives, and timing output were removed after the run.

- JPEG `Image.load()` decode, 20 samples:
  - 69.03 ms median
  - 74.44 ms p95
- Before the fix, with every source already decoded:
  - wheel handler: 15.89 ms median, 16.63 ms maximum
  - request to paint: 21.71 ms median, 23.11 ms maximum
  - full-source `QImage` to `QPixmap`: 21 conversions for 21 requests,
    approximately 15.78 ms each
- After the fix, with the configured 11-page working set ready:
  - `standard` wheel handler: 0.055 ms median, 0.218 ms maximum
  - `standard` request to paint: 0.961 ms median, 1.364 ms maximum
  - 32 prepared hits, zero misses, zero navigation-time workers, zero
    navigation-time `QImage` to `QPixmap` conversions
  - ten uninterrupted wheel handlers: 0.837 ms total, 3.214 ms to the
    coalesced final paint, ten state commits and one paint
  - `high_quality` wheel handler: 0.058 ms median, 0.192 ms maximum
  - `high_quality` request to paint: 0.910 ms median, 1.153 ms maximum
- Cold `standard` display-size miss with the source raster already decoded:
  - wheel handler: 0.140 ms median, 0.177 ms maximum
  - request to paint: 19.66 ms median, 74.08 ms maximum
  - zero navigation-time `QPixmap` conversions; one display worker was active
    while the previous frame remained visible
- Working set in the final ready-standard run:
  - 125.04 MiB after source conversion
  - 283.11 MiB with 11 display-ready pixmaps
  - each 1361 x 2160 32-bit pixmap accounts for about 11.21 MiB; the 11
    retained artifacts account for about 123.36 MiB before Qt allocator and
    widget overhead
- Wide-page split preparation at 4096 x 6500:
  - two GUI-thread half-image deep copies: 11.492 ms median
  - two shared `QImage` handles with worker-side crop: 0.0028 ms median
  - both split identities retain the source cache key; crop range remains part
    of the render key

A final rerun after the cache-eviction, priority, DPR, and lifecycle boundary
fixes used the same 21-page, 4096 x 6500 generated JPEG ZIP and an 11-page
prepared working set:

- `standard`:
  - wheel handler: 0.258 ms median / 0.569 ms maximum
  - request to paint: 5.162 ms median / 8.428 ms maximum
  - 32 ready hits, zero misses, zero navigation-time workers, and zero
    navigation-time QImage-to-QPixmap conversions
  - ten uninterrupted handlers: 0.953 ms total; 2.206 ms to the coalesced
    paint
- `high_quality`:
  - wheel handler: 0.170 ms median / 0.721 ms maximum
  - request to paint: 1.848 ms median / 7.124 ms maximum
  - 32 ready hits and zero navigation-time workers/conversions
  - ten uninterrupted handlers: 1.708 ms total; 8.146 ms to the coalesced
    paint
- process working set was approximately 125 MiB after one decoded source and
  284 MiB after the 11 display-ready artifacts.

The navigation portion intentionally reused one decoded `QImage` for all
logical page identities so it could isolate display-artifact costs. Its working
set is not an estimate for retaining 21 independently decoded source rasters.

These results isolate display preparation from archive decode. A true cold ZIP
request also includes the measured JPEG decode time and storage/archive
latency, but neither runs in the wheel handler or paint event.

### Final production-window navigation

The final offscreen measurement used the production `ViewerWindow`,
`ViewerWidget`, and `ViewerRenderTask` path at a 3840 x 2106 viewport with 21
raw-ready 4096 x 6500 RGB888 pages. Decode was intentionally excluded here so
input-handler, display preparation, and paint could be counted independently.

- One cold `standard` page:
  - 1.908 ms input handler in the latest confirmation
  - 56.893 ms request to paint, including the 16 ms input-idle boundary
  - one demand resize, one target-size `QPixmap`, one commit, and one paint
- Ten ready pages forward:
  - 1.380 ms median / 1.948 ms maximum input handler
  - 7.463 ms median / 8.868 ms maximum request to paint
  - zero demand workers, resizes, and `QImage` to `QPixmap` conversions
- Four ready pages in the reverse direction:
  - 1.287 ms median / 2.238 ms maximum input handler
  - 5.675 ms median request to paint
  - zero demand workers and conversions
- Six already-viewed forward/backward operations:
  - 1.414 ms median / 3.060 ms maximum input handler
  - 9.210 ms median request to paint
  - zero workers and conversions
- Six uninterrupted requests crossing the ready frontier:
  - 0.391 ms median / 0.923 ms maximum input handler in the latest
    confirmation
  - 2.753 ms total handler time
  - one demand worker and one target-size `QPixmap`
  - intermediate cold pages 10 and 11 produced no worker, commit, or paint;
    only the latest page 12 was materialized
  - 55.354 ms from the first request to the final paint

The rapid test was repeated with 21 distinct physical QImage buffers
(approximately 1599.61 MiB of raw raster data): handler median was 0.554 ms,
maximum was 1.161 ms, total was 3.888 ms, and only the latest cold target
created a worker and pixmap. Rolling prefetch began only after that target had
committed and painted. Shutdown left zero active render threads, tasks, pending
demands, or active demand/prepared timers.

## Additional whole-application improvements

- Browser thumbnail memory hits now pass separate implicitly shared `QImage`
  handles through the provider and model. Qt copy-on-write still isolates
  later mutation, while removing two GUI-thread deep pixel copies per hit.
  A 1024 x 1024 RGBA comparison measured 0.5210 ms per deep copy versus
  0.0045 ms per shared-handle construction.
- Direct, non-recursive folder books use `os.scandir()` metadata and filter
  extensions before asking whether an entry is a file. Recursive listing,
  symbolic-link behavior, natural sorting, and disappearing-entry handling
  keep their previous contracts.
- ZIP entry reads remain seekable for Pillow but now write directly into one
  size-preallocated `BytesIO`, checking request-local cancellation between
  1 MiB reads. For a 48 MiB incompressible payload, six-run medians were
  31.611 ms / 50.002 MiB peak Python allocation for stored ZIP and
  66.984 ms / 52.033 MiB for deflate. The previous chunk-list plus final join
  measured 32.635 ms / 96.008 MiB and 62.458 ms / 96.008 MiB respectively.
  `tracemalloc` does not include native zlib or OS allocations.
- In a deterministic fake-reader cancellation comparison, the new ZIP path
  reported `process_cancelled` in 4.235 ms median (4.327 ms p95), versus
  144.261 ms completion wait for an equivalent loop without cancellation
  observation points.

## Confirmed follow-up work

The following issues were measured or statically confirmed but deliberately
left outside this page-navigation change:

- Non-`standard` manual zoom still asks Pillow for the full zoomed output.
  A 4096 x 6500 page at 8x is about 6.35 GiB at 4 bytes per pixel before DPR;
  DPR 2 can make the requested area about 25.4 GiB. A safe solution needs
  viewport cropping/tiling or a documented quality-preserving cap.
- The single `Viewer cache最大メモリ` value is currently assigned separately
  to raw `ImageCache` and prepared-pixmap cache. Their combined retention can
  approach twice the displayed setting, plus current/task references.
- If a source-sized standard render is still running when zoom changes again,
  the stale generation is rejected correctly but the latest generation may
  repeat that one preparation. Completed source-sized artifacts are reused.
- When the page-list dock is visible, its legacy thumbnail path can still
  perform a large-image Smooth scale on the GUI thread. It is independent of
  the normal hidden page-list navigation path and should be moved to a worker
  in a separate change.
