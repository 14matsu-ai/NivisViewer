# Third-Party Notices

NivisViewer's current project license is **AGPL-3.0-or-later**.
See `PROJECT_LICENSE.md` and the full GNU text in `LICENSE`. Dependency license
choices and original copyright notices are retained, not relicensed en masse.
The 2026-09-10 installed-versus-pinned and existing-portable inventory review,
official source links, and remaining public-release blockers are recorded in
`docs/RELEASE_LICENSE_AUDIT.md`. This supplements the provenance below; it does
not certify a distributable release.

The installed PySide6/shiboken6 6.11.2 metadata declares open-source alternatives
`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` even though its wheel contains only
`LicenseRef-Qt-Commercial.txt`. That missing-file situation is not evidence that
a commercial license is required. Official version-matched GPL/LGPL texts are
retained in `licenses/Qt/6.11.2/` with source URLs and hashes. These texts alone
do not fulfill the source, module-specific, or third-party attribution duties.
In particular, the inspected portable folder also contains GPLv3-only Qt Virtual
Keyboard and Qt PDF's separate native dependency tree; it is not all LGPL Qt.

## ZipPlaFork-derived Viewer scheduling and raster-loading structure

This notice supersedes any earlier description that treated ZipPlaFork only as
design inspiration or stated that its Viewer processing structure was not
ported.

