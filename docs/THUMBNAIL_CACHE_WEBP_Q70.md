# Thumbnail disk WebP quality 70 (2026-09-10)

Historical report of the fixed-quality-70 trial. The current policy is now
configurable (default 60), with request-owned encoding identity rather than
rejecting other configured qualities. See
[the current compression-setting report](THUMBNAIL_COMPRESSION_SETTING.md).
The measurements and policy descriptions below describe that earlier trial.

## Policy and scope

Opaque thumbnail files use WebP quality **70**, previously 90. Method 4 and
`exact=True` are unchanged. A thumbnail containing any non-opaque alpha remains
**lossless**, with quality/encoding effort **90**; that value controls compression
effort rather than pixel fidelity. Transparent entries therefore need not shrink.
The PNG fallback remains lossless and unchanged.

`thumbnail_render.THUMBNAIL_ENCODER_QUALITY` is shared by render-spec identity,
the disk format identity and the actual lossy encoder. Lossless effort has its
own constant. Disk writes reject a render spec claiming a different lossy
quality. The current WebP identity is `3-webp-q70-alpha-lossless-e90`; the SQLite
schema stays at 3, so opening the cache does not drop its index or metadata.

No logical size, frame ratio, DPR policy, Auto quality margin, bucket selection,
crop/display setting or maximum generated edge was changed. No settings file,
user image, existing user cache, Viewer pipeline or portable build was modified.

## Existing cache transition

- Both exact and suitable-thumbnail lookups require the new disk format. Render
  cache/family tokens also change with quality, so q90 cannot indefinitely satisfy
  q70 requests. Normal requested work regenerates thumbnails lazily; no source
  folder scan or bulk regeneration is added.
- The existing `ThumbnailDiskCache.prune` retires at most **128 obsolete-format
  entries per invocation**, oldest first, even when below the byte limit. Existing
  age expiry, orphan cleanup and LRU eviction still apply independently and may
  remove additional entries. This is not a global cache wipe.
- Pruning continues through existing maintenance: after the configured save
  interval (default 32), when daily cleanup is due, or existing forced maintenance.
  No new timer, worker pool or cache authority is introduced.
- Page counts can be read/updated across encoding formats only when source
  kind/path/size/mtime match. New writes carry forward a valid count if no fresh
  count was supplied. The selected-metadata pipeline reads that count before
  initial maintenance can retire its old payload.
- Unvisited obsolete rows eventually lose their counts when retired. If needed
  later, normal requested metadata loading repopulates them. There is no extra
  metadata database or proactive source enumeration to preserve those rows.

The inspected repository configuration has a **512 MiB** cache limit and 90-day
unused-entry retention. The cap defaults to 512 and is validated to 128–4096 MiB.
Existing LRU pruning, when above the cap, aims for 90% (460.8 MiB at that limit).
This can explain usage near 500 MB; it does not prove the user's active portable
profile uses that exact configuration. Indexed payload usage excludes database
overhead. A smaller encoding can mean more cached entries at the same cap, not
a guaranteed permanent reduction of an approximately 500 MB total. Transition is
gradual with use/maintenance; no existing user cache was deleted in this task.

## Focused measurement

`python -B -m scripts.benchmark_thumbnail_webp_quality --output <artifact-dir>`
uses deterministic synthetic detailed color-cover, Japanese text and line-art
images, plus a transparent text image. It compares q90/q70 on **identical pixels**
after the real thumbnail renderer. It asserts that changing only encoder quality
does not change those rendered pixels. Sources are generated in memory, encoded
files are in a temporary directory, and optional decoded PNG/JSON artifacts are
written only to the requested output directory.

Requested conditions: logical 149 px, 1:sqrt2 portrait, Auto, max edge 512;
letterbox for these matching-aspect synthetic fixtures. DPR 100/125/150/200%
produces 181x256 / 226x320 / 226x320 / 362x512 cache pixels. The inspected saved
source profile had different quality/max-edge values; it was not rewritten.

Seven-repeat medians include encoding to a temporary file and the production
Pillow-to-QImage disk read, with warm filesystem caches. These are codec/file-read
measurements, not end-to-end Browser latency or cold storage benchmarks.

At 200% DPR (362x512 pixels):

| Sample | Bytes q90 -> q70 | Encode ms q90 -> q70 | Read ms q90 -> q70 |
| --- | ---: | ---: | ---: |
| Detailed color cover | 65,364 -> 33,412 | 31.421 -> 26.015 | 8.297 -> 6.912 |
| Japanese text | 43,168 -> 30,994 | 20.037 -> 19.157 | 7.299 -> 6.914 |
| Line art | 43,362 -> 30,266 | 21.480 -> 20.522 | 7.118 -> 6.653 |
| Transparent, lossless | 51,500 -> 51,500 | 36.576 -> 35.558 | 6.280 -> 5.589 |

Opaque byte reductions across these fixtures/scales ranged approximately
28–56%; this is **not** a promise for a user's images/cache. One small-cover read
was slightly slower at q70 (4.876 -> 4.962 ms at 125%); timings are variable.
Lossless byte/pixel identity was verified; its timing differences are noise.
Visual inspection found more smoothing/block structure in q70 colored texture,
while the synthetic text/line patterns remained readable. Both formats remain
lossy for opaque images; no universal visual-equivalence claim is made.

Artifacts: `build/webp-q70-audit-20260910/measurements.json` and the corresponding
q90/q70 decoded PNGs. Runtime: Python 3.11.9, Pillow 12.3.0, Qt/PySide6 6.11.2.

## Verification

157 focused tests passed: disk cache, thumbnail provider, Sprint 15 thumbnail
quality/DPI policy, 149px preset, and Browser page-count/chrome tests. New coverage
checks actual encoded bytes against q70/alpha-lossless options, PNG fallback,
old/new cache identities, unchanged requested DPI buckets, startup preservation,
bounded retirement below the byte cap, metadata carry-forward/invalidation, and
selected-count reuse before maintenance without opening the source archive.

No new dependencies, real app launch, native input, external GUI, portable
rebuild, commit, push or destructive Git operation.
