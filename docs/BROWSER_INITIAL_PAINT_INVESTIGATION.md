# Browser initial paint investigation (2026-09-21)

The initial investigation did not change production code; the later key-guard
change is recorded below. The user reports an initial
white window with neither the sidebar nor top controls visible. This differs
from an already-painted Browser waiting for its directory listing.

## Comparison and method

Compared tag `1.01` (`ac0fe7c`) with current `f7e73d3`. Both construct Browser,
start initial directory restoration from its constructor, then show the window.
Both publish initial rows after asynchronous directory preparation completes.
The scanner itself is unchanged between these revisions. The directory snapshot
cache is session-only and cannot accelerate the first visit after process start.

The probe exports the tagged `app/` into a temporary directory without changing
the checkout. Both versions use the same existing Python environment, synthetic
8x12 PNG files, isolated profiles, Japanese UI, and offscreen Qt. No personal
images, native application launch, or real profile are involved. Disk thumbnail
caching is disabled; this measures initial UI/list presentation, not warm-cache
thumbnail completion. Three fresh processes per case; results are medians.

Timing starts after QApplication creation, before ApplicationController
construction. It excludes Python/module imports. Paint timestamps are Qt event
delivery, not Windows compositor presentation. The probe does not reproduce the
reported roughly one-second native white window.

## Measurements

Top controls and sidebar first paint (the later of the two), in milliseconds:

| Synthetic files | 1.01 | Current |
| --- | ---: | ---: |
| 20 | 56.8 | 83.6 |
| 15,000 | 104.5 | 145.7 |

First paint containing rows was 66.3 vs 92.9 ms for 20 files, and 410.1 vs
449.6 ms for 15,000. This demonstrates a modest current-version difference in
this environment, not the cause or duration of the native symptom.

## Diagnostic prototype

A probe-only Browser subclass defers initial-folder restoration until after the
first top-level paint handler. It queues restoration for the next event-loop
turn. Production app files are untouched. The following are a separate paired
run with fresh profiles, three processes per case:

| Files | Current chrome paint | Deferred chrome paint | Current rows paint | Deferred rows paint |
| --- | ---: | ---: | ---: | ---: |
| 20 | 84.0 | 75.9 | 96.3 | 92.4 |
| 15,000 | 139.6 | 79.2 | 470.7 | 430.3 |

The result supports giving the first Browser paint priority over starting the
initial folder work. Worker/GUI contention is a possible contributor; individual
GIL, filesystem watcher, and native Shell costs were not isolated. This does not
establish that the same reduction occurs on the user's native desktop.

If implemented, use a one-shot, generation-guarded startup action after a real
first-paint notification. Respect explicit command-line/IPC navigation,
restore-disabled mode, user navigation, and shutdown so delayed restoration
cannot replace a newer location. A zero-delay timer alone does not prove paint
has happened. Keep existing tag-strip geometry and startup selection semantics.
Show the Browser chrome and its normal loading status while the list fills.

Diagnostic artifacts (ignored, not production files):

- `out/startup_paint_compare.py`
- `out/startup_paint_compare.json`
- `out/startup_paint_prototype.json`

All 24 diagnostic child processes returned successfully. The probe exits after
the initial painted rows and a shutdown request; it is not a shutdown/lifetime
regression test. No release build, commit, push, or product change was performed.

## First-thumbnail acceptance check (2026-09-21)

The follow-up probe `out/startup_thumbnail_compare.py` used the same deferred
restore subclass and the same synthetic folders, now with actual PNG thumbnail
generation. It compared three fresh, paired offscreen processes per variant and
case. Cold profiles had empty disk thumbnail caches. Warm profiles received
copies of a cache populated by a separate startup run. The clock started after
`QApplication` creation, before controller construction, and includes Browser
construction, scan, thumbnail loading/decoding, and viewport paints. It excludes
module imports and synthetic fixture/cache preparation, equally for both
variants. These are Qt paint-event measurements, not native compositor timings.

| Files | Cache | Current chrome | Deferred chrome | Current first real thumbnail | Deferred first real thumbnail | Current visible viewport complete | Deferred visible viewport complete |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 | Cold | 86.7 ms | 80.7 ms | 176.8 ms | 188.8 ms | 315.6 ms | 330.7 ms |
| 20 | Warm | 83.4 ms | 75.2 ms | 117.3 ms | 131.7 ms | 142.0 ms | 157.0 ms |
| 15,000 | Cold | 128.8 ms | 75.5 ms | 530.8 ms | 494.2 ms | 727.9 ms | 694.3 ms |
| 15,000 | Warm | 132.6 ms | 80.1 ms | 502.2 ms | 486.9 ms | 526.7 ms | 513.6 ms |

Each number is a median of three. Paired first-thumbnail differences
(deferred minus current) were +27.6, -27.8, +12.0 ms for 20 cold; -3.9,
+14.4, +16.4 ms for 20 warm; -41.0, -30.3, +3.3 ms for 15,000 cold; and
-42.6, -9.0, +3.4 ms for 15,000 warm. The small-folder medians therefore
regressed by 12.0 and 14.4 ms, despite earlier chrome paints. Under the
first-visible-thumbnail non-regression condition, deferred initial restoration
is rejected. No production startup code was changed. The probe's viewport
completion means the visible model range had image data when its paint event
was delivered; it does not verify Windows compositor presentation. The warm
15,000-file viewport was painted during partial row publication, so it is not
evidence of complete folder loading.

## Application event-filter key guard (2026-09-21)

