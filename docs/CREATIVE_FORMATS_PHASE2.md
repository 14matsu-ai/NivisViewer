# Creative project formats — Phase 2 (revised)

Base repository snapshot for this patch:

- repository: `14matsu-ai/NivisViewer`
- base commit: `e9eed08261b0d808e06ec7c6cb23791b22260112`
- base subject: `Add read-only PSD and PSB viewing with merged previews`

This revised patch supersedes the earlier Phase 2 patch that rejected XCF v14+.

## Scope

| Format | Browser | Viewer | Phase 2 fidelity |
| --- | --- | --- | --- |
| Krita `.kra` | `preview.png`, fallback merged | `mergedimage.png` | saved full composite |
| OpenRaster `.ora` | `Thumbnails/thumbnail.png`, fallback merged | `mergedimage.png` | saved full composite |
| CLIP STUDIO `.clip` | `CanvasPreview.ImageData` | same CanvasPreview | preview-only |
| GIMP `.xcf` v0–26 | feature-gated direct raster decode, otherwise installed GIMP 3.x | same | see direct-reader limitations below |

No project file is modified.

## KRA and ORA

Both are bounded ZIP-container reads. NivisViewer opens only the saved
preview/composite member and never expands the project layer tree.

## CLIP

CLIP STUDIO `.clip` remains a preview-only reader in Phase 2. The Python
implementation reads only the validated `CSFCHUNK` / `CHNKSQLi` /
`CanvasPreview` path and does not decode `CHNKExta` raster bodies.

The layout was checked against `Aodaruma/clipfile-rs` at revision
`bd88467fa80e63ad48c6c713fbfb5a8a116d798a` (MIT). NivisViewer does not bundle
or link that Rust crate.

## XCF: direct raster decoding and authoritative fallback

XCF is a living GIMP project format. The official GIMP XCF version history
shows:

- v4–13: GIMP 2.10-era changes;
- v14–23: GIMP 3.0-era changes;
- v24–25: GIMP 3.2.0 additions;
- v26: GIMP 3.2.6 channel live-filter persistence.

Modern versions can carry semantics that a legacy third-party compositor may
silently ignore, including layer effects, new blend/composite-space behavior,
vector/link layers, transforms and live filters.

Therefore:

1. `app/xcf_raster_reader.py` reads RGB8 nonlinear raster tiles directly, including
   alpha, 32/64-bit pointers, raw/RLE/zlib tiles, layer offsets and disabled masks.
   XCF v0–3 and v7–26 are considered; development versions v4–6 fall back.
   Ordinary single layers need no compositor or duplicate full-sized canvas.
2. Multiple normal/union layers support legacy nonlinear composition or modern
   explicitly profile-nonlinear composition. Linear/Lab/perceptual multi-layer
   composition, layer opacity, active masks, groups, effects, text/vector/link
   layers, higher precision, indexed/grayscale pixels and unknown rendering
   properties still require GIMP. No approximate substitute is returned.
   Image ICC data is preserved; unknown image parasites fall back.
3. The runtime no longer loads gimpformats/NumPy to try a second legacy decode
   before GIMP. The old dependency remains installed for existing review fixtures.
4. If GIMP 3.x is unavailable, a file requiring it returns a read error
   instead of claiming a potentially incorrect render.

The GIMP backend launches a **new**, **no-interface** process and exports the
opened XCF to a temporary PNG using GIMP 3's public `Gimp.file_save()` API in
`python-fu-eval` batch mode. It never saves back to the source XCF.

NivisViewer prefers `gimp-console` where available. It searches:

- `NIVISVIEWER_GIMP_EXE` explicit override;
- Settings → Archives → GIMP executable (takes priority over the environment);
- PATH (`gimp-console-3.2`, `gimp-console-3.0`, `gimp-3.2`, `gimp-3.0`, etc.);
- Windows `%ProgramFiles%\GIMP 3\bin`.
- Windows `%LOCALAPPDATA%\Programs\GIMP 3\bin`.

The GIMP settings field is persisted as `gimp_executable` and applied without
restarting. An empty field restores automatic detection (including the environment
override). A nonempty but unavailable path does not fall back to a different GIMP.
The check button checks availability/version on a worker; editing the field rejects
any pending result. CLIP uses the embedded preview reader and needs no external tool.

The process is invoked with an argument vector and `shell=False`; user paths are
not interpolated into a shell command. One process-level lock prevents multiple
XCF thumbnail/viewer workers from launching competing GIMP instances
simultaneously.

NivisViewer does not download, install or redistribute GIMP.

## XCF Browser scheduling

XCF has no cheap generic saved composite. Speculative READ_AHEAD/PREFETCH/
BACKGROUND thumbnail generation remains disabled for XCF. Visible or selected
XCF items may render, and the existing thumbnail disk cache then reuses that
result.

One provider-owned XCF worker lane is separate from ordinary Browser thumbnails.
Direct XCF files use it immediately. If an archive/folder cover turns out to be
XCF, an internal handoff unwinds the ordinary worker and retries on the XCF lane
with the same cancellation token, generation, revision and signal identity.
The ordinary lane does not wait for the retry. Both lanes participate in provider
close/wait and cancellation. GIMP conversions themselves remain globally serial;
this change does not promise concurrent XCF rendering or eliminate every GIMP wait.

The direct parser is a new implementation of the documented format, not a
ZipPlaFork port. Retaining the current GIMP-only path preserves fidelity but has
startup cost; a ZipPlaFork scheduler replacement does not solve XCF decoding;
the chosen hybrid connects a new bounded reader to existing Viewer contracts and
a separate Browser lane. No new dependency or copied third-party implementation
was introduced. Format reference: https://developer.gimp.org/core/standards/xcf/.
XCF loading now defaults to OFF (`xcf_loading_enabled=false`). Settings → Archives
→ GIMP provides an explicit opt-in checkbox; changing it requires an app restart.
At startup ApplicationController configures the shared enabled-extension sets.
Browser scanning, Folder/ZIP/external-archive image enumeration and adjacent-book
search exclude XCF when disabled. Thumbnail requests reject XCF before work is
queued, and the decoder also rejects it before reading or launching GIMP.
The complete capability/association extension lists remain separate from this
runtime policy. PSD and CLIP are unaffected.

## Safety limits

The Phase 2 limits include:

- project ZIP member: up to 1 GiB;
- embedded image: up to 256 Mpixel;
- CLIP SQLite: up to 256 MiB;
- CLIP preview blob: up to 128 MiB;
- XCF canvas accepted by NivisViewer: up to 64 Mpixel;
- GIMP render process timeout: 120 seconds;
- GIMP temporary PNG: up to 1 GiB.

The GIMP subprocess runs on a worker path; the GUI thread does not wait on the
external process.

Viewer and Browser worker cancellation tokens are bound using a scoped
ContextVar, reset after each loader call. GIMP discovery and conversion monitor
their own child process with 50 ms cancellation checks; conversion-lock waits
are cancellable too. A cancelled request never starts a queued conversion.
Cancellation/timeout terminates only the owned child, waits for exit (kills it
after a 2 s termination timeout), then releases the temporary files and slot.
No unrelated GIMP instance or in-process native decoder is terminated.

## Provenance and licensing

- `gimpformats 2025`: LGPL-3.0-only runtime dependency for legacy XCF.
- GIMP 3.x: optional external application, GPL-3.0-or-later, not bundled.
- `clipfile-rs`: MIT; used as a fixed-revision format/provenance reference only.

Frozen release compliance remains subject to the repository's existing manual
license audit.
