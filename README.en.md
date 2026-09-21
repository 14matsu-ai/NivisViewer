# NivisViewer

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

## Contents

- [Overview](#overview)
- [Windows x64 portable edition](#portable)
- [Supported inputs](#formats)
- [Requirements](#requirements)
- [Setup and launch](#setup)
- [Explorer drag-and-drop check](#drop)
- [License](#license)

<a id="overview"></a>
## Overview

NivisViewer is a comic and image viewer for Windows. It takes inspiration from ZipPla's controls and behavior while using its own implementation without reusing ZipPla source code.

It includes a folder tree synchronized with favorite folders, a BrowserWindow with a fixed-cell thumbnail grid, a separate ViewerWindow for books, settings, flush two-page spreads, a portable thumbnail cache, folder and single-image viewing, ZIP/CBZ, RAR/7z-family archive and PDF support, natural sorting, and asynchronous image loading. ViewerWindow also supports moving between books with the mouse Back/Forward buttons, configurable right-button drag gestures, and UI that appears at the screen edge in fullscreen mode.

The name NivisViewer comes from *nix, nivis*, Latin for “snow.”\
It expresses the idea of viewing a large collection of images as quietly and naturally as watching snow accumulate.

<a id="portable"></a>
## Windows x64 portable edition

Extract the release ZIP to any writable folder and run `NivisViewer.exe`. Python does not need to be installed separately. The standard distribution uses a PyInstaller one-folder layout. When `portable.flag` is next to the executable, `config.json` and `data/` (history database, thumbnail cache, and logs) are stored in the same portable folder. Neither the current working directory nor the image folder being viewed is used for application data.

The viewer can still read files from a read-only location, but settings, history, thumbnail cache, and logs cannot be saved there. Move the entire folder to a writable location. To uninstall, delete the NivisViewer folder. If you registered Windows file associations, remove them first in Settings → Windows integration.

A second launch, including repeated Open commands from Explorer, is safely forwarded to an existing process for the same user and portable folder. A copy in a different location is a separate instance. If the portable folder moves, register the Windows file associations again because the executable path changes.

Windows integration is enabled only when the user explicitly registers it in Settings. It adds “Open with” and default-app candidates, but does not force a change to Windows defaults or UserChoice. WinRAR and 7-Zip are not bundled. pypdfium2/PDFium is included in the portable edition.

The application is not code-signed, so Windows SmartScreen may warn about a downloaded release. For bug reports, copy diagnostic information from Viewer → Help. Logs are stored in `data\logs\NivisViewer.log` and are not sent anywhere automatically.

<a id="formats"></a>
## Supported inputs

- Folders
- Single images (supported images in the parent folder are opened as a book)
- ZIP / CBZ
- RAR / CBR (using WinRAR or 7-Zip installed by the user)
- 7z / CB7 (using a supported WinRAR CLI configuration or 7-Zip installed by the user)
- PDF (using pypdfium2 v5 / PDFium)
- Images: JPEG, PNG, WebP, AVIF, JPEG XL (JXL), BMP, GIF, TIFF, ICO

For RAR/7z/CBR/CB7, the viewer selects a recognized external CLI from Windows file associations, standard installation locations, PATH, or the WinRAR/7-Zip path configured in Settings → Archives. If an installed WinRAR includes a supported official console CLI, an additional 7-Zip installation is not needed just for RAR/CBR viewing. Unknown file-association applications are never launched or passed to ShellExecute.

NivisViewer does not download or install WinRAR or 7-Zip automatically, and neither binary is bundled. The currently verified WinRAR console CLI supports RAR only. If it cannot extract 7z/CB7 to stdout, the format is safely reported as `unsupported_archive`; one fallback is attempted only when 7-Zip is available.

Password-protected archives and PDFs can be detected, but password entry is not supported. For split RAR archives, ordinary `.rar` files and the first `.part1.rar` are candidates; later `.part2.rar` parts and `.r00` files are excluded from the book list. Split 7z files such as `.7z.001` are not officially supported. Solid archives retrieve only the requested page, so jumping to a later page may be slow.

PDFs are read-only: normal pages and annotations are displayed. Editing, text search or selection, links, table-of-contents UI, form input, JavaScript, and XFA are not supported. A PDF at 100% corresponds to 96 logical DPI. On high-DPI displays, the device pixel ratio affects render resolution; the page is rendered again at its target size about 180 ms after resizing or zooming stops.

<a id="requirements"></a>
## Requirements

- Windows
- Python 3.11 or later (for running from source)

<a id="setup"></a>
## Setup and launch

In PowerShell, go to the repository root, install dependencies, and launch the application:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The command line accepts files, folders, and multiple paths. Paths after the first open in separate Viewer windows.

```powershell
NivisViewer.exe "D:\Comics\book.cbz"
NivisViewer.exe --reuse "book1.cbz" "book2.pdf"
NivisViewer.exe --new-window "C:\Images"
NivisViewer.exe --browser-only "D:\Books"
```

`--new-window` and `--reuse` cannot be combined. `--no-restore` suppresses restoration of the last location for that launch only.

To run tests, install the development dependencies as well:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

<a id="drop"></a>
## Checking drops from Windows Explorer onto the Browser center

Run NivisViewer as a regular user, not as administrator. Drop a file from Explorer onto an item and onto empty space in the Browser center. The cursor should show that the drop is accepted; the Browser should display the file's parent folder, select and center the item, and leave Viewer closed. Dropping a folder displays that folder. For multiple files, items with the same parent are selected and the number of items from other parent folders is reported. HTTP/HTTPS URLs are not accepted.

Dropping from Explorer onto the Browser center means “show this location,” not copy or move. Only dropping an item already inside NivisViewer onto a folder item, folder tree, or favorites uses the existing copy/move operation.

<a id="license"></a>
## License

The current project license for NivisViewer is **GNU AGPL version 3 or later (`AGPL-3.0-or-later`)**. The full license text is in [LICENSE](LICENSE); [PROJECT_LICENSE.md](PROJECT_LICENSE.md) contains the application notice and copyright information. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the [ZipPlaFork comparison record](docs/ZIPPLAFORK_COMPARISON.md) for the provenance of structures migrated from ZipPlaFork. Licenses of dependencies, including MIT, BSD, Apache, LGPL, and GPL, are retained.
