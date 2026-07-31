# ZipPlaFork source provenance

Upstream: https://github.com/himamon/ZipPlaFork
Fixed revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.
License: AGPL-3.0-or-later.
Copyright: Rio's Toolbox, 2016; source assembly copyright 2016-2017.
Preserved texts: `licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`.

The following mappings identify the source files, methods, processing structures
and NivisViewer counterparts documented for this revision. Structural translations
retain the upstream AGPL provenance. Qt/Python resource ownership, cancellation,
generation checks and display integration adapt those structures to NivisViewer.
See `THIRD_PARTY_NOTICES.md` for the retained notices and port boundaries.

| Fixed upstream source and process | NivisViewer destination | Port boundary |
| --- | --- | --- |
| `source/ZipPla/ViewerForm.cs:3177`, `bmwLoadEachPage_DoWork`: load/decode and create a display-sized result in one background page job | `app/image_source.py`, `open_qimage_at_most`; `app/image_cache.py`, `_ImageLoadTask.run` | Processing structure port. JPEG decoding uses `QImageReader.setScaledSize()` so full source pixels are not materialized first. No source expression copied. |
| `source/ZipPla/ViewerForm.cs:5558`, `SetBackgroundMode`; `source/ZipPla/GenerarClasses.cs:247`, `SetWorksOrder`: replace queued order around the new current page | `app/viewer_window.py`, `_queue_decode_demand` / `_apply_pending_decode_demand`; `app/image_cache.py`, `preload_around` | Processing structure port. A 16 ms request-ID/generation guarded input-idle boundary replaces crossed wheel targets before raster work is registered. No source expression copied. |
| `source/ZipPla/ViewerForm.cs:5451`, `SetNewResizedImage`; `:5471`, `ReduceUsingMemory`: publish completed resized artifacts and evict by a memory bound | `app/image_cache.py`, target-preview byte accounting and LRU; existing `app/viewer_widget.py` prepared-display cache | Algorithm/structure reference. Existing Qt cache contracts and generation checks are retained; no upstream eviction implementation copied. |
| `source/ZipPla/ViewerForm.cs:6438`, `showCurrentPage`; `:6990`, `pbView_PaintToCanvas`: page changes present a completed display-sized artifact | Existing `app/viewer_widget.py`, prepared-display commit/paint path | Processing structure reference. The Qt implementation predates this decoder-size change and remains independently implemented. |

