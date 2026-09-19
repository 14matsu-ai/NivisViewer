# AVIF and JPEG XL (TODO 6)

## Optional codec startup correction (2026-09-19)

The usual `py -3.11` resolves to the user's Python311 installation (Pillow
12.1.1), where `pillow_jxl` is absent. The existing `.venv311` has Pillow 12.3.0
and the approved JXL plugin. An unconditional plugin import previously prevented
the application controller from importing in the first environment.

Plugin registration now tolerates ImportError (including ModuleNotFoundError)
and OSError (native loader failure). Diagnostics report JPEG XL availability
and the original loader error. JXL remains a recognized extension, but opening
it through file/folder/ZIP/external archive image sources reports an explicit
`decoder_unavailable` error naming `pillow-jxl-plugin`. Other formats remain
usable. Browser thumbnail failures retain their existing fallback behavior.
Installed-plugin registration and its two decoder threads remain unchanged;
AVIF registration, dependency pins, licenses and packaging collection are unchanged.

Using JXL requires `pillow-jxl-plugin==1.3.8` in the Python environment selected
to run the application; the existing `.venv311` already contains it. This fix
does not install anything or modify either Python environment.

Validation: the actual `py -3.11` successfully imports the application controller
and standalone image-detail module without constructing QApplication. Fresh
Python subprocess tests simulate absent packages, ImportError and OSError;
PNG/JPEG/AVIF file/folder/ZIP decoding survives, and missing JXL yields the
explicit error for file/folder/ZIP and fake RAR/7z sources. Installed-plugin
tests cover JXL/AVIF thumbnails, Viewer pixels, worker-only decoding and corrupt
inputs. The optional-plugin, format and settings-help group passes 34 tests.
Syntax checks pass. The standard nine-page 2400x3600 high-detail offscreen
navigation evaluation completes all directions and rapid input; result:
`out/optional-jxl-navigation.json`. No real application or external GUI launched.

## Implemented formats

`.avif` and `.jxl` are registered in `app/supported_formats.py`. Browser discovery,
folder/archive listings, Viewer open filters and image file-registration
categories share this table. Existing Pillow decoding and asynchronous work
remain unchanged. Local Pillow 12.3.0 reports AVIF support. Only the approved
JXL wheel was installed with `--no-index --no-deps`; Pillow and packaging 26.3
were already present and were not upgraded. Synthetic RGB tests exercise image,
folder and ZIP thumbnails plus Viewer pixels, with uppercase and Japanese paths.
RAR/7z tests use real image payload decoding and fake extraction backends.
Corrupt AVIF/JXL, JPEG/PNG regression and worker-only JXL decode are checked.
Animation, HDR, alpha and release packaging have not been validated in this item.
The plugin currently advertises one frame only; this does not add animation playback.

There is no saved extension allowlist in the current ConfigManager or Browser
filter state. Open-dialog and supported-image categories derive from the shared
table; existing search/rating and unsupported-file visibility choices are retained.
No personal settings or Windows registry entries were modified.

## Integrated JPEG XL dependency

Pin: `pillow-jxl-plugin==1.3.8`. Inspected wheel METADATA declares exactly
`License-Expression: GPL-3.0-or-later`, Python >=3.9 and runtime requirements
`pillow` and `packaging` (no version constraints). Existing versions satisfy them.
Upstream: https://github.com/Isotr0py/pillow-jpegxl-plugin/tree/v1.3.8
Wheel: `pillow_jxl_plugin-1.3.8-cp311-cp311-win_amd64.whl`.
PyPI: https://pypi.org/project/pillow-jxl-plugin/1.3.8/
SHA256: `85649ffbb6a21b2830022ce723de8625fc4d33621ed09ec4687eaf57b5044f4c`.

Before integration the Pillow registry and Qt supportedImageFormats had no JXL
decoder; `pillow_jxl`, `djxl` and ImageMagick were absent. FFmpeg 8.1 is on PATH, but its
JXL decoder availability was not verified because external application launch
is prohibited. Existing FFmpeg preview integration is not a shared Viewer image
decoder. A subprocess conversion path would require additional process, output
and cancellation handling for every image, including extracted archive bytes.

`app/pillow_plugins.py` registers the plugin before worker decoding and limits
its internal decoder to two threads per job. It is imported by image sources
and standalone Browser image-detail probes. Existing file/BytesIO paths serve
Browser and Viewer consistently. `requirements.txt` and release pins include
the plugin and its existing runtime dependency packaging. Other pins are unchanged.

NivisViewer.spec explicitly collects the wheel's 11 DLLs from
`pillow_jxl_plugin.libs` with PyInstaller's delvewheel helper and retains hidden
imports for JXL and AVIF plugins. Collection was checked without building.
Wheel GPL text is retained in `licenses/pillow-jxl-plugin/LICENSE`; packaging's
Apache/BSD texts are retained in `licenses/packaging/`. The license collector
includes both distributions for future builds. The wheel also bundles libjxl,
Highway, Brotli, Little CMS and MinGW runtime DLLs; the wheel ships only its GPL
license file, so this is not a complete transitive binary-license audit.
This is a private application; no distribution or release build was performed.
