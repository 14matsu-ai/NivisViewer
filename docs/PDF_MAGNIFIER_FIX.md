# PDF loupe activation and resolution refresh — 2026-09-12

## Request and reproduction

User feedback: PDF loupe appears not to work. Investigation used only generated
four-page vector PDFs (alternating red/blue stripes), temporary profiles and
offscreen Qt. No private PDF/image, native input or real application launch.

Cold single/spread activation initially passed. Two integrated paths failed:

- A same-page refresh that applies a prepared display unconditionally called
  `cancel_magnifier`. Both single and spread lost the lens. Preparing the current
  unit and calling the real `ViewerWindow._refresh_view` reproduced this.
- A PDF with a retained prepared QPixmap but no attached QImage sent
  `magnifierSourceResolutionRequested`, the raster-only request. The Window
  deliberately ignores that signal for PDFs, so the lens stayed waiting.

The initial regression run produced five failures (four refresh combinations
and the QPixmap-only PDF case). This reproduces concrete defects, but does not
establish which cache state the user's PDF was in.

## Fix and boundaries

Production changes are limited to `app/viewer_widget.py` and
`app/viewer_window.py`:

- Prepared commits retain the lens only for the same complete slot identity and
  source identity. Different page/book commits still cancel it. Existing render
  generation, source generation and magnifier-key rejection remain in place.
- QPixmap-only PDFs request PDF resolution, with rendered dimensions (including
  rotation) used for source coordinates. Raster hydration remains separate.
- Single-page promotion signals now fire after the fallback artifact key/job is
  installed. A synchronous refresh or cancellation can no longer be overwritten
  by the caller resuming with an old key. Spread already followed this ordering.
- Single-page PDF resolution uses the whole-page artifact target once. Previously
  it divided by the selected fraction a second time, requesting unnecessarily
  large rasters. Existing PDF size buckets and resource caps remain authoritative.
- Removed the premature resume in `set_pages`, which inspected the old images
  before the new display was prepared. The existing post-commit callback resumes
  from the committed images. Prepared-source attachment now also resumes after
  attaching, because its commit signal runs before attachment.
- A sufficient cached PDF raster is attached even when the render bucket does
  not change. That case produces no new image-loaded event to wake the lens.

Normal fit geometry, rotation, resampling policy and presentation ownership are
unchanged. PDFium still renders on its serialized service worker; resampling
still uses the existing image worker. No dependencies or borrowed code added.
Existing Browser spacing changes and their untracked files were preserved.

## Verification

Existing Python 3.11.9 / PySide6 and Qt 6.11.2 were used. The sandbox could not
launch the installed Python executable; approved execution outside that boundary
used the existing `.venv311` without repairing or installing anything.

All commands run from the repository root. Tests inherit
`QT_QPA_PLATFORM=offscreen` from `tests/conftest.py`.

```powershell
.venv311/Scripts/python.exe -m pytest tests/test_pdf_magnifier_integration.py -q
$env:QT_SCALE_FACTOR='1.25' # repeat in fresh processes with 1, 1.5 and 2
.venv311/Scripts/python.exe -m pytest tests/test_pdf_magnifier_integration.py -q
.venv311/Scripts/python.exe -m pytest tests/test_pdf_support.py -q
.venv311/Scripts/python.exe -m pytest tests/test_viewer_resampling_magnifier.py tests/test_spread_loupe_slider_trail.py -q
.venv311/Scripts/python.exe -m pytest tests/test_viewer_prepared_display.py -q
.venv311/Scripts/python.exe -m pytest tests/test_pdf_magnifier_integration.py -q -k late
```

- New PDF integration suite: **27 passed per DPI** at 100/125/150/200%.
  Tests cover cold/prepared single/spread, LTR/RTL, 0/90/270 degrees, geometric
  stripe enlargement, final artifact source keys, bounded promotion sizes,
  same-page refresh, fit restoration and movement across the spread gutter.
  Worker thread IDs are checked. Synthetic PDF source-free and unchanged-bucket
  cases are covered, including synchronous cancellation during promotion.
- The final strengthening of the cancellation test uses real async book open
  while the previous PDF render is blocked: **6 passed** (single/spread ×
  cancellation/page/book). Old results do not revive the lens.
- Existing PDF suite: **73 passed**.
- Existing magnifier and spread-loupe/slider/trail suites: **255 passed**.
- Prepared-display suite: **62 passed, 1 failed**. The failing
  `test_window_cold_display_demand_coalesces_to_latest_after_input_idle` expects
  slider value 0 while current code projects requested value 3. Running that
  exact test with both production modules loaded from their pre-change HEAD
  blobs in memory reproduced the same failure. No checkout or file replacement
  was used. This pre-existing expectation mismatch was left outside this fix.
- The first 200% run exposed a test assertion's one-physical-pixel discrepancy
  between PDF point aspect and rounded logical dimensions (1707 vs 1708).
  Resolution assertions now allow two pixels while separately checking actual
  2× stripe enlargement and request bounds. The final 200% run passed.
- AST syntax parsing and `git diff --check` passed.

Required raster navigation smoke:

```powershell
.venv311/Scripts/python.exe scripts/benchmark_viewer_navigation.py --pages 9 --width 2400 --height 3600 --viewport-width 1280 --viewport-height 900 --cache-mib 256 --output "$env:TEMP/NivisViewer-pdf-loupe-navigation-20260912.json"
```

Completed on nine generated 2400×3600 JPEGs in a ZIP: sequential, reverse,
direction reversal, ping-pong, rapid final target and immediate-after-first-paint
cases; **0 terminal errors**. Fixture mode is flat synthetic color, not a claim
about photographic detail or native responsiveness.

## Limitations

No Qt/PDFium abort occurred in these runs. The one prepared-display assertion
failure above is reported separately from successful groups. No native visual
or performance verification has been performed; the user's real PDF was neither
opened nor inspected. No build, publication, commit or push.
