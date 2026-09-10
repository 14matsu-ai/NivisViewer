# Oversized quality-40 thumbnails: technical investigation (2026-09-10)

Historical diagnosis of the prior any-alpha -> whole-image-lossless policy.
The subsequently authorized implementation is documented in
[the current saved-alpha policy](THUMBNAIL_SAVED_ALPHA_POLICY.md). No real cache
file was modified as part of either investigation or implementation.

## Outcome

The two supplied oversized cache entries are **VP8L lossless WebP**, not opaque
lossy WebP incorrectly encoded at a stale quality. Both carry the current q40
request/format identity. The configured quality successfully reaches the common
encoder, but `ThumbnailDiskCache.put` deliberately overrides it with **lossless
encoding, effort 90**, if even one pixel has alpha below 255. This is the current
intended-but-expensive policy, not a reproduced setting-propagation defect.

There is a real usability consequence: lowering 保存サムネイルの圧縮品質 cannot
shrink these almost-opaque entries under the present policy. Their cache identity
still says q40 because it identifies the request policy, not the actual codec
branch taken for that particular image.

## Real-cache evidence (metadata and numerical alpha only)

Read-only inspection of the exact supplied installation's
`data/thumbnail_cache/files`: **51 files**, 993,864 bytes total; **2 files** above
both 100,000 bytes and 100 KiB. No originals were opened. No cached image was
displayed, semantically analyzed, classified or subjected to OCR. Pillow decoding
was used only for dimensions and numerical alpha extrema/histograms.

| Cache basename | Bytes | Dimensions | Actual codec | Non-opaque pixels | Alpha range |
| --- | ---: | --- | --- | ---: | --- |
| `27818da291cbea38bfd6eaea5530c2d3a38fbde3cb827d5013faa774af861d55.webp` | 122,232 | 272 x 384 | VP8L | 157 / 104,448 (0.1503%) | 248-255 |
| `386bdc74de939ea35aa28c1142ebe97f39c71e03fde6257b04e15ff4952042ed.webp` | 155,052 | 272 x 384 | VP8L | 544 / 104,448 (0.5208%) | 253-255 |

Both have **zero fully transparent pixels**. The first has 89 pixels at alpha254;
the second has none at254. RIFF inspection found a single VP8L payload in each
(122,212 and 155,031 bytes, respectively); these are not PNG fallback or appended
EXIF/ICC blobs. All other 49 files are VP8 lossy and have alpha255 throughout.
All 51 existing-file index rows have `3-webp-q40-alpha-lossless-e90`; none of
these existing payloads has the old q70/q90 identity.

Both oversized entries' index kind is `archive`. Indexed size/dimensions match
the files, and both have request token `3601225498349578370` and family token
`3503588546304118159`. The index was opened via SQLite URI
`mode=ro&immutable=1` only after confirming no WAL existed. Its byte size, mtime
and SHA-256 remained unchanged, and no WAL/SHM was created. This is a point-in-time
inspection, not a live subscription to subsequent application activity.

The installation's `config.json` has compression40, logical149, portrait1:sqrt(2),
smart_crop, **economy**, maximum edge512, Browser center_crop. The user separately
confirmed quality40. This does not prove every historical file was created with
today's settings; the matching per-entry identities above are additional evidence.
The installed EXE SHA-256 equals the newly built EXE:
`ffc30a3dea3e5d30e62d03e22fb88a746ea4aae1334428bd46bcac23d02edfd3`.
That build's 120 embedded app modules and main entrypoint were previously verified
against the live repository. A stale installed executable is not indicated.

## Setting-to-byte trace

1. `SettingsDialog.values` emits `thumbnail_webp_quality`.
   `ConfigManager.apply` normalizes/persists and emits `settings_changed`.
2. `BrowserWindow.__init__` / `apply_settings` install the value into the provider
   disk policy and `_build_thumbnail_render_spec`. `_request_visible_thumbnails`
   passes that immutable spec, not a rendered label or default integer size.
3. `BrowserThumbnailProvider._load_pipeline` preserves the spec through source
   rendering and passes it directly to the **single** `disk_cache.put` call.
4. `ThumbnailDiskCache.put` reads `spec.encoder_quality`; fingerprint format,
   cache token and family token identify the same quality. Initially save options
   are `quality=40, method=4, exact=True`. `_qimage_to_pil` produces RGBA bytes.
5. If alpha minimum is255, Pillow writes ordinary lossy WebP using quality40.
   If minimum is below255, options become `lossless=True, quality=90` regardless
   of the slider. PNG is used only if the installed encoder lacks WebP support.

Same-session changes use existing generation fences, cancel pending obsolete
requests and preserve each running job's own spec/identity. Event-gated tests show
no old bytes labeled as new quality and no stale display publication. Restart
loads the saved quality; suitable/memory family matching excludes other qualities.
Old q70/q90 rows can remain pending bounded cleanup, but cannot satisfy a q40 spec.
Explicit legacy integer disk writes snapshot the active disk quality and were
verified at40 and70. Integer-only decoding helpers may create a default render
spec internally, but that value is not used as the integer disk-write quality;
the real Browser calls the full-spec path.

