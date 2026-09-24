# Large raster timing review — 2026-09-24

## Scope and privacy

Measured the actual application checkout in Documents/Codex/NivisViewer.
The user's supplied private ZIP was read only for dimensions, format, archive
extraction, JPEG decoding and display-size resampling timings. No image was
shown, exported, saved, classified, described, OCRed or sent to a service.
Reports contain page indexes and numeric measurements, not member names or
pixels. Fixture-only tests use generated images. No native application/input
was started. Existing unrelated pending Viewer edits were preserved.

## Findings and implemented changes

The first five private ZIP pages were JPEG, ranging from 2612×3665 to about
4400×6200 pixels. Some use progressive JPEG encoding. On the slow pages,
native JPEG decoding alone takes roughly 100–130 ms even with decoder-scaled
output. This is distinct from the archive extraction and final resize costs.

* `ZipImageSource` now reads DEFLATE output in at most 128 KiB blocks while
  retaining a single preallocated final payload. The previous 1 MiB reads
  incurred more transient buffer/copy cost on this archive. Stored entries
  retain the 1 MiB reads. Cancellation checks, size limits, ZIP CRC checks and
  request ownership remain in the same loop. Smaller explicit limits still
  take precedence. Both the JPEG Qt buffer and generic Pillow stream benefit.
* Folder JPEG paths avoid `ImageOps.exif_transpose`'s unconditional image copy
  when the EXIF orientation requires no transform. Rotated/mirrored images
  still use the existing transformation and detached QImage conversion.
* `qimage_to_pillow` passes the Qt buffer directly to `Image.frombytes`, which
  creates owned Pillow pixels. It no longer first allocates a redundant
  full-image Python `bytes` object. Pixel formats, stride, alpha handling,
  resampling policy and source/frame ownership are preserved.

No dependency or image-quality setting changed. Normal Folder and ZIP
execution remains one worker. `BookSession.zip_worker_limit` is an explicit
benchmark/test override, defaulting to one like `folder_worker_limit`.

## Measurements and limits

Measurements use an offscreen Qt application and real production page jobs.
OS file cache may be warm; per-page tests have no decoded source/frame cache.
These are not native-window perceptual measurements or proof of beating
ZipPla on every file.

Alternating 1 MiB / 64–128 KiB extraction runs on the same bytes showed:

| Sample (zero-based) | Prior extraction | 128 KiB extraction |
| --- | ---: | ---: |
| 0 | 16.6–16.7 ms | 13.5 ms |
| 2 | 7.1 ms | 5.8 ms |
| 3 | 55.4–56.5 ms | 43.1 ms |
| 4 | 34.2–34.5 ms | 22.7 ms |

A complete cold page job for sample 3 at a 1200×800 viewport measured
166.7 ms before and 153.3 ms afterward. Other samples and repeat runs varied;
the extraction reduction is more stable than total latency. The progressive
JPEG decoder remains the dominant cost and is not made faster by this change.

Two concurrent page jobs were also tried using existing bounded scheduling
and memory reservations. Private-ZIP 12-page warmup improved from roughly
1.3–1.5 s to 0.8–1.0 s. However cold/direct-seek sequences could get slower.
The generated 3200×5000 ViewerWindow test measured ZIP reversal at about
105 ms with one worker and 187 ms with two, and Folder reversal at 173 vs
206 ms. Therefore two-worker execution is **not enabled in production**.
The benchmark overrides and additional ZIP/mixed-source lifetime tests make
this experiment reproducible without changing ordinary application behavior.

A Windows GDI+ decode/resize microprobe was slower than the existing reduced
Qt/Pillow paths on most sampled pages. This is only a backend comparison,
not an execution or benchmark of ZipPla's complete Viewer and resize code.

## Reproduction and validation

* `scripts/benchmark_large_raster_page.py`: synthetic JPEG/PNG/WebP cold job
  costs, or `--archive <private-path>` for numeric-only page timings. For ZIP
  JPEGs it separates archive reads from the rest of decoding. The archive-read
  field is specifically a JPEG instrumentation boundary, not a generic PNG/
  WebP extraction measurement.
* `scripts/benchmark_raster_overlap.py`: alternates one/two workers on the same
  ZIP; records initial frame, 12-page warmup, forward, reverse, roundtrip and
  rapid final-target completion. No source image files are written.
* `scripts/benchmark_raster_navigation_critical_path.py`: actual offscreen
  ViewerWindow navigation on generated 3200×5000 images for ZIP and Folder.
  Updated obsolete wrappers to forward production keyword arguments and to
  record wheel events consumed at an unready frontier, rather than demanding
  a new page commit for every consumed notch. `--workers` defaults to one.
* Regression coverage includes native JPEG dimensions/EXIF, common and padded
  QImage formats with detached pixel ownership, memory limits, stale jobs,
  request reversal, shutdown and Browser thumbnail retention. The optional
  two-slot tests additionally cover ZIP and shared Folder/ZIP global limits.

Final validation: 226 related tests passed together; the two additional
multi-block DEFLATE payload/CRC cases passed afterward (228 distinct cases).
Syntax checks and whitespace checks passed. All ten production-navigation
scenarios completed for each of ZIP and Folder at 3200×5000, and both
coordinators shut down with no remaining runtime jobs. The ZIP reversal
included one cancelled read/decode counted by the probe as a failed attempt;
all 24 pages subsequently decoded successfully, with no stale publication.

## Explicit architecture comparison

Reference: https://github.com/himamon/ZipPlaFork, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.
Read `source/ZipPla/ViewerForm.cs` (`bmwLoadEachPage_DoWork`, background thread
count, resize switch) and `source/ZipPla/ImageLoader.cs` (stream bitmap loading).
The local reference checkout is at `b59e43966ae14bc5207cce54e9075c6099a30272`;
these two files have no diff from the fixed revision, which was also read
directly using Git objects.

| Design | Assessment |
| --- | --- |
| ZipPlaFork | One page worker carries stream loading through resizing; selected resize implementations parallelize internally. GDI+/WinForms ownership cannot be transplanted wholesale into Qt. |
| NivisViewer (selected) | Retain the native reduced-JPEG path, one-worker current-first policy, generation fences and atomic commits. Remove measured Python/ZIP buffer overhead. |
| Hybrid | Existing two-slot raster scheduling improves warmup throughput but worsens tested cold reversals. Keep it opt-in for experiments. |
| New design | A different native decoder or separately budgeted encoded-read pipeline could address remaining latency, but requires measured benefit and a new cancellation/resource-lifetime validation. No such backend is introduced here. |

No new ZipPla source code or structure is copied in this change. Existing
structural-port attribution and Copyright © 2016–2017 Rio's Toolbox notices
remain in `docs/ZIPPLAFORK_COMPARISON.md` and `licenses/ZipPlaFork/`.
