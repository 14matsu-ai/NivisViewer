# Saved thumbnail compression quality (2026-09-10)

Transparency-policy update: the setting location/range remain, but the historical
whole-image lossless exception below has been replaced by default matte flattening
plus lossy RGB, with optional exact alpha preservation. See
[the current saved-alpha policy](THUMBNAIL_SAVED_ALPHA_POLICY.md).

## Settings and scope

Main Settings -> Browser -> サムネイル, immediately after 生成最大辺:

- A compact circled `?` follows `生成最大辺:`. Its multi-line hover text
  summarizes the physical-resolution and bucket behavior, and clicking it opens
  the full explanation.
- Label: `保存サムネイルの圧縮品質`.
- Integer spin control: 1 through 100, default **60**.
- A compact circled `?` follows the label. Its multi-line hover text summarizes
  the quality/size tradeoff, and clicking it opens the full explanation.
- The detailed help states that the integer range is 1 through 100, the default
  is **60**, higher values improve quality while increasing size, WebP fallback
  is lossless PNG, and the setting applies to new thumbnails while existing
  cache entries update gradually.

The existing ConfigManager owns `thumbnail_webp_quality`. Missing, malformed,
boolean, fractional and out-of-range values normalize to 60. Apply persists and
updates the Browser; Cancel discards unapplied changes. Reopening Settings loads
the saved value. There is no second configuration path.

This setting controls opaque WebP encoding, not generated resolution. Existing
生成品質 (Auto's sqrt(2) margin), logical size, frame ratio, DPR, buckets, maximum
edge, crop and upscaling behavior are untouched. Transparent thumbnails retain
lossless WebP with effort 90; PNG fallback remains lossless. No dependencies added.

## Request identity and live changes

Each immutable ThumbnailRenderSpec owns its compression quality. Memory family
and cache tokens, disk format identity and actual opaque encoder quality all use
that same value. Example default identity: `3-webp-q60-alpha-lossless-e90`.
SQLite schema remains version 3: opening does not drop the index.

Applying a different value updates the policy scalar without taking the disk
cache lock or running SQL/encoding/maintenance on the GUI thread. Browser rebuilds
its render spec, advances the existing provider generation and schedules normal
thumbnail requests. Already painted thumbnails and layout remain in place. There
is no directory refresh, bulk pre-generation or blanking of the current model.
Unchanged quality and unrelated settings do not advance this generation.

Queued obsolete work is cancelled through the existing provider. An encode already
in flight may finish with its OLD spec, bytes and format identity; it cannot label
old bytes as new quality. Existing generation checks reject stale publication.
New requests cannot treat old-quality variants as compatible. The compatibility
integer-size disk API also snapshots its quality before encoding. No new pool or
cache authority was introduced.

Old formats retire lazily through existing prune maintenance, at most 128
obsolete-format rows per invocation, oldest first. Existing expiry/orphan/LRU
rules can remove additional entries independently. Pruning occurs on existing
save/daily/forced maintenance paths; applying quality does not wipe the cache.
Valid source page counts may be reused across formats by kind/path/size/mtime
and carried into new writes. A selected metadata request reads a cached count
before initial maintenance. Counts in obsolete rows that are retired before
reuse are not retained in a separate metadata authority.

## Quality 70 versus 60 trial

Deterministic synthetic color-cover, Japanese-text, line-art and alpha fixtures;
same 149 logical px / portrait 1:sqrt(2) / Auto / maximum edge 512 policy.
Rendered pre-encoding pixels were equal between qualities. Physical frames:
100% = 181 x 256; 125% and 150% = 226 x 320; 200% = 362 x 512.
The equal middle frames result from existing bucket quantization, not this change.

| Fixture | DPI | Bytes q70 -> q60 |
| --- | --- | --- |
| Color cover | 100 / 125 / 150 / 200% | 6684 -> 5794 / 10680 -> 9032 / 10680 -> 9032 / 33412 -> 27772 |
| Text | 100 / 125 / 150 / 200% | 10016 -> 9372 / 15240 -> 14346 / 15240 -> 14346 / 30994 -> 29272 |
| Line art | 100 / 125 / 150 / 200% | 8450 -> 7960 / 13134 -> 12368 / 13134 -> 12368 / 30266 -> 28574 |
| Transparent | 100 / 125 / 150 / 200% | 19644 / 27656 / 27656 / 51500, unchanged |

Opaque savings were approximately 5.6-16.9% relative to q70 for these fixtures.
Seven-run median cover encode times q70 -> q60 were 5.911 -> 5.634 ms at 100%
and 25.842 -> 23.980 ms at 200%. Text/line encode times were mostly similar.
Read timings were noisy, not a consistent improvement: text 100% was
3.850 -> 10.472 ms; cover 200% was 7.150 -> 6.882 ms. These are warm-filesystem
synthetic measurements, not a promise of real Browser latency improvement.
Alpha bytes/pixels remained identical. Offscreen decoded samples were inspected;
tiny text and dense converging lines remain limited by the thumbnail resolution.
The configurable control lets the user judge their own covers at their real DPI.

Generated measurements and decoded PNGs are in
`build/webp-q60-audit-20260910/`; the maintained script is
`scripts/benchmark_thumbnail_webp_quality.py` (baseline quality defaults to 70).
The historical q90 -> q70 trial remains in `THUMBNAIL_CACHE_WEBP_Q70.md`.

A 512 MiB configured cache cap can still lead to roughly the same steady-state
disk usage: smaller files permit more entries. Existing LRU targets 90% of the cap
when exceeded. The repository profile has a 512 MiB cap; this is not a claim that
the user's active portable profile or live cache was inspected. No user cache
was deleted or opened for this work.

## Verification and files

239 focused tests passed across compression settings, disk cache, provider,
thumbnail generation quality, medium preset, Browser status/chrome, Settings and
ConfigManager. New tests exercise real offscreen Settings Apply/Cancel/reopen,
normalization, exact encoder bytes at 1/37/60/100, alpha and PNG fallback, restart
hits, format separation, bounded lazy retirement, valid page-count reuse, and an
event-gated in-flight encode while the GUI applies a different value. The old
generation cannot publish. Resolution/layout/rescan guards remain in the tests.

The 22 compression-setting cases also passed in fresh offscreen processes at
125%, 150% and 200% scaling (66 additional passes; the main run covers 100%).
The control's center is inside the Settings scroll viewport after scrolling to
it. Syntax parsing passed for all nine changed Python files; diff whitespace
validation passed.

Required shared Viewer smoke also completed with nine seeded high-detail
2400 x 3600 JPEGs in a temporary ZIP, 1280 x 800 viewport and 256 MiB budget:
forward 0.970 ms, reverse 0.938 ms, ping-pong median 0.909 ms, rapid-final paint
8.856 ms; zero terminal errors and one obsolete result rejected. Direction
reversal was also exercised. These offscreen observations are not a claim of
native compositor timing or a Viewer performance change.

Implementation: `app/config_manager.py`, `app/settings_dialog.py`,
`app/browser_window.py`, `app/thumbnail_render.py`, `app/thumbnail_provider.py`,
`app/thumbnail_disk_cache.py`.
Verification: `tests/test_thumbnail_compression_setting.py`,
`tests/test_thumbnail_disk_cache.py`, `scripts/benchmark_thumbnail_webp_quality.py`.
Documentation: this report, the historical trial notice and section 48 of
`ZIPPLAFORK_COMPARISON.md`.

All work uses offscreen Qt/temp fixtures. Existing unrelated modified/untracked
work is preserved. No real application/native input/external GUI, user config
write, portable rebuild, commit or push was performed.
