# PDF loupe: immediate entry and independent artifacts — 2026-09-12

## Scope and cause

The user confirmed that delay is most noticeable when entering magnification.
This follow-up retains the correctness work in `PDF_MAGNIFIER_FIX.md` and the
existing Browser changes. The earlier document records its own historical tests;
the active PDF promotion implementation is now the independent path below.

At the start of this task, single-page entry queued an enlarged whole-page
fallback on the resize worker, or waited for source hydration when only a
prepared QPixmap remained. Spread entry also waited for enlarged page artifacts.
PDF promotion altered the normal `ImageCache` render spec, which could cancel
normal work, advance its generation and clear its cache. Exiting restored the
normal spec, causing another invalidation. These are structural sources of
entry latency and repeated work, even after the previous correctness fixes.

## Four-way comparison

| Design | Evaluation |
| --- | --- |
| Starting NivisViewer | Keeps current UX and safe PDF service, but waits for worker-built fallback and couples lens resolution to normal cache lifetime. |
| ZipPlaFork approach | The project's existing fixed-revision comparison, section 44, documents separate page canvases and offsets in a shared spread coordinate space. That is useful existing geometry, but is not evidence that PDF entry can bypass canvas preparation or that its PDF lifetime model should replace ours. No fresh source inspection or port was performed here. |
| Hybrid — chosen | Preserve current single/spread coordinates, controls, final filters and serialized PDF service. Immediately paint retained display pixels; use a separate bounded final-artifact cache for asynchronous PDF promotion. |
| New region/tile renderer | Could reduce large-page promotion work, but introduces new region admission, edge/filter and caching contracts. Deferred as requested; not required to remove the measured entry dependency. |

No new ZipPlaFork code was copied or translated. Existing provenance and notices
remain in `ZIPPLAFORK_COMPARISON.md` and `licenses/ZipPlaFork`.

## Implementation

- `ViewerWidget` snapshots implicitly shared, already painted QPixmaps. Entry
  immediately becomes active and paints only the visible source rectangle with
  a cheap transform. No QImage transfer, full enlarged fallback allocation,
  PDF work or fallback resize job is needed. The temporary image can look coarse.
- Single-page normalized coordinates and spread page placements/gutter remain
  the same before and after promotion. Movement only changes the crop/offset.
  A new final image changes pixels, not the selected point or zoom geometry.
- New `PdfLoupeCache` owns exact final-artifact identities: document ID, page,
  split side, output dimensions, rotation, DPR, filters and image adjustments.
  It uses one bounded worker lane and the existing serialized `PdfiumService`.
  PDF rendering, pixel conversion, adjustments and the existing `render_qimage`
  final filters happen off the GUI thread. GUI publication creates the QPixmap.
- Only finished final artifacts are cached; promoted PDF source rasters are
  transient worker data. Normal `ImageCache` images, specs/generation and normal
  prepared display surfaces are not replaced by lens-resolution content.
- Cancelling the lens removes its live bindings, but lets a still-useful current
  promotion finish for reuse. Reopening the identical target shares pending or
  completed work. Page changes cancel obsolete tasks; book replacement and
  shutdown cancel all owned tasks and clear the book's lens cache.
- `PdfImageSource` groups cancellation by purpose. Normal ImageCache cancellation
  cannot cancel a lens job, and a lens token never cancels normal work. Document
  close still cancels every purpose and runs through the serialized PDF service.
- Queued/late results must match the current source, adjustment policy and exact
  live widget request. They cannot activate a cancelled lens. Promotion failure
  leaves the temporary view interactive; a subsequent reopen may retry.
- Brightness/contrast/gamma use the exact existing adjustment implementation,
  extracted into `image_adjustments.py` and shared with ImageCache. No new filter
  implementation, dependency or change to the controls was introduced.

## Memory and lifetime

The reusable lens LRU is capped at **160 MiB and 8 entries**, and further limited
to the space left in the configured Viewer cache budget after normal source and
display caches. Normal caches are not cleared to admit lens entries. Shrinking
that allowance evicts reusable lens entries first.

Live single/spread lens artifacts retain the existing aggregate **32-Mpixel**
ceiling (integer-edge rounding allowed), including after DPR changes. A live
surface may remain while its reusable cache entry is evicted; this is bounded
working memory, separate from the reusable allowance. Ready pixmaps are shared
between cache and live bindings, not copied. Temporary fallback pixmaps share
normal display storage; filter/zoom changes can retain the previous sharp lens
until replacement. These limits describe retained surfaces, not peak process RSS.

One full-page PDF render remains subject to the existing 64-million-pixel and
32,768-edge limits. PDF byte buffers, Pillow/QImage conversion and final filtering
can coexist transiently on the worker. No peak-RSS improvement is claimed.
For a split wide PDF, cold final artifacts currently render the full page for
each half before cropping; this preserves quality and geometry but is not a
tile renderer or a raw-page sharing optimization. Exact cached halves are reused.

## Comparable before/after observations

`tests/test_pdf_loupe_performance.py` uses the same generated four-page vector
PDF, 640×480 offscreen Window, 2× lens, and a PDF worker release scheduled at
150 ms. It records first magnified paint separately from final sharp paint.
Each row is one observation, not a statistical/native performance result.