- Upstream repository: <https://github.com/himamon/ZipPlaFork>
- Fixed revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`
- Upstream license: GNU Affero General Public License v3.0 or later
  (`AGPL-3.0-or-later`)
- Preserved license and upstream notice: `licenses/ZipPlaFork/AGPL.txt` and
  `licenses/ZipPlaFork/About.txt`

The following fixed-revision upstream files, methods, and processing structures
were examined and used for the book-scoped ZIP raster Viewer runtime:

- `source/ZipPla/ImageLoader.cs`:
  `ImageLoader.GetJpegOrientation(Stream)`,
  `ImageLoader.LoadRotateBitmap(Stream)`,
  the private
  `ImageLoader.GetFullBitmap(Stream, bool, out ImageInfo, string, bool)`, and
  its public `ImageLoader.GetFullBitmap(Stream, string, bool)` wrapper. The
  traced JPEG path uses `SeekableStream.Seekablize(...)`, `Exif.GetAll(...)`,
  `StopBuffering()`, `new Bitmap(seekable)`, and
  `ViewerFormImageFilter.Rotate(...)`.
- `source/ZipPla/ImageLoader.cs`: `VirtualBitmapEx`,
  `VirtualBitmapEx.DataSizeInBytes`, and
  `BitmapEx.GetDataSizeInBytes()`. The accounting includes both other/display
  data and the retained source bitmap.
- `source/ZipPla/PackedImageLoader.cs`: `PackedImageLoader` and
  `ReadOnMemoryMode` (normally `None`), including the retained archive
  reader/entry index and the physical-entry stream passed to
  `ImageLoader.GetFullBitmap(Stream, ...)`.
- `source/ZipPla/ViewerForm.cs`: the one-page-at-a-time worker, current-first
  and nearby work selection, UI completion callback, completed-bitmap
  replacement, loading-mask regions, `priorityLevel` full-page ranking, and
  `ReduceUsingMemory` work-order-based disposal traced end to end.
- `source/ZipPla/GenerarClasses.cs`:
  `BackgroundMultiWorker.SetWorksOrder` and its completion-time selection of
  the first unfinished item from the latest order.
- `source/ZipPla/ViewerFormImageFilter.cs`:
  `ViewerFormImageFilter.GetOrientation(int)` and
  `ViewerFormImageFilter.Rotate(...)`.
- `source/ZipPla/Exif.cs`: `Exif.GetAll(Stream)`.

NivisViewer's `app/zip_raster_book_runtime.py`, especially
`ZipRasterBookRuntime`, `_ZipRasterUnitJob`, and `_ZipRasterFrameStore`,
is a **direct structural translation/port** of that worker ownership and
work-order control flow: one active display-unit job, entry read through
display-ready image preparation in that job, current completion before ordered
prefetch, completed-artifact retention until real unit/byte pressure,
current/next/previous protection, work-order/distance eviction, and
cancellation or rejection of obsolete work. The active three-unit scheduling
frontier is not used as cache membership. It is not a verbatim or line-for-line
translation of the upstream C# source.

The same fixed revision's page-indexed `PreFilteredImageArray`,
`OriginalImageInfoArray`, `ResizedSizeArray`, and `ResizedImageArray` informed
the decision to give decoded sources a longer lifetime than layout-specific
display artifacts.  NivisViewer's `_ZipRasterSourceStore`, preview/full
resolution-sufficiency keys, paired source/frame validity, combined Qt byte
ledger, and direct-mode magnifier projection are new Python/Qt designs rather
than line-for-line translations.  They retain the AGPL provenance notice for
the surrounding runtime while avoiding the upstream WinForms/GDI ownership.

NivisViewer's `app/viewer_presentation_state.py` and the corresponding
integration in `app/viewer_window.py` and `app/viewer_widget.py` also adopt the
single-owner completed-artifact publication structure traced through
`source/ZipPla/ViewerForm.cs` methods
`bmwLoadEachPage_EachRunWorkerCompleted`, `SetNewResizedImage`,
`showCurrentPage`, and `pbView_Paint` / `pbView_PaintToCanvas`. In NivisViewer,
the accepted complete frame, displayed page, slider, status, page history, and
reading progress are published from one presentation commit instead of being
updated independently. This processing organization is treated as a direct
structural port and as AGPL-3.0-or-later-derived. It is not a verbatim or
line-for-line translation of the upstream C# source.

NivisViewer's `app/viewer_page_list_runtime.py` and its integration in
`app/book_session.py` and `app/viewer_window.py` adopt the visible-only
thumbnail ownership structure from `source/ZipPla/CatalogForm.cs`:
`CatalogForm.bmwMakePreview_RunWorkerStarting` (lightweight item creation),
`ThumbViewerItem`, `ThumbViewerItem.LoadAsync`, `ThumbViewerItem.Clear`,
`ThumbViewer.SilentSet`, the data/show-index mappings,
`ThumbViewer.OnPaint` / `PaintPart`, `DrawItem`, `preRenderScroll`,
`OnMouseWheel`, and `ThumbViewer.Clear`.  The structure was introduced in
upstream commit `8ea492821efa95ac66483246c5b17fa71b400f0e` and its thumbnail
concurrency was reduced to one worker by the fixed/head commit
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.

The adopted structure is: lightweight virtual rows, direct page/row mapping,
thumbnail requests only for the visible range plus a small margin, one
low-priority thumbnail job, explicit disposal outside that range, and local
publication of only the completed item.  This is treated as a direct
structural port and as AGPL-3.0-or-later-derived.  The Qt model/view classes,
book/spec generations, cooperative cancellation, separate read-only source
sessions, target-sized `QImage` byte budget, GUI-thread-only accepted
`QPixmap/QIcon` upload, current-Viewer pause/paint-resume gate, and queued
callback/source drainage are independent NivisViewer implementations.  The
upstream paint-time task start, static process-wide semaphore, incomplete
cancellation, 33 ms sleep, GDI bitmap/canvas code, and NTFS alternate-data-
stream thumbnail cache were not copied.

The following are independent NivisViewer Qt/Python implementations, not
literal ZipPlaFork translations:

- `app/image_source.py`: the persistent Python `ZipFile`/entry index,
  request-local cancellation, deferred ZIP close, Pillow decode adapter,
  Qt JPEG decoder-sized adapter, and WebP adapter used by the book runtime.
  The reserved-`QByteArray` and sequential-`QIODevice` JPEG adapters were
  measured for the earlier compatible-path A/B and remain regression/profiling
  code; neither defines the current runtime architecture.
- GUI-thread-only `QPixmap.fromImage`, immutable
  source/layout/request-generation checks, book-epoch and DPR tokens,
  immutable presentation snapshots, committed history/progress policy,
  byte-budgeted page records, and atomic single/spread/wide-split ViewerWidget
  frame replacement.
- NivisViewer-specific Browser-lane gating, render-spec adaptation for
  page-list-independent single/spread/rotation/filter/magnifier behavior,
  stale-result rejection, and BookSession-owned shutdown.

Those Qt/Python mechanisms implement the same performance objective but were
written independently for NivisViewer. The structural portions identified
above remain documented as ZipPlaFork-derived and
AGPL-3.0-or-later-derived. See section 11 of
`docs/ZIPPLAFORK_COMPARISON.md` for the current method-level mapping and
adoption decision. `docs/ZIPPLAFORK_FINAL_ADOPTION.md` is retained only as the
superseded compatible-path A/B record.

NivisViewerは現在、次のソフトウェアを直接依存として使用しています。

- PySide6 / Qt：GUI
- Pillow：画像デコードと画像処理
- natsort：ファイル名の自然順ソート
- pypdfium2 / PDFium：PDFページの読み取り専用レンダリング

Viewer性能設計の比較と構造移植には、AGPL-3.0-or-laterのZipPlaFork
（https://github.com/himamon/ZipPlaFork、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`）を使用しています。
固定snapshotで確認したupstreamの表示は、`license/About.txt`の
`Copyright ©  2016 Rio's Toolbox`と、
`source/ZipPla/Properties/AssemblyInfo.cs`の
`Copyright © 2016-2017 Rio's Toolbox`です。

