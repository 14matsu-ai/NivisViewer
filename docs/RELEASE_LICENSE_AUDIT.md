# Publication-preparation license review — 2026-09-10

Status: **not cleared for public release**. This is a scoped engineering
inventory/checklist, not a legal opinion or a statement that placing a license
file beside a binary fulfills every condition. No binary was rebuilt or
published. Existing `dist/`, portable backups, upstream source/reference trees,
and historical notices were not edited. AGENTS.md's personal-use/no-publication
working restrictions remain unchanged.

## Project-level inconsistency resolved

The old root MIT grant, README's MIT publication statement, and About's
`License: MIT` did not describe the current documented ZipPlaFork-derived
application. At the owner's direction the current project distribution license
is **AGPL-3.0-or-later**. Root `LICENSE` is the verbatim official GNU AGPLv3 text;
`PROJECT_LICENSE.md` contains GNU's recommended program notice with the explicit
"or later" choice and original `Copyright (c) 2026 14matsu-ai` attribution.
`app/version.py` supplies the identifier to About and generated Windows version
metadata. Both the spec's internal resources and the build script's top-level
copies now include the project notice. Existing built artifacts still contain
their old notices; they must not be represented as updated releases.

The former MIT notice survives in `licenses/NivisViewer-Historical-MIT.txt`.
Valid permissions previously granted for earlier versions are not revoked;
that historical notice does not offer the current combined application under
MIT. No upstream MIT/BSD/Apache/GPL/LGPL text was replaced or relicensed.

ZipPlaFork's fixed revision remains
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later, with Rio's
Toolbox copyrights preserved in `licenses/ZipPlaFork/About.txt` and
`THIRD_PARTY_NOTICES.md`. `docs/ZIPPLAFORK_COMPARISON.md` remains the detailed
file/class/method/structural-port map. No new implementation was ported here.