| Current display | First magnified paint, ms before → after | Sharp paint, ms before → after | Generation changes across two enter/exit cycles | PDF render calls on reopen/exit cycle |
| --- | --- | --- | --- | --- |
| Single, attached QImage | 13.961 → 1.044 | 202.719 → 160.463 | 4 → 0 | 6 → 0 |
| Spread, attached QImages | 19.303 → 1.191 | 244.386 → 217.782 | 6 → 0 | 9 → 0 |
| Single, QPixmap only | 218.969 → 1.119 | 225.189 → 162.432 | 4 → 0 | 6 → 0 |
| Spread, QPixmaps only | 14.121 → 1.149 | 237.084 → 192.392 | 6 → 0 | 9 → 0 |

All four final cases were active when the entry handler returned. Every normal
cache key present before entry remained unchanged afterwards. Normal prefetch
may add entries; that is not a cache replacement. Total first-cycle PDF calls
were 6→3 for single and 9→2 for spread. Counts include normal deferred work:
the final single case includes two useful normal prefetch renders and one lens
render. Reopen counts are zero, rather than a timing-based inference of reuse.

## Verification and commands

Python 3.11.9, PySide6/Qt 6.11.2; existing environment, no installs or repairs.
Commands run from the repository root. Tests force `QT_QPA_PLATFORM=offscreen`.

```powershell
.venv311/Scripts/python.exe -m pytest tests/test_pdf_loupe_performance.py -q -s
.venv311/Scripts/python.exe -m pytest tests/test_pdf_magnifier_integration.py tests/test_pdf_loupe_pipeline.py -q
# Fresh processes, repeated with 1.25, 1.5 and 2:
$env:QT_SCALE_FACTOR='1.25'
.venv311/Scripts/python.exe -m pytest tests/test_pdf_magnifier_integration.py tests/test_pdf_loupe_pipeline.py -q
.venv311/Scripts/python.exe -m pytest tests/test_pdf_support.py -q
.venv311/Scripts/python.exe -m pytest tests/test_viewer_resampling_magnifier.py tests/test_spread_loupe_slider_trail.py -q
.venv311/Scripts/python.exe -m pytest tests/test_sprint14_followup.py -q -k 'cache or adjustment'
.venv311/Scripts/python.exe -m pytest tests/test_viewer_prepared_display.py -q
```

The focused PDF cases cover actual striped pixels before and after promotion,
single/spread, crossing the gutter, LTR/RTL, 0/90/180/270 rotations, QPixmap-only
entry, pointer movement while PDF work is blocked, exact-cache reopen,
cancel/page/async-book switch races, policy/zoom/DPR changes, split wide pages,
adjustments, failure fallback, purpose-specific cancellation and bounded caching.
Entry tests reject calls to QPixmap scaling/conversion and fallback render queues.

Final combined PDF correctness/pipeline suite: **54 passed at each of
100/125/150/200% DPI in fresh processes**. The comparable measurement suite
completed **4 cases**. The added DPR-growth admission test intercepts final work
instead of allocating giant images, while normal-size rendered tests verify the
actual before/after pixels at every listed DPI.

Existing results: **73 PDF tests**, **255 loupe/spread tests**, and **30 focused
cache/adjustment tests** passed. The maintained prepared-display suite produced
**62 passed, 1 failed**: the previously documented slider expectation mismatch
(`test_window_cold_display_demand_coalesces_to_latest_after_input_idle`, expected
0 vs requested position 3). It remains untouched, and is not counted as passing.
AST parsing of 11 changed Python files and `git diff --check` passed.

```powershell
.venv311/Scripts/python.exe scripts/benchmark_viewer_navigation.py --pages 9 --width 2400 --height 3600 --viewport-width 1280 --viewport-height 900 --cache-mib 256 --output "$env:TEMP/NivisViewer-pdf-loupe-entry-navigation-20260912.json"
```

The required same-size synthetic ZIP smoke completed sequential, reverse,
direction reversal, ping-pong, immediate burst and rapid-final navigation:
**0 terminal errors**, final rapid target page 8. Flat synthetic JPEGs were used.

## Actual files and limits

Production: `app/viewer_widget.py`, `app/viewer_window.py`,
`app/pdf_image_source.py`, new `app/pdf_loupe.py`; shared adjustment extraction:
`app/image_cache.py`, new `app/image_adjustments.py`.
Tests: `tests/test_pdf_magnifier_integration.py`,
`tests/test_viewer_resampling_magnifier.py`, `tests/test_spread_loupe_slider_trail.py`,
new `tests/test_pdf_loupe_pipeline.py`, new `tests/test_pdf_loupe_performance.py`.
Documentation: this report and the comparison provenance note.

Old assertions requiring a waiting/inactive lens or promoted QImages in the
normal display were updated to the newly authorized immediate/independent
behavior. Real pixel/geometry, final output, stale-result and normal-cache
assertions replace those implementation assumptions.

No Qt/PDFium abort occurred. An initial combined run with failed old assertions
finished pytest but left its Python process running; only the identified test
processes were stopped. The passing fresh-process groups terminated normally.
That initial failure/cleanup observation is not reported as a passing run.

Offscreen paint timings are not Windows compositor/native-input measurements.
No private images/PDFs, real app, native input, external GUI, new dependencies,
environment repair, build, publication, commit or push. No native checks were
requested; consultation review is next.
