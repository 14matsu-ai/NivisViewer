# ZIP open freeze investigation (2026-09-26)

## Update: captured hangs 19564 and 21656

Both user-provided dumps have the same worker pattern:

- A layout metadata worker is in `probe_image_size -> QImageReader.size ->
  _ZipEntrySequentialDevice.readData -> ZipExtFile.read`.
- Another raster worker is in `_read_jpeg_qbytearray_at_most -> reader.size`.
- ZIP read-ahead is waiting to enter the source `_lock`.
- The GUI stack is in a Qt event-loop call (or a fullscreen pointer geometry
  query), not retired cache destruction. The accompanying log shows first
  paint and post-paint cleanup completed, with cleanup around 0.1–0.4ms in
  the final session. These captures do not support disposal as their cause.

The leading explanation is native Qt image/plugin synchronization interacting
with Python callbacks/GIL reacquisition. Python stacks cannot identify the
native lock owner, so the precise lock cycle remains an inference. The confirmed
problematic boundary is concurrent Qt image probing through a Python QIODevice
whose readData performs ZIP I/O while holding the source lock.

`ZipImageSource.probe_image_size` now reads a bounded prefix in Python first,
then probes a native QBuffer **outside** the ZIP lock. Qt no longer calls Python
or performs ZIP reads from this header-probe path. Prefixes grow from 64KiB to
256KiB to 1MiB only when necessary. Unresolved headers return None; actual
decoding remains the geometry authority. EXIF axis handling, cancellation and
entry-size validation are preserved. The production compatible JPEG decoder
already uses a native buffer; its implementation is unchanged. The legacy
streamed experimental decoder is not used by the current raster route.

No worker-count/read-ahead policy was changed and no permanent test added.
Validation after this update: 114 existing tests passed; syntax/diff checks
passed. A disposable offscreen concurrent probe completed 96 header reads and
96 JPEG decodes with read-ahead enabled in 2.14s, with 99 GUI heartbeat callbacks.
The same 4096x6500, eight-page high-detail navigation benchmark completed
sequential, reverse, reversal, ping-pong and rapid-final scenarios. These checks
verify the replacement path, not proof that the user's intermittent hang can
never recur. The diagnostic capture remains enabled for real-use confirmation.

The sections below describe the earlier disposal hypothesis and its independent
mitigation, before these two hang stacks were available.

Baseline: `162bbfd` / 1.0.16, initially clean local main. The user's intermittent
freeze has **not** been reproduced or conclusively attributed. The preceding
09_ZIP経路診断 discussion suggested cache disposal, GUI completion, and warmup;
these are hypotheses, not captured hang stacks. Existing local application logs
contained startup information but no stack from the reported freeze.

## Confirmed synchronous work and correction

ZIP creation/listing runs in BookOpenWorker, pixel decoding in raster workers.
After the first frame paints, ViewerWindow._flush_presentation_side_effects calls
BookSession.release_retired_book_resources on the GUI thread. Previously this
called runtime.shutdown(wait_msecs=0), whose cancel(clear_artifacts=True) cleared
all QPixmap and QImage caches synchronously. The zero wait bounds worker waiting,
not memory destruction. Large caches could therefore stall the new book after
its first image had appeared.

An idle retired runtime now drains at most eight artifacts or about 4ms per
slice, then retries through a 10ms single-shot Qt timer owned by BookSession.
QPixmaps remain on the GUI thread. An individual destructor can exceed 4ms:
this is a cooperative budget, not a hard real-time guarantee. Worker completion
ownership, cancellation, generations and source close rules remain in place.
Application shutdown still drains synchronously. This correction addresses the
confirmed unbounded replacement cleanup; it does not establish the root cause
of the user's intermittent hang or address every possible close-time stall.

Options considered: the ZipPlaFork pipeline is already the architectural
reference, but a new port is unnecessary for this Qt resource-lifetime problem.
The existing NivisViewer one-shot disposal has no GUI time budget. A hybrid
decode/cache pipeline replacement is disproportionate to the evidence. The
selected new disposal approach retains the current pipeline and yields between
small groups of retired Qt resources. No new ZipPlaFork code was copied.

## Evidence on the next occurrence

Normal source and future frozen launches install a native CPython watchdog.
A 1-second Qt heartbeat rearms a 5-second one-shot all-thread stack dump. This
also works when a Python daemon cannot acquire the GIL. Dumps go to the active
profile's `data/logs/freeze-<pid>.log`; at most four earlier session files are
retained, and the watchdog stops rearming once its file reaches 2MiB.

`NivisViewer.log` records first open/prepared/frame completion/paint/post-paint/
PageList/retired cleanup boundaries per owner and operation, plus phases taking
50ms or longer. Repeated fast page turns do not log each boundary. Runtime
boundaries also record frame/source counts and cache bytes. No image content,
OCR, classification or archive entry contents are collected. Stack dumps show
code locations, not Python local values. An OS sleep or a native modal dialog
can also trigger a dump, so a dump is evidence to inspect, not proof of deadlock.

Use the modified source tree to collect these records. The already published
1.0.16 executable has not been rebuilt. Read-only profiles disable the watchdog.

## Validation

- Existing BookSession, ZIP runtime and Viewer integration suite: 71 passed.
- One disposable offscreen probe: 256MiB retired QImages; first slice 1.06ms,
  56 of 64 images remained; 50 heartbeat callbacks ran during disposal, and all
  retired owners were finalized. A deliberate 5.3s GUI pause produced a stack.
- Existing high-detail 4096x6500 JPEG ZIP benchmark, 8 generated pages,
  3840x2106 viewport, 512MiB cache: completed initial display, sequential,
  reverse, direction reversal, ping-pong and rapid-final navigation. Initial
  display 154.6ms; warm navigation paints about 5.7–6.5ms (offscreen evidence,
  not native Windows display latency).
- Benchmark default immediate target 5 times out because cold repeated wheel
  input is deliberately not queued; observed/accepted target was 1. Rerun used
  `--immediate-target 1`, otherwise identical generated image conditions. The
  discarded-wheel policy was not changed to satisfy an outdated expectation.
- No permanent tests added, no real application/native input, no private image
  viewing, no commit/push or release build performed for this change.
