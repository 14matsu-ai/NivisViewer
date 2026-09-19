# Saved thumbnail alpha policy (2026-09-10)

## Current behavior

Main Settings -> Browser -> サムネイル, beside 保存サムネイルの圧縮品質:

**保存サムネイルの透明度を保持**, default **OFF**.

- OFF: composite generated thumbnail transparency onto the real Browser
  thumbnail background, discard alpha, and encode RGB as **lossy WebP at the
  configured compression quality**.
- ON: retain the generated thumbnail's alpha bytes exactly with
  `alpha_quality=100`, while RGB still uses **lossy WebP at the configured
  quality**. No near-opaque threshold or alpha flattening is used in this mode.
- WebP unavailable: existing PNG fallback saves the corresponding opaque RGB
  or alpha-preserving RGBA pixels losslessly. Settings explicitly mentions this
  fallback; PNG is not represented as lossy compression.

The previous “any alpha below255 -> whole-image lossless at effort90” branch is
removed. Quality100 does not activate lossless WebP. Fully transparent hidden RGB
is not guaranteed bit-exact when using lossy RGB; alpha preservation is a separate
contract from RGB fidelity. No image-subject heuristic is involved.

Existing compression values, including the user's40, are not overwritten.
The compression default remains60, integer range1-100. The new
`thumbnail_preserve_alpha` key uses ConfigManager and strict boolean normalization
(missing/invalid -> false). Apply/Cancel/reopen use the existing Settings flow.
Actual user config was not edited during implementation.

The compact circled `?` after the checkbox has a concise multi-line hover
summary. Clicking it opens the detailed help:

> 保存サムネイルの透明度保持は既定ではオフです。
> オフ: Browserの背景色で透明部分を埋め、透明度を破棄してRGBを指定品質の非可逆WebPで保存します。
> オン: 透明度（アルファ）をそのまま保持し、RGBは指定品質で非可逆圧縮します。
> WebP非対応時はPNG（可逆圧縮）を使用します。

Compression helper now correctly removes “透明画像は対象外” and explains that
WebP-unavailable fallback is PNG. Existing lazy-update explanation remains.

## Matte authority and visual consistency

`BrowserItemDelegate.paint` fills the real thumbnail frame with
`option.palette.base()` before drawing the image. The saved matte therefore comes
from **the list view's normal Active/Base palette color** (normally white in the
light theme), captured on the GUI thread in the immutable render spec. It is
not a hard-coded black placeholder color, the folder/file fallback settings, or
the Viewer's independently configured background.

`BrowserWindow` observes the existing list view's `PaletteChange` events. When
flattening is enabled, a changed Base color changes the request/cache family and
format identity, advances the existing generation, and schedules ordinary
requested work. Already displayed pixels remain while replacement work finishes;
there is no immediate folder scan, bulk regeneration or cache wipe. With alpha
preservation ON, matte color does not enter the identity, so a palette change
only affects normal painting and does not regenerate thumbnails.

The matte represents the normal thumbnail background, not transient selection,
disabled or focus colors. A custom style with different Active/Inactive Base
colors cannot be represented simultaneously by one opaque file; alpha-preserve
mode retains background-independent compositing. This change does not modify the
delegate, selection overlay, placeholder backgrounds or associated icons.

Worker processing converts premultiplied QImage pixels to straight RGBA through
the existing `_qimage_to_pil`, uses `Image.alpha_composite` once, then converts to
RGB. It never simply discards alpha or composites premultiplied colors a second
time. Caller-owned QImages are not mutated. For persisted previews the provider
publishes that same matte-composited image on initial generation, and disk reads
return the opaque saved image; normal WebP RGB compression differences remain,
but there is no fresh-versus-reloaded alpha/matte mismatch. Ordinary RGB previews
avoid this extra provider-side conversion. Memory-only shell/fallback previews
and disabled disk-cache operation keep their existing display behavior.

## Request/cache coherence and migration

`ThumbnailEncodingPolicy` is an immutable value in the existing thumbnail-render
module, not another cache/settings authority. It groups quality, alpha mode and
matte for pixel preparation and encoder options. ThumbnailRenderSpec owns it via
its immutable fields. ConfigManager -> Browser -> provider -> disk cache uses
that same policy. Updating the current cache policy is a single assignment with
no cache lock, SQL, decoding or cleanup on the GUI thread.

Examples at quality40:

