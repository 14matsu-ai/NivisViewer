# ZipPla-compatible Viewer validation checklist

> **Historical checklist (inactive production path).** This checklist applies
> only to the retired `ZipPlaCompatibleRasterPath` A/B experiment. Current ZIP
> navigation uses the book-scoped `ZipRasterBookRuntime` for all Viewer
> features; see `ZIPPLAFORK_COMPARISON.md`, section 11, and
> `scripts/benchmark_zip_runtime_navigation.py`.

## Automated/offscreen A/B

Use `QT_QPA_PLATFORM=offscreen`, fake/mock services, and a temporary directory
only. Do not start the real application, send native input, or launch an
external application.

Run old production path A and `ZipPlaCompatibleRasterPath` B with the same
temporary ZIP, JPEG bytes, viewport, DPR, and settings. B eligibility is:

- ZIP source and JPEG entry
- single-page display
- fit-window or fit-no-upscale
- standard resampling
- rotation 0
- default brightness, contrast, and gamma
- magnifier inactive and disabled
- page list logically disabled and actually hidden

Exercise both cache hits and intentional cache misses:

1. completely cold first frame;
2. forward cold miss beyond the retained current/next/previous frontier;
3. reverse-direction cold miss while old-direction prefetch is active;
4. repeated two-page round trips, including forced cold misses;
5. rapid multi-page input followed by a cold final target;
6. same-page layout/DPR replacement and stale-paint rejection;
7. source/book replacement and shutdown during an active compatible entry
   read（sequential stream版とQByteArray版の双方）;
8. corrupt JPEG or decoder failure with one-way fallback and no retry loop;
9. transitions to page list, magnifier, rotation, image adjustment,
   non-standard resampling, and unsupported formats.

For each applicable case, record or assert:

- current starts before any prefetch;
- prefetch starts only after the accepted current artifact is display-ready;
- requested order is current, next, previous;
- at most one compatible worker is active;
- reversal leaves no queued job from the old direction;
- obsolete active work receives cancellation;
- source identity, source epoch, request ID, maximum size, and DPR stale results
  are rejected before `QPixmap` creation;
- one queued GUI callback and one `QPixmap.fromImage` per cold decoded page;
- an artifact hit performs neither operation again;
- no `ImageCache`, source cache, `ViewerRenderTask`, prepared-display cache, or
  render-cache access occurs on B's critical path;
- old complete content remains visible until one atomic replacement; there is
  no `clear()`, placeholder, or empty frame;
- slider, status, and model metadata correspond to the accepted frame;
- timestamps cover input, worker start, compatible decode end, GUI callback,
  QPixmap end, commit, and matching paint;
- metrics include `jobs_submitted`, `queued_callbacks`, `qpixmap_creations`,
  `cache_hits`, `cache_misses`, `cancel_requests`, `bytes_read`, and
  `read_calls`;
- artifact count/bytes, working-set peak, and shutdown cleanup are captured.

## Real-machine comparison

Use exactly the same large-image JPEG ZIP in NivisViewer and ZipPlaFork. Start
NivisViewer with single-page view, standard resampling, fit-window, rotation 0,
default adjustments, magnifier off, and page list hidden.

1. Open cold and compare responsiveness and time to the first painted frame.
2. Advance beyond retained pages and compare forward cold navigation.
3. Reverse immediately while forward prefetch runs; the new current must win,
   no old-direction page may appear, and input must remain responsive.
4. Alternate between two pages at least 20 times, then repeat after forcing
   both pages cold.
5. Send 8–20 rapid wheel/page commands and stop. Only the final requested page
   may commit; there must be no crossed-page flash.
6. In every cold case, the previous complete frame must remain visible until
   replacement: no black frame, blank frame, placeholder, or partial image.
7. Slider, status, page number, resolution, and metadata must never describe a
   different page from the committed image.
8. Enable the page list. Verify immediate safe fallback and normal thumbnail
   loading. Hide it and verify compatible eligibility resumes without duplicate
   decode.
9. Test magnifier, rotation, non-standard resampling, and non-default image
   adjustments separately. Each must fall back safely without a dead request or
   stale compatible frame.
10. Resize repeatedly and move between different-DPI displays. Old-layout
    results and old paints must not replace or release the new request.
11. Switch books or close during an active large ZIP read. Cancellation must be
    prompt, with no crash, archive lock, or unbounded shutdown.
12. Test corrupt and small JPEGs. Decoder failure must fall back only once; note
    that the at-most decoder does not itself upscale a small source.
13. Observe working set, peak memory, CPU use, UI-thread stalls, and retained
    compatible artifact count during forward, reversal, and round-trip runs.

An offscreen timing improvement alone is not completion. The same-archive
real-machine comparison must feel visibly closer to, or better than, ZipPlaFork.

## Current execution status

2026-07-31に統合後検証を実行した。実アプリ、native input、外部GUIは
起動せず、`QT_QPA_PLATFORM=offscreen`、fake/mock、固有`--basetemp`、
temp ZIPだけを使用した。

- compatible path、eligibility/fallback、archive lifetime/shutdown、
  serial/stale rejection、atomic commit/old-frame、page-list、magnifier、
  rotation、DPI、corrupt JPEG、book switchのfocused groupは合格。
- 関連Viewer groupは287件合格。
- 全82 test fileをQt/thread ownershipが近い12個の独立processへ分割し、
  重複なしで合計1,436件合格。広すぎる混成groupは2回、assert failureでは
  なくprocess終了待ちでtimeoutしたため、command lineで今回起動分だけを
  確認して終了した。二分後の各groupはすべて正常終了し、残留processはない。
- 高詳細A/Bは21 entry、4096 x 6500、682,441,432-byte ZIP、
  SHA-256
  `7bf798a4eee4a895c56d6e2a3415aa1a6ab47e4228d41d0118d24a39124e35f1`
  をA/Bで一致確認した。
- fresh-process request→paintは旧A / 新Bの順に、完全cold初回
  270.349 / 266.759 ms、順送りcold 278.333 / 255.481 ms、反転cold
  275.505 / 268.689 ms、往復cold中央値277.500 / 262.848 ms、
  rapid-final 287.891 / 270.425 ms。
- ready hitは順送り6.216 / 7.629 ms、逆方向5.107 / 6.939 ms、
  往復中央値5.394 / 6.312 ms。両経路ともworker/QImage/QPixmap生成は0。
  新Bのready往復では、直前paint後prefetchのcancel完了callbackが3回だけ
  current intervalへ重なったが、ZIP readとpayload materializationは0。
- memory-pressureのviewer working-set deltaは195.63 / 65.02 MiB、
  logical cacheは121.46 MiB・7 page / 30.36 MiB・3 page。
- rapid-finalのcommit前通過page decodeは0。paint後のpage 19 / 17
  neighbor prefetchは別phaseとして記録した。
- 旧stream backendはPython `QIODevice.readData` callbackが約1,985回/page
  発生して高詳細decodeを遅くしたため、production Bはreserved
  `QByteArray` → `QBuffer` → `QImageReader`へ置換した。

これらはoffscreenの補助証拠である。実機体感の合格判定は上記
Real-machine comparisonを同一実ZIPで実施するまで保留する。