Primary authority: [GNU AGPLv3](https://www.gnu.org/licenses/agpl-3.0.html),
[official plain text](https://www.gnu.org/licenses/agpl-3.0.txt), particularly
sections 1, 4–6 and 13. Root license SHA-256:
`0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`.

## Installed, pinned, and observed packaged components

Read-only evidence: `.venv311` distribution METADATA/RECORD/license files,
`requirements-release.txt`, existing `dist/NivisViewer/licenses/manifest.json`,
and native filenames/file-version fields/hashes in that existing artifact.
No binary was executed. All **58 DLL/PYD files** inspected under its PySide6,
shiboken6, PIL, and pypdfium2_raw directories matched the installed equivalents
by SHA-256. This verifies those files, not the complete application or a full
software bill of materials. Pure-Python/bootloader provenance was not established
solely by those native hashes.

| Component | Release pin | Installed / artifact evidence | License evidence |
| --- | --- | --- | --- |
| PySide6, Essentials, Addons, shiboken6 | PySide6 6.11.1 | 6.11.2 distributions; Qt DLLs 6.11.2.0 | Wheel metadata: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; module distinctions below |
| Pillow | 12.1.1 | 12.3.0 | MIT-CMU; wheel LICENSE also contains native-component notices |
| natsort | 8.4.0 | 8.4.0 | MIT, original copyright retained |
| pypdfium2 | 5.12.1 | 5.13.0; raw PDFium version.json 153.0.7999.0, pdfium-binaries origin, empty flags | Apache-2.0 / BSD-3-Clause; PDFium BSD-style plus BUILD_LICENSES; docs/examples CC-BY-4.0 |
| PyInstaller | 6.20.0 | 6.22.2 installed and recorded by existing artifact manifest | GPLv2-or-later with bootloader exception; runtime hooks Apache-2.0 |
| CPython | Python 3.11 x64 build requirement | 3.11.9 interpreter / python311.dll and python3.dll | PSF and incorporated-software notices in installed LICENSE.txt |

Pins and environment were **not upgraded or repaired** during this task.
The regenerated `licenses/manifest.json` now reports these mismatches instead
of allowing the installed versions to be mistaken for the release pins.
Decide which exact tested versions to release, lock the complete transitive
environment and wheel/source hashes, and repeat the inventory for that build.
The current five-line release file is not a complete reproducibility lock.

### Qt and PySide: missing wheel texts are not a commercial-only requirement

All four 6.11.2 Qt-for-Python distributions contain only
`LicenseRef-Qt-Commercial.txt` in their declared license files, but their own
METADATA declares open-source alternatives. Qt also documents open-source
distribution routes. No commercial entitlement is assumed or purchased here.
[Qt licensing](https://doc.qt.io/qt-6/licensing.html) and
[Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html) are the
primary guidance; the exact module/source licenses remain authoritative.

`licenses/Qt/6.11.2/` adds the official LGPLv3, GPLv3 and GPLv2 texts from
[PySide's v6.11.2 LICENSES](https://github.com/pyside/pyside-setup/tree/v6.11.2/LICENSES),
plus a limited attribution notice and URL/hash index. The intended open-source
route is LGPLv3 for eligible PySide/shiboken/Qt libraries, **not** GPLv2-only
for an AGPL combination. The GPL alternatives are retained as published texts.
The GPLv3/AGPLv3 combination permission does not relicense Qt itself as AGPL.

Actual existing Qt DLL inventory:

- Qt6Core, Qt6Gui, Qt6Widgets, Qt6Network, Qt6OpenGL;
- Qt6Svg, Qt6Pdf;
- Qt6Qml, Qt6QmlMeta, Qt6QmlModels, Qt6QmlWorkerScript, Qt6Quick;
- **Qt6VirtualKeyboard**, plus `platforminputcontexts/qtvirtualkeyboardplugin.dll`.

Plugins include qwindows/qdirect2d/qminimal/qoffscreen, qmodernwindowsstyle,
qsvgicon, qtuiotouch, qnetworklistmanager, qcertonlybackend/qopensslbackend/
qschannelbackend, and imageformats qgif/qicns/qico/qjpeg/qpdf/qsvg/qtga/qtiff/
qwbmp/qwebp. Thus the artifact includes more than the imported Widgets API.

**Qt Virtual Keyboard is GPLv3-only on the open-source route**, with potentially
additional input-method notices. It cannot be treated as LGPL just because
PySide's aggregate metadata lists LGPL. Before release either fulfill its
GPL/source/attribution requirements for the actual build, or separately approve,
implement and test its exclusion if unnecessary. Nothing was removed here.
[Qt Virtual Keyboard licensing](https://doc.qt.io/qt-6/qtvirtualkeyboard-index.html#licenses-and-attributions).

Qt PDF has an LGPLv3 alternative, but `Qt6Pdf.dll` / `qpdf.dll` also bring a
**second PDFium/Chromium-derived dependency tree**, not necessarily the same
revision/configuration as pypdfium2_raw/pdfium.dll. Its notices cannot be inferred
from pypdfium2's BUILD_LICENSES. Audit it separately or separately approve/test
removing this unused plugin chain.
[Qt PDF licensing](https://doc.qt.io/qt-6/qtpdf-licensing.html).

Remaining Qt work: obtain exact source/build configuration for these binaries;
collect module-specific and incorporated-library copyrights, NOTICE/license
texts and any required attribution (including QtBase, QtDeclarative, QtSvg,
QtImageFormats, QtVirtualKeyboard and Qt PDF's source tree). Match official
SBOM/attribution records against shipped modules and plugins rather than copying
every Qt example/tool license or assuming unused code is absent from binaries.
The added three GNU texts and limited copyright examples do **not** close this
gap. [Qt third-party inventory](https://doc.qt.io/qt-6/licenses-used-in-qt.html).

For the LGPL route, verify prominent library/usage/license notices, source
availability duties, the actual replaceable shared-library mechanism or another
compliant relinking route, and that modified compatible libraries can be used.
Document/reproduce the DLL/PYD replacement or application rebuild in this exact
one-folder layout; do not assume merely seeing separate DLLs proves it. Retain
rights to modification and reverse engineering for debugging such changes, and
provide installation information where the license requires it. Shipping an
AGPL application source tree alone does not settle these duties.
[LGPLv3 terms, especially section 4](https://doc.qt.io/qt-6/lgpl.html).

### Other runtime and build notices

Pillow's existing 12.3.0 LICENSE is an aggregated wheel notice: it includes
brotli 1.2.0, freetype 2.14.3, harfbuzz 14.2.1, lcms2 2.19.1, libavif 1.4.2,
libjpeg-turbo 3.1.4.1, libpng 1.6.58, libwebp 1.6.0, openjpeg 2.5.4, tiff 4.7.1,
xz 5.8.3 and zlib-ng 2.3.3 sections. Preserve the complete file rather than
reducing it to MIT-CMU. Confirm that build feature inventory for the final wheel.
[Pillow license](https://pillow.readthedocs.io/en/stable/about.html#license).

pypdfium2's 19 collected documents include its three top-level license texts and
16 PDFium BUILD_LICENSES files. They remain unchanged and associated with the
installed version, not the older release pin. The build-specific dependency
terms matter. [pypdfium2 licensing](https://pypdfium2.readthedocs.io/en/stable/readme.html#licensing).

PyInstaller is a build tool with bootloader/loader and runtime hooks present in
the resulting executable. Its existing COPYING.txt retains its exception and
runtime-hook distinction; using it does not turn NivisViewer into a GPLv2-only
project. Establish exact bootloader/build provenance for a release and retain
any applicable additional hook notices.
[PyInstaller license](https://pyinstaller.org/en/stable/license.html).

The collector now retains CPython 3.11.9's installed LICENSE.txt verbatim.
Additional observed top-level native DLLs include OpenSSL libcrypto/libssl
3.0.13, sqlite3 3.45.1.0, libffi-8 and Microsoft VC runtime 14.38.33126.1;
MSVCP140/VC runtime DLLs also occur in package folders. Audit the exact native
notice sets and Microsoft redistribution entitlement/terms before release.
Do not label these collectively AGPL or assume the PSF notice covers them all.
[Python incorporated-software license guidance](https://docs.python.org/3.11/license.html).

### External programs

The build specification adds application resources and installed Qt/PDFium
dependencies; it does not collect WinRAR, 7-Zip or FFmpeg. No named WinRAR,
UnRAR, RAR, 7z/7zz, FFmpeg or FFprobe executable was found in the inspected
bundle. Production locators invoke user-installed tools, without downloading
or installing them. The portable verifier now also rejects FFmpeg/FFprobe,
alongside its existing archive-executable rejection. This is a filename guard,
not an exhaustive renamed-binary/content audit. A future decision to bundle
any external program requires its own exact-build licensing review.

## Practical publication gates / source delivery

1. Obtain separate approval for a release and choose its exact source revision,
   dependency lock, toolchain, artifact version and license route. Verify rights
   for all contributions/assets; preserve attribution and modification notices.
2. Prepare complete Corresponding Source for the covered work conveyed: actual
   NivisViewer sources, required assets, build/spec/scripts, interface definitions,
   patches/configuration and required library sources/build material. Do not
   substitute pyc/PYZ, wheels, or a generic link to an upstream default branch
   for preferred-form source. Establish applicable System Library/tool exclusions
   specifically; do not assume bundled Qt is excluded.
3. For an Internet binary download, prefer a matching, accessible source archive
   beside it under AGPL section 6(d), with an explicit version/hash mapping and
   clear link/instructions. Verify access from a recipient's perspective and
   retain the required availability. No actual source endpoint or binding written
   offer has been created here; choosing another section-6 route requires its
   exact conditions, not a generic "source on request" sentence.
4. Finish the Qt/library notice, source and replacement/relinking work above.
   Verify About/legal notices (copyright, warranty, redistribution and license
   access) in the final UI/package; add the actual source-delivery information.
   If future modified functionality supports remote network interaction, assess
   AGPL section 13's user source-access requirement; do not infer that this
   desktop-only preparation supplies a network source offer.
5. Reproduce the one-folder build from an isolated, approved source copy with a
   new output directory and audited lock. Follow `PORTABLE_BUILD.md`; retain exact
   commands/tool versions and source/build hashes. Do not reuse the current old
   portable folder as a compliant release simply by copying new LICENSE files.
6. Review an explicit source/package allowlist. Exclude development environments,
   caches, profiles/history/configuration, logs, build/portable backups and private
   fixtures. `portable-backups/` has tracked paths in this repository: `.gitignore`
   alone does not remove tracked material from a source archive or Git publication.
   Only names were inspected here. A secrets/privacy/Git-history and content-rights
   audit is a **separate, unperformed review**; do not publish the repository or
   use an unreviewed `git archive` as a shortcut. No history rewrite is authorized.
7. Review final binary and source manifests, licensing gaps, reproducibility and
   functional results; get owner/reviewer approval before any upload/tag/release.

## Verification performed

17 focused tests passed: project/About/version/package-notice
consistency, official GNU checksum, offline exact-version supplements, retained
notice collisions, mismatch warnings, refusing modified supplement overwrites,
corrupt-source checksum rejection, exact interpreter-notice copying, and fake
external-executable guards. Existing build-configuration/version tests were
included. Syntax and diff checks passed. All 27 manifest notice hashes matched;
eight explicit Qt-review/pin-mismatch warnings intentionally remain. The
repository-required nine-page generated 2400 × 3600 JPEG ZIP offscreen navigation
check completed forward/reverse/reversal/ping-pong/rapid-final with zero terminal
errors. License collection alone was run; no PyInstaller or portable packaging
process was run.

Changed source/documentation: root LICENSE, PROJECT_LICENSE.md, README.md,
THIRD_PARTY_NOTICES.md, .gitattributes, NivisViewer.spec; app/version.py and
app/diagnostics_dialog.py; scripts/collect_licenses.py, build_portable.ps1 and
verify_portable_build.py; docs/PORTABLE_BUILD.md, RELEASE_CHECKLIST.md and this
audit; licenses/README.md and manifest.json; new licenses/Qt/6.11.2/ texts/index/
notice, licenses/Python/LICENSE.txt and NivisViewer-Historical-MIT.txt;
tests/test_release_licensing.py. Existing dependency notices and ZipPlaFork
source/provenance/notice files were not changed.

No real app, native input, external GUI, dependency installation, publication,
commit/push, destructive Git action, private image inspection, or general
secret/history audit occurred.