Profile authority is `resolve_app_paths`: explicit command-line profile, then
`NIVISVIEWER_PROFILE_DIR`, then executable-directory portable.flag/profile,
otherwise LOCALAPPDATA/NivisViewer. Source and portable configurations need not
be the same file. No private alternate profile was searched. The inspected
installation config is consistent with the supplied cache; live process arguments
and real monitor DPI were not inspected.

## Every preview route and alpha provenance limits

- Image, folder cover, ZIP and external archive cover use `_render_image` /
  `render_pil_thumbnail`, then the common encoder. External decoding was faked in
  regression tests; no external program ran.
- PDF goes through the same final renderer/encoder. PDFium requests an opaque
  white background. Existing PDF tests plus a fake-source q40 route test passed.
- Generated text fills an opaque paper-colored QImage before drawing text; the
  shared encoder receives q40. Antialiased text over that opaque fill stays opaque.
- FFmpeg video returns a generated preview through the same persistence path.
  Its extraction returns RGBA and VideoThumbnailPolicy renders it; the q40 route
  test uses a fake backend, with no real extraction/native process.
- Windows Shell previews are memory-only (`persist_to_disk=False`), including
  shell video placeholders. Failed/unreadable archives and previewless folders
  do not save their delegate fallback icons to the thumbnail disk cache.

For RGB and fully opaque RGBA inputs, synthetic tests of letterbox, center crop
and smart crop kept every alpha at255. Letterbox returns the fitted source image,
not a padded transparent canvas (800 x 600 -> 272 x 204 for a 272 x 384 frame).
There is no added card/background/delegate-icon image in these archive payloads.
Lanczos scaling can preserve/spread existing transparency, but the tests did not
reproduce artificial alpha from an all-opaque input. We cannot determine where
the supplied entries' slight transparency originated without source-level data;
original images were deliberately not opened. We do not claim it was intentional
source transparency, antialiasing, or a corrupt original.

`exact=True` preserves hidden RGB at alpha0 and can be expensive for other inputs.
It is **not the hidden-RGB explanation for these two entries**, since neither has
any alpha0 pixels. The whole-image lossless decision is sufficient to explain
their current storage behavior.

## Why 272 x 384 is possible

`quantize_thumbnail_required_edge` selects a bucket from required logical edge x
DPR x generation-quality margin. A384 long-edge portrait1:sqrt(2) bucket has
width round(384/sqrt(2)) =272. At logical149/economy, DPR2.25 or2.5 produces this
bucket and **exactly the observed request token**. More generally the384 bucket
corresponds to required edge greater than320 and at most384. Exact past DPI cannot
be recovered from the token, which stores the quantized result, not monitor DPI.
Current DPR or previous settings may differ; existing larger compatible variants
can be reused. No actual monitor scaling was inferred from file dimensions alone.

The earlier assumed logical149/Auto/max512 comparison gives181x256 at100%,
226x320 at125/150%, and362x512 at200%; at175% it can also give272x384, but its
Auto-mode token differs from these entries. Current inspected profile is economy,
not that earlier assumed Auto benchmark configuration.

## Synthetic reproduction and bounded proposal

Seeded random RGB at272x384, actual production `ThumbnailDiskCache.put`:

| Input | q1 bytes | q40 bytes | q60 bytes | q100 bytes |
| --- | ---: | ---: | ---: | ---: |
| Opaque RGB/RGBA | 17,632 | 51,520 | 59,188 | 117,018 |
| Same pixels, one alpha254 pixel | 313,518 | 313,518 | 313,518 | 313,518 |

Opaque q100 remains VP8 lossy (not automatic lossless), yet exceeds100KiB for this
high-entropy fixture. Pixel dimensions alone therefore cannot establish failure.
The one-alpha254 fixture is VP8L, byte-identical at all slider values, and decoded
pixels are exact. Fully hidden random RGB produced48 bytes with `exact=False`
versus313,422 with `exact=True` under lossless encoding; this is a separate
synthetic case, not a claim about the supplied entries.

**Proposal only; no production change:** encode RGB with configured lossy quality
while preserving alpha with `alpha_quality=100`, rather than making all RGB
lossless whenever any alpha is nonopaque. The one-alpha254 synthetic fixture at
q40 then used51,576 bytes (`VP8X` + `ALPH` + `VP8`) and retained exact alpha bytes.
This avoids flattening/thresholding alpha but changes the current lossless-RGB
contract for transparent artwork. It requires explicit policy approval. A proper
implementation would need cache-format versioning and alpha-range regression
coverage; it must not silently relabel old cache or trigger bulk regeneration.
No threshold that discards near-opaque alpha is proposed as an automatic fix.

## Verification and changes

**184 focused tests passed**, including the new synthetic investigation tests,
compression Settings/in-flight tests, disk cache/provider, external archive
thumbnails, PDF support and existing preview tests. Sources are real temp fixtures
for image/folder/ZIP/text and fakes for external/PDF/video/shell routing. No test
uses the user's image collection. Real cache files were never reencoded, modified
or deleted. No production code changed; no portable rebuild occurred.

Added only `tests/test_thumbnail_compression_audit.py` and this report.
No interactive application/native input/external GUI/dependency install,
commit/push or destructive Git operation was performed. All prior work remains.