- Default white matte: `3-webp-q40-rgb-lossy-v1-matteffffff`.
- Matte #123456: `3-webp-q40-rgb-lossy-v1-matte123456`.
- Exact alpha: `3-webp-q40-rgb-lossy-v1-alpha100`.
- PNG fallback uses the same policy suffix with `3-png-q40-...`; the suffix
  describes the requested RGB policy, while actual format remains explicitly PNG.

Spec cache/family tokens also include the alpha policy. Previous
`3-webp-q40-alpha-lossless-e90` entries cannot satisfy the new requests, even at
the same quality/dimensions. SQLite schema remains3. Existing bounded obsolete
format retirement (128 per prune call), age/LRU maintenance and valid page-count
reuse remain; nothing directly opened, deleted or reencoded the user's cache.

An already running job retains its original policy/format even if the UI changes
quality, matte or alpha mode during encoding. Pending work and stale publication
use the existing generation fences. The compatibility integer write path also
captures one policy, including across provider compositing and cache writing.
No new pool, timer/debounce, source traversal or cache migration authority exists.

Logical size149, portrait1:sqrt(2), generation-quality Auto/economy, DPI/bucket
selection, maximum edge and crop/display geometry are unchanged.

## Synthetic size and pixel evidence

Seeded272x384 synthetic random RGB with one alpha254 pixel:

| Policy | Quality | Encoded bytes |
| --- | ---: | ---: |
| Previous whole-image lossless | slider40, overridden effort90 | 313,518 |
| New default, flattened white + lossy RGB | 40 | 51,520 |
| Optional exact alpha + lossy RGB | 40 | 51,576 |

The default reduced this deliberately high-entropy fixture by approximately83.6%.
This is not a reencoding or predicted exact size of either real user cache entry.
Default flattened sizes at qualities1/40/60/100 were
17,632 / 51,520 / 59,188 / 117,018 bytes. Quality100 can still exceed100KiB;
there is no arbitrary file-size cap or false promise about all images.

Tests cover every alpha value0-255, including partial and fully transparent
pixels; optional output alpha bytes match exactly. RGB is demonstrably lossy.
PNG tests separately prove exact single compositing for straight/premultiplied
input and identical fresh/reloaded compositing without JPEG/WebP tolerance hiding
an alpha error. No actual user image was displayed, interpreted, OCR'd or opened
from its original source; all new pixel/visual work used synthetic fixtures.

## Verification

- **255 passed**: focused alpha policy, Settings/config, cache/provider, preview
  routing, generation quality and medium preset tests.
- **48 additional passes**: all16 new alpha-policy tests in fresh offscreen
  processes at125/150/200% DPI (100% is included above).
- Broader offscreen suite: **330 passed, 2 failed**. Both failures are the
  pre-existing `test_folder_without_preview_uses_exact_black_thumbnail_rectangle`
  light/dark cases at `tests/test_browser_grid.py:271`, asserting that a previewless
  image does NOT use the shared placeholder canvas. The current delegate returns
  true for all absent images. Delegate SHA-256 matches the pre-task build snapshot;
  this behavior and these unrelated assertions were not changed or suppressed.
- The initial broader run also passed all34 `test_browser_window.py` tests before
  encountering the same grid failure.
- Final focused rerun again passed all255 tests. Syntax parsing passed for all11
  changed Python files; diff whitespace validation passed.
- Required offscreen Viewer smoke: nine synthetic2400x3600 JPEGs in a temporary
  ZIP, 1280x800 viewport, 256MiB; forward0.923ms, reverse0.940ms,
  direction reversal1.252ms, ping-pong median0.932ms, rapid-final9.040ms.
  Zero terminal errors, one stale result rejected. No native-performance claim.

## Changed files and safety

Production: `app/thumbnail_render.py`, `app/thumbnail_disk_cache.py`,
`app/thumbnail_provider.py`, `app/config_manager.py`, `app/settings_dialog.py`,
`app/browser_window.py`.

Tests: new `tests/test_thumbnail_alpha_policy.py`; updated
`tests/test_thumbnail_compression_setting.py`, `tests/test_thumbnail_disk_cache.py`
and `tests/test_thumbnail_compression_audit.py` for the authorized changed policy.
The maintained `scripts/benchmark_thumbnail_webp_quality.py` now uses production
encoding policy instead of reproducing the removed lossless exception.
Documentation: this report and historical notices in the preceding compression
setting and size-investigation reports.

No dependency, unrelated refactor, real application/native input/external GUI,
portable rebuild, user cache/config edit, commit or push. Existing dirty/untracked
work is preserved. This work did not update a portable package; the new policy
is source-only until a separately authorized rebuild.