NivisViewerはZIP／CBZ book全体について、単一display-unit job、current中心の
work order再構築、current完成後のnext／previous dispatch、完成frameのatomic
publish、近傍page record保持をZipPlaFork由来の構造として直接移植しています。
single／spread、rotation、filter、page-list表示、magnifierなどの機能を理由に
従来のdecode task、render task、prepared/source cache経路へfallbackしません。
NivisViewer独自の拡張は、移動方向を反映した近傍順、
request/generation/source/layout検証、協調cancel、decoder-sized JPEGを同一job
内のdecoder strategyとして使うこと、GUI-thread QPixmap化、完成前の旧frame
保持、全page配列をwheelごとに生成しないdistance rank、decode前のmemory
admission、およびBookSessionによるruntime／archive lifetime所有です。

C#ソースの逐語・行単位コピー、GDI固有処理、binary、upstream source fileの
同梱は行わず、ZipPlaForkをruntime／build依存にもしていません。ただし、
上記worker ownershipとwork-order control flowは直接的な構造翻訳であり、
AGPL-3.0-or-later由来として扱います。固定revision、元file／class／method、
処理内容、NivisViewer側の対応箇所、移植境界、cache accountingの相違は
`docs/ZIPPLAFORK_COMPARISON.md`と
`docs/ZIPPLAFORK_FINAL_ADOPTION.md`に記録しています。upstreamの完全な
AGPL本文は`licenses/ZipPlaFork/AGPL.txt`、正式通知は
`licenses/ZipPlaFork/About.txt`として、固定revisionの内容を変更せず保持して
います。

RAR／7z／CBR／CB7閲覧では、利用者環境のWindows関連付け、標準インストール先、PATH、または設定画面で指定されたWinRAR／7-Zipの認識済みCLIを任意で呼び出します。

- WinRARはNivisViewerとは別の第三者ソフトウェアです。現在の配布物へWinRAR、UnRAR、RARのバイナリを同梱しません。
- 7-ZipもNivisViewerとは別の第三者ソフトウェアです。現在の配布物へ7-Zipバイナリを同梱しません。
- NivisViewerは両アプリを自動取得・自動インストールせず、未知の関連付けアプリを起動しません。

動画サムネイルではWindows Shellを優先し、利用者が別途用意したFFmpegを任意で呼び出せます。NivisViewerはFFmpegを自動取得・自動インストールせず、現在の配布物へFFmpeg binaryを同梱しません。将来同梱する場合は、採用するbuildと含まれるcodecごとの公式ライセンスおよび再配布条件を改めて監査し、必要な文書を`licenses/`へ追加します。

各依存ソフトウェアのライセンス名や配布条件は、採用するバージョン、配布形態、同梱物によって確認すべき内容が変わるため、この文書では推測による断定を行いません。

正式公開前に、実際に採用する固定バージョン（pypdfium2と同梱PDFiumを含む）の公式配布物および公式ライセンス文書を監査します。pypdfium2については採用wheelへ同梱された正式なライセンスファイルを基準にします。必要な著作権表示、ライセンス本文、NOTICEなどを収集し、`licenses/`ディレクトリへ同梱した上で、この文書に採用バージョンと確認結果を記録します。

frozen配布では、実際に含まれるPySide6／Qt DLLとplugin、shiboken6、Pillowのnative component、pypdfium2／PDFium native binary、PyInstaller bootloaderを監査対象にします。`scripts/collect_licenses.py`はインストール済みdistributionの正式なLICENSE／COPYING／NOTICE等だけを収集し、バージョンmanifestを生成します。自動収集結果は正式公開前に必ず人手で監査します。PyInstallerはビルドツールである一方、生成物にはbootloaderが含まれるためruntime componentとは区別して記録します。

将来WinRAR、UnRAR、RAR、7-Zipのいずれかを同梱する配布形態へ変更する場合は、その時点の公式ライセンス、構成要素ごとの条件、著作権表示および再配布条件を改めて監査し、必要な文書を`licenses/`へ追加します。