Profiling the current startup path found 832 calls to
`BrowserWindow._is_browser_history_key_surface` before the first top-level
paint, about 8 ms cumulative under profiling. The application-wide Browser
event filter invoked that widget ancestry/focus/modal predicate before checking
whether an event was a key press. The cancel-command surface predicate had the
same ordering. This is a local cost observation, not a diagnosis of the native
white-window interval.

The narrow production change classifies `QKeyEvent` key presses once, then
checks both keyboard surfaces only for those events. It keeps the
application-wide filter, event handling order, directory restoration timing,
scan, thumbnail scheduler, and cache policy unchanged. An offscreen test
asserts show/layout events skip both keyboard surface predicates. The Browser
navigation and Escape regression files passed 80 tests, including Backspace
navigation versus property/name/search/address editors and Escape from toolbar
tag/rating controls without first clicking the list.

`out/startup_event_filter_compare.py` compared the HEAD version of `app/` and
the changed checkout in three paired fresh offscreen processes per case, with
identical synthetic folders and cold or copied warm disk caches. The clock
starts after `QApplication` creation and includes controller construction,
Browser construction, scan, thumbnail work, and paint delivery. Module imports
and fixture/cache preparation are excluded equally from the timed interval.

| Files | Cache | HEAD chrome | Changed chrome | HEAD first real thumbnail | Changed first real thumbnail | HEAD visible viewport complete | Changed visible viewport complete |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 | Cold | 83.4 ms | 76.6 ms | 172.2 ms | 158.8 ms | 305.5 ms | 305.2 ms |
| 20 | Warm | 82.6 ms | 77.4 ms | 132.9 ms | 112.2 ms | 156.5 ms | 136.4 ms |
| 15,000 | Cold | 128.6 ms | 137.4 ms | 525.4 ms | 523.6 ms | 715.4 ms | 723.0 ms |
| 15,000 | Warm | 126.5 ms | 138.4 ms | 484.0 ms | 477.2 ms | 511.7 ms | 503.4 ms |

Values are medians of three. Paired first-thumbnail differences (changed minus
HEAD) were +9.7, -17.6, -15.9 ms for 20 cold; -9.7, +0.3, -25.6 ms for 20
warm; -1.7, -20.6, +14.6 ms for 15,000 cold; and -8.2, +19.5, -20.1 ms
for 15,000 warm. There is no systematic first-thumbnail regression in these
runs, but chrome timing for large folders varied and had a worse median. The
change is retained because it removes measured unrelated-event work while
preserving the first-thumbnail acceptance condition in this probe. It does not
establish a fix for the user's roughly one-second native white screen. The
probe records Qt paint delivery rather than Windows compositor presentation;
warm large-folder viewport completion can occur during partial row publication.

## Critical review of the remaining white interval (2026-09-21)

The earlier conclusion that no further large Python cost was identified must
not be read as evidence that the white interval cannot be improved. The
profile's time inside QApplication.exec includes native work, waiting, and
possible contention; it does not attribute those components. The offscreen
platform also does not reproduce Windows exposure/composition, platform theme
and Shell behavior. Synthetic profiles omit the user's window geometry,
history and bookmarks. Paint-event entry is not completed screen presentation.

Browser builds its controls before show_initial(), which currently calls only
show(). Thus absent controls during the white interval do not by themselves
prove that control construction occurs after window exposure. Initial rendering
and exposure are separate review targets. No production code sets a deliberate
startup white screen or a startup updates-disabled interval in Browser.

Diagnostic out/startup_repaint_review.py compared the current checkout against
show()+repaint() and show()+zero-timer repaint(), without deferring the scan.
Three fresh processes per case, synthetic images, isolated cold/warm caches;
all 36 comparison runs succeeded. Values below are median sidebar-paint /
first-thumbnail-paint milliseconds:

| Files/cache | Normal | Immediate repaint | Zero-timer repaint |
| --- | ---: | ---: | ---: |
| 20/cold | 75.94 / 158.00 | 81.43 / 165.83 | 74.91 / 159.49 |
| 20/warm | 76.71 / 105.14 | 77.49 / 108.80 | 75.97 / 124.19 |
| 15,000/cold | 135.54 / 512.54 | 141.20 / 532.33 | 130.85 / 508.35 |
| 15,000/warm | 135.49 / 474.48 | 126.06 / 480.45 | 127.00 / 476.24 |

Neither repaint variant establishes a reliable improvement; neither is adopted.
These short, fixed-order runs are diagnostic, not a precise effect estimate.

Remaining hypotheses, not confirmed fixes:

- Make initial native exposure coincide with a prepared first UI frame while
  leaving directory work running. This changes presentation, not loading speed.
  Hidden/pre-rendered images and opacity gating require Windows validation:
  painting a pixmap does not prove the window backing store was presented;
  opacity changes can themselves cause painting and platform side effects.
  Do not gate on thumbnail completion or hide the app indefinitely.
- Separate the initial QFileSystemModel root/drive and platform icon work from
  the independently scheduled Browser listing. _build_ui calls setRootPath("")
  and leaves custom directory icons enabled. Qt documents possible high cost
  of custom icons on network/removable drives. This is an environment-dependent
  candidate, not evidence of a synchronous GUI stall in this app. Check actual
  ordering before deferring tree synchronization: some synchronization paths
  already occur after list paint.
- Correlate actual Windows show/expose, first paint completion, sidebar paint
  completion and first real-thumbnail paint. Do not launch the native app under
  current restrictions; any native confirmation must be a user-run check.

References: https://doc.qt.io/qt-6/qwidget.html#repaint ,
https://doc.qt.io/qt-6/qwidget.html#windowOpacity-prop ,
https://doc.qt.io/qt-6/qfilesystemmodel.html#Option-enum .
