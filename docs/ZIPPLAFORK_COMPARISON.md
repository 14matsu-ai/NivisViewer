# ZipPlaFork comparison record

## 2026-09-24: Browser wheel-distance choice

The supplied Browser scroll audit identifies the reference as the
`CatalogForm.Designer.cs` menu in ZipPlaFork commit
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` (AGPL-3.0-or-later): separate
Tiny/Small/Normal/Large/Huge distance choices exist alongside separate 1/2/3
line choices. The reference file is
[CatalogForm.Designer.cs](https://github.com/himamon/ZipPlaFork/blob/07955f5267e2fb92d6fc6e40fde2507d8fb07b3b/source/ZipPla/CatalogForm.Designer.cs).
The audit did not establish the distance handlers' exact arithmetic, and this
change makes no claim of numeric compatibility.

| Reference structure | NivisViewer mapping |
| --- | --- |
| `CatalogForm.Designer.cs`, `CatalogForm`: distinct distance presets and line-count presets | `app/browser_wheel_scroll.py`, `BrowserWheelScrollAccumulator`; `app/explorer_list_view.py`, `ExplorerListView.wheelEvent`: preserve system and row modes, add independent logical-pixel and viewport-percentage modes |

Only the menu-design principle was adopted. No ZipPlaFork code, constants, or
processing algorithm were copied or translated for wheel handling, so this
change introduces no AGPL-derived code or additional license notice.

## 2026-09-19: tag click/menu/label correction after TODO18 feedback

Freshly inspected local `../ZipPlaViewer` against repository
https://github.com/himamon/ZipPlaFork, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.
`git cat-file -t` confirmed the commit and `git diff --exit-code` for
`source/ZipPla/Program.cs`, `CatalogForm.cs`, and `CatalogForm.Designer.cs`
returned zero with no differences. No checkout or reference changes occurred.
AGPL-3.0-or-later; Copyright © 2016 Rio's Toolbox, assembly © 2016–2017
Rio's Toolbox. Existing `licenses/ZipPlaFork/AGPL.txt` and `About.txt` retained.

| Fixed source / observed behavior | Adopted mapping |
| --- | --- |
| `Program.cs:1352`, `Program.SetTagsToToolStripMenuItems`: all/none/mixed initial state, both mouse buttons toggle, right button keeps dropdown open | `browser_tag_dialogs.py:TagSelectionMenu`, explicit Qt mouse handling and colored registry swatches |
| `Program.cs:1918–1954`, `PrefixEscapedToolStripMenuItem.ToggleCheck`: unchecked -> checked; mixed -> unchecked; checked -> mixed only if initially mixed, otherwise unchecked | `next_tag_state`, `TagCheckBox.nextCheckState`, `TagSelectionMenu._toggle` (behavioral translation) |
| `CatalogForm.cs:12693–12857`, `cmsRightClickPrepareAndShow`: uncheck-all, tag list, editor, register unknown tags from selected filenames; disable conflicting operations while draft changed | Real tag submenu, uncheck-all, manager, unknown-tag registration draft; other file-operation actions disabled while tag edits pending |
| `CatalogForm.cs:13578`, `renameForTextBoxRatingTagPageSequence`, and `cmsRightClick_Closed:19815`: apply tag draft on root menu close; keyboard close cancels; mixed preserves each file's membership and unknown tags survive | Root menu return applies through existing rename batch; left click closes, right click stays; Escape discards. `edited_tags` remains authoritative for preservation. Root hide also hides child menu. |
| `CatalogForm.cs:6846–6926`, `drawTags`: reverse **filename** tag order from bottom right, grow left, wrap upward; reserve bottom-left icon area, clip to thumbnail | `BrowserItemDelegate._paint_tags`; registry supplies color/existence only, not draw order. Maintain Nivis upper rating band and reserve the largest existing type badge. |
| `CatalogForm.cs:6659–6662`, draw margins top=1/bottom=0; label size follows measured text | Replace Qt line-height+4 with actual glyph bounding height+4. Two logical pixels each side guard hinted/fractional-DPI glyphs; no font shrink. One-pixel guard failed edge tests, so was not retained. |

Bug reproduced: the original Qt `setTristate(True)` on every checkbox cycles
unchecked -> partial -> checked, making a single click on an absent tag a no-op
on Apply, even for a single selected item. It now follows the observed ZipPla
cycle. A single item never gains a partial state through clicks. For initially
mixed selection the cycle is dash -> empty (remove all) -> check (add all) ->
dash (preserve original per-file membership). Untouched dash remains unchanged.
JA/EN explanations explicitly identify all three meanings.

Toolbar tag control now belongs to the same corner container as the rating
filter, immediately to its left, as the user explicitly requested. It is no
longer attached after the filename search toolbar. The reference toolbar's
`setTagFilterLocation` (around 23768–23826) and tag-filter code were read; its
general-search injection is NOT adopted. Existing exact independent tag filters,
filename-string identities, no IDs/history/aliases, no mass file rename on
registry edits and unknown-tag discoverability remain intentional differences.
The existing toolbar selected-item dialog retains explicit Apply/Cancel; the
new context tag submenu follows the reference's close-to-apply behavior.
Delete remains separated immediately above Properties; ZIP remains above Cut.

Verification: real Qt mouse clicks + Apply exercise single add/remove,
old-OFF/new-ON replacement with unknown tag preservation, mixed selection's
four outcomes, Cancel, context left-close/right-stay/Escape, manager remove and
re-register, exact filter keyboard selection + Apply and toolbar geometry.
Synthetic render checks cover filename order vs reversed registry order,
compact color-box height, actual JA and descending Latin glyphs inside the
background at DPR 1/1.25/1.5/2. Offscreen Qt exposes no fonts here, so rendering
tests load the existing Windows Meiryo font into the test process only (not an
installation). A transient test crash was caused by sending an input-method
event to a null focus widget; the test now obtains and validates the actual
table editor. No production input workaround was added.

Final focused results: 53 operation/tag/menu tests and four separate font/pixel
tests passed; the actual global `py -3.11` also passed all 10 operation-click
tests. Standard nine-page 2400x3600 high-detail forward/reverse/reversal/
ping-pong/rapid offscreen evaluation completed (`out/tag-ux-navigation.json`).
No dependency, user media, real GUI/native input, build, commit or push.

## 2026-09-19: Browser filename tags (TODO18)

Source: https://github.com/himamon/ZipPlaFork, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.
Copyright © 2016 Rio's Toolbox; source assembly copyright © 2016-2017 Rio's Toolbox.
The existing `licenses/ZipPlaFork/AGPL.txt` and `About.txt` remain the notices.
The local reference checkout's inspected files were verified with `git diff
--exit-code` against this fixed revision before inspection.

| Source file / class / method | Adopted behavior | NivisViewer mapping |
| --- | --- | --- |
| `source/ZipPla/ZipPlaInfo.cs`, `ZipPlaInfo`, `TagArray`, `CanBeTag`, `GetPathOfCurrentInfo` (lines 23–38, 77–80, 140–147, 262–306) | Comma-separated `t` filename membership, physical rename, permitted tag characters | `zippla_filename_metadata.py`, `browser_tags.py`, `rating_rename_service.py` |
| `source/ZipPla/Program.cs`, `SetTagsToToolStripMenuItems` (1352–1385); `CatalogForm.cs`, `renameForTextBoxRatingTagPageSequence` (13602–13766) | All/none/mixed selection; preserve mixed membership and unregistered tags | `ItemTagsDialog`, `edited_tags`, `BrowserWindow.set_tags_for_paths`, shared rating rename batch |
| `source/ZipPla/TagEditForm.cs`, `loadTags`, `saveTags`, validation and color/order controls (90, 121–230, 300–430); `ZipTag.cs`, `ZipTagConfig` (170–183) | Separate registered names/colors/order; registry rename/delete never rewrite existing file tags | `TagManagerDialog`, `ConfigManager.browser_tag_registry` |
| `source/ZipPla/CatalogForm.cs`, `drawTags` (6846–6910) | Only registered exact names receive colored labels; re-registering the name restores labels | `visible_tags`, `BrowserItemDelegate._paint_tags` |

This is a structural/behavioral adaptation under the above license, not a
WinForms UI port. Filename tag strings are identities: no IDs, alias history,
historical-name reservations or automatic library-wide replacement. The manager
is a draft until OK; only the selected-item editor's Apply requests renames.
Unregistered names remain visible in that editor so users can remove old tags
and add new ones in one Apply. Tag-only serialization preserves unrelated
parameter text, including cover precision. Distinct folder collisions are automatically
skipped rather than offering replace/merge. Case-only tag edits use the established
staging/rollback rename path for files and folders; its collision check ignores
only the exact source directory entry, never a different case-folded target.
Equal normalized model keys retain their thumbnail caches. File renames retain the existing
Windows no-overwrite and timestamp behavior. Affected open Viewers use the
existing close-confirmation contract; they are not silently rebound to stale
source paths. Physical folders/images/archives are supported, not virtual entries.

Intentional user-approved divergence: ZipPla's tag buttons inject general
filename search terms (`CatalogForm.cs` 23636–23668; `SearchManager.cs` 54–128,
154–242). Nivis instead matches exact tag membership independently: all/any
included names plus exclusion, AND-composed with the existing literal filename
search and rating predicate. The visible-list pipeline and snapshot identities
carry these conditions; Esc clears all filters. Label layout is a bounded row
beside the type icon, preserving Nivis's thumbnail geometry and rating hit area.


## 1. Scope, fixed revision, and license provenance

この比較と構造移植は、次の固定snapshotだけを移植元として監査した。

- Repository: https://github.com/himamon/ZipPlaFork
- Fixed revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`
- License: AGPL-3.0-or-later
- `license/About.txt`:
  `Copyright ©  2016 Rio's Toolbox`
- `source/ZipPla/Properties/AssemblyInfo.cs`:
  `Copyright © 2016-2017 Rio's Toolbox`
- `README.md:16-17`: original ZipPlaと同じくAGPLである旨

固定revisionはViewer実装を比較するためのsnapshotである。このrevisionの
commit自体がViewerの単一workerを導入したわけではない。commitの変更対象は
`CatalogForm`のthumbnail同時読み込み制限であり、Viewerの
`bmwLoadEachPage.ThreadCount = 1`はsnapshot内に既に存在する。

2026-07-31に`refs/heads/master`を再照合し、現在HEADも固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`と同一Git objectであることを
確認した。このため、固定snapshot以後に取りこぼしたViewer性能変更はない。

NivisViewerは個人利用専用で、ZipPlaForkのコード、アルゴリズム、処理構造の
コピー、翻訳、移植を許可している。今回のPython差分にC#の式、pixel loop、
GDI操作、`BackgroundWorker`実装の逐語的なコピーまたは翻訳はない。一方、
単一lane、queue再構築、display-unit単位のpaced dispatch、cache lifecycle
などは、repository規則に従いAGPL-3.0-or-later由来の構造移植として記録する。

ZipPlaForkはruntime/build dependencyではなく、binaryや実装source fileを
NivisViewerへ組み込んでいない。固定revisionの`license/AGPL.txt`は
`licenses/ZipPlaFork/AGPL.txt`、`license/About.txt`は
`licenses/ZipPlaFork/About.txt`としてbyte-for-byte保持している。

監査対象は次のとおり。

- `source/ZipPla/ViewerForm.cs`
- `source/ZipPla/ViewerForm.Designer.cs`
- `source/ZipPla/SettingForm.cs`
- `source/ZipPla/GenerarClasses.cs`
- `source/ZipPla/PackedImageLoader.cs`
- `source/ZipPla/ImageLoader.cs`
- `source/ZipPla/BitmapResizer.cs`
- `source/ZipPla/QuickGraphic.cs`
- `source/ZipPla/LongVectorImageResizer.cs`
- `source/ZipPla/CatalogForm.cs`
- base import/core commit:
  `5e942ef0006decf01c21d9dbe037dba50e763420`
- visible-thumbnail commit:
  `8ea492821efa95ac66483246c5b17fa71b400f0e`
- fixed snapshot / one-thumbnail-worker commit:
  `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`

## 2. ZipPlaFork Viewer path: end-to-end inventory

| 段階 | File / class / method | 固定revisionで確認した処理 |
| --- | --- | --- |
| 入力受付 | `ViewerForm.cs:1066-1085`, `ViewerForm` constructor; `:15852-15855`, default shortcut table | `PreviewKeyDown`、`KeyboardShortcut`、mouse gestureを接続し、既定のWheelDown/UpをNext/Previousへ割り当てる。 |
| command dispatch | `ViewerForm.cs:13042`, `getMouseGestureSettingTemplate` | `Command.NextPage` / `PreviousPage`を`moveToNextPage` / `MoveToPreviousPage`へ結び付ける。 |
| ページ移動要求 | `ViewerForm.cs:12403`, `moveToNextPage`; `:12424`, `MoveToPreviousPage`; `:9279`, `movePageNatural` | natural `NextPage` / `PreviousPage`は**現在の数値band**がreadyなら1回だけ次のbandへ進める。移動先は未完成でもよく、そこで次のnatural入力を拒否する。`MoveForwardOnePage` / `movePageToMinimulForward`は別経路で、ready frontierを越えてlogical currentを更新できる。 |
| current / 未完成判定 | `ViewerForm.cs:1903`, `NextPage`; `:1951`, `PreviousPage` | singleでは現在pageがreadyならcold destinationへ最大1 step進み、そのpageが完成するまで後続WheelDownを捨てる。spreadでは原則として現在の数値2-page bandが揃ってから次bandへ最大1 step進む（wide/cover分岐は1-page動作を取り得る）。natural入力にpending final targetやburst coalescerはない。 |
| 方向管理 | `ViewerForm.cs:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | 直前の移動方向を状態として保持しない。常に新しい`currentPage`を中心にpriorityを再計算する。 |
| current / next / previous priority | `ViewerForm.cs:5586`, `priorityLevel` | `M = MaxPageCountInWindow`の固定数値bandを使う。`M=1`はcurrent / `c+1` / `c-1`、`M=2`は`c,c+1` / `c+2,c+3` / `c-2,c-1`をlevel 0/1/2とし、残りをlevel 3へ置く。これはwide/coverを含む実際のdisplay-unit topologyではなく、逆方向移動中も数値上のnextが先である。 |
| queue / scheduler | `ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage`; `GenerarClasses.cs:247`, `SetWorksOrder`; `:266`, `privateRunWorkerCompletedEventHandler` | Viewer page workerは1 thread。priority permutationを差し替え、現在実行中の1 jobはpreemptせず、完了境界で次の未開始jobを新orderから選ぶ。 |
| queue再構築 | `ViewerForm.cs:5371`, `bmwLoadEachPage_EachRunWorkerCompleted`; `:5558`, `SetBackgroundMode` | current変更を検出するとworker完了時に全page orderを再構築する。pause中は`SetBackgroundModeIfPausing`から反映する。 |
| ZIP entry一覧 | `PackedImageLoader.cs:283`, constructor; `:1197`, `getZipArchiveEntries` | `ZipArchive`を開きentry tableを作る。archive全体をmemoryへ置くoptional pathも持つ。 |
| ZIP entry read / 展開 | `PackedImageLoader.cs:1781`, `OpenImageStream`; `:1795`, private `OpenImageStream`; `:1856`, `OpenInnerImageStream` | 選択entryのstreamを開き、同じpage job内で展開と画像openを行い、entry streamを処理後にdisposeする。 |
| decode / filter | `ViewerForm.cs:3177`, `bmwLoadEachPage_DoWork`; `ImageLoader.cs`, `BitmapEx.ConvertToBitmapEx` | entry open、full image decode、orientation/filter、`PreFilteredImageArray`生成を1 page job内で行う。 |
| resize / scale | `ViewerForm.cs:5273`, `GetResizedSize`; `:3405-3449`, scaling selection; `BitmapResizer.cs`; `QuickGraphic.cs`; `LongVectorImageResizer.cs` | viewportとbindingからtarget sizeを決め、同じjobでdisplay sizeへresizeする。page schedulerは1 threadだが、pixel resizer内部はparallel pathを使用できる。 |
| display-ready生成 | `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | decodeからresize/post-filter、`VirtualBitmapEx`生成までをliteralな1 background page jobで完結する。 |
| publish | `ViewerForm.cs:5451`, `SetNewResizedImage` | 完成した`VirtualBitmapEx`だけを`ResizedImageArray[idx]`へ代入する。admission失敗時はdisposeして`ReworkOrder`する。 |
| 表示差し替え | `ViewerForm.cs:5371`, `bmwLoadEachPage_EachRunWorkerCompleted`; `:6438`, `showCurrentPage` | singleのcold targetではlogical currentとtrackbarを先に進め、入力直後とworker完成後にinvalidateする。完成後は`ResizedImageArray`へ公開済みのartifactへ差し替える。 |
| paint / loading frame | `ViewerForm.cs:6748-6756`, `:6768`, `pbView_Paint`; `:6990`, `pbView_PaintToCanvas` | cold target未完成時は空clearせず、再利用canvas上の旧frameをin-placeでhalf-gray化してloading frameとして描く。通常coldはloading paint 1回＋完成paint 1回で、NivisViewerの「旧frameを無加工保持」とは異なる。 |
| prefetch | `ViewerForm.cs:5558`, `SetBackgroundMode`; `GenerarClasses.BackgroundMultiWorker` | page配列全体をwork setとし、current近傍から遠方へ1 jobずつ進む。memory admission不能時は再work化または必要近傍完成後にpauseする。 |
| source / display cache | `ViewerForm.cs` fields `PreFilteredImageArray`, `OriginalImageInfoArray`, `ResizedSizeArray`, `ResizedImageArray` | decoded/filter済みsourceとdisplay-ready artifactを別配列で保持するが、同じpage indexとeviction lifecycleに結び付ける。 |
| memory accounting / eviction | `ViewerForm.cs:3522-3528`, `VirtualBitmapEx`生成; `ImageLoader.cs:1660-1673`, constructor; `ViewerForm.cs:5471-5524`, `ReduceUsingMemory` | `VirtualBitmapEx.DataSizeInBytes`へ原寸`PreFilteredImageArray`相当bytesとresize済みdisplay bytesの双方を加算する。evictionでは同pageのresized bitmap、原寸source、size/infoを同じlifecycleでdisposeする。 |
| 方向反転 | `ViewerForm.cs:5377-5384`, `SetBackgroundMode` call | travel direction専用のqueue反転はない。新current基準のorder再構築により、旧方向の未開始jobを後方へ送る。実行中の最大1 jobは完了する。 |
| stale work / source replacement | `ViewerForm.cs:2757-2771`, open path; `:2882-2915`, `Reload` / `clearResizedImageArray`; `GenerarClasses.cs`, `RunWorkerAsyncWithInterrupt` | 新loader/arrayへ切り替える前に旧resized配列をclearし、`waitCancel=true`で旧worker終了を待つ。意味のあるgeneration IDではなく、wait-cancelとobject replacementで隔離する。 |
| spread protection | `ViewerForm.cs:1903-2002`, `NextPage` / `PreviousPage`; `:5273`, `GetResizedSize`; `priorityLevel` level 0 | navigationのsize/ready判定はbinding/wide/cover分岐を持つ一方、worker priorityは`M=2`の固定数値2-page bandをlevel 0として扱う。通常partnerは同時優先されるが、実display unitとの完全な同一性は保証されない。 |

### 2.1 通常color JPEG 1ページの具体的処理列

対象はsingle page、standard fit、identity EXIF、filterなし、
`ReadOnMemoryMode.None`である。

1. book open時に`File.OpenRead`とarchive readerを各1回だけ作り、
   全entry metadataを配列化する
   (`PackedImageLoader.cs:283-330`, `:1197-1244`, `:1341-1445`)。
2. page jobは`EntryArray[page]`に記録済みのphysical indexから
   `ZipArchiveEntry.Open()`または`OpenEntryStream()`を1回呼ぶ
   (`ViewerForm.cs:3192-3203`, `PackedImageLoader.cs:1781-1917`)。
3. non-seek streamだけ`SeekableStream`とprefix用`MemoryStream`を各1個作る。
   EXIFを読んだprefixだけbufferし、`StopBuffering`後は元entry streamを
   decoderが直接読む (`PackedImageLoader.cs:2731-2898`)。
4. `new System.Drawing.Bitmap(stream)`でJPEGを原寸decodeする。managed層で
   entry全体の`byte[]`、`MemoryStream`、`ToArray`は作らない
   (`ImageLoader.cs:2065-2068`)。
5. orientationが90/270度の時だけ追加Bitmapを1個作る。通常24bpp color
   JPEGはformat変換せず、grayscale判定を1回行う
   (`ViewerFormImageFilter.cs:177-220`, `ViewerForm.cs:4321-4485`)。
6. 原寸sourceを`PreFilteredImageArray[page]`へ保存し、同じworker job内で
   `GetResizedSize`、resize 1回、任意post-filter、`VirtualBitmapEx`生成まで
   完了する (`ViewerForm.cs:3279-3528`, `BitmapResizer.cs:42-188`)。
7. worker completionをUI synchronization contextへ1回marshalし、
   `ResizedImageArray[page]`へ1回insert、currentなら
   `showCurrentPage(false)`からinvalidateする
   (`GenerarClasses.cs:266-287`, `ViewerForm.cs:5371-5468`)。
8. paintは再利用canvasへfit済みbitmapをscanline copyし、最後に
   `DrawImageUnscaled`でwindowへ転送する
   (`ViewerForm.cs:6768-6858`, `:6990-7250`, `:7386-7414`)。

| 処理 | ZipPlaFork full cold | display hit |
| --- | ---: | ---: |
| archive再open / reader再生成 / entry全走査 | 0 / 0 / 0 | 0 / 0 / 0 |
| physical entry参照 / entry stream open / 展開 | 1 / 1 / 1 | 0 / 0 / 0 |
| entry全体byte copy / byte[] / full MemoryStream | 0 / 0 / 0 | 0 / 0 / 0 |
| EXIF prefix MemoryStream | 通常1 | 0 |
| JPEG decoder / decode | 1 / 原寸1 | 0 / 0 |
| orientation追加Bitmap | identity 0、90/270度時1 | 0 |
| display resize / resized Bitmap / wrapper | 1 / 1 / 1 | 0 / 既存1 / 既存1 |
| page worker job / worker→UI callback | 1 / 1 | 0 / 0 |
| source cache lookup / insert | 1 / 1 | 0 / 0 |
| display cache insert | UIで1 | 0 |
| 入力直後 / 完成後invalidate | 1 / 1 | 1 / 0 |
| 通常paint | loading 1＋完成1 | 1 |

方向反転時は実行中の旧方向jobをpreemptしないため最大1件残り、その結果も
通常どおりcacheへ入る。worker完了境界で新current中心に全orderをsortし直す。
個別のqueued worker objectはなく、次に選ばれるjobだけが変わる。

## 3. NivisViewer Viewer path: end-to-end inventory

| 段階 | File / class / method | 現在の未commit実装 |
| --- | --- | --- |
| 入力受付 | `app/viewer_widget.py`, `ViewerWidget.wheelEvent` | Ctrl+wheelはzoom、それ以外は`nextRequested` / `previousRequested`をemitする。native inputは検証で使用しない。 |
| ページ移動要求 | `app/viewer_window.py`, `ViewerWindow.next_page` / `previous_page`; `app/viewer_page_navigation.py`, `ViewerPageNavigationController.next_display_unit` / `previous_display_unit` | navigation controllerが`PageModel.next` / `previous`を呼び、`_on_page_navigation_changed`から`_refresh_view`へ進む。 |
| current決定 | `app/page_model.py`, `PageModel.next` / `previous` / `go_to_index`; `app/viewer_window.py`, `_refresh_view` | logical current、slider、historyは表示artifact完成前にtargetへ進む。visual frameだけは完成まで旧frameを維持する。この点はZipPlaForkのnormal navigationと同一ではない。 |
| request isolation | `app/viewer_window.py`, `_active_request_id`, `ViewerDisplayUnit`; `app/image_cache.py`, `generation`; `app/viewer_widget.py`, `_display_request_generation`, `_render_generation` | request ID、source generation、source identity、render generationを各境界で照合し、古いdecode/render結果をcurrentへcommitしない。 |
| 方向管理 | `app/viewer_window.py`, `_navigation_prefetch_direction`, `_configured_prefetch_units` | 直前centerとの差から進行方向を明示的に追跡する。反転時は両側の直近unitを先に保護し、その後を新方向優先に並べる。これはZipPlaForkより強い独自拡張。 |
| 共通priority | `app/image_work_coordinator.py`, `ImageWorkPriority`; `app/image_cache.py`, `ensure_loaded`; `app/viewer_widget.py`, `prepare_display_units`, `_prepare_display`, `_request_magnifier_render` | decodeとrenderを同一priority domainへ統一する。current=800、spread partner / 近傍preparedは700以下、interactive rerender / magnifier=600、next=500、previous=400。 |
| production execution lane | `app/image_work_coordinator.py`, `ImageWorkCoordinator`; `app/image_cache.py`, `_start_task`; `app/viewer_widget.py`, `_start_render_task`; `app/viewer_window.py`, `_build_ui` | productionではdecode taskとdisplay render taskを同じ1-worker Viewer laneへ送る。Browserは別laneで、current cold処理中はpauseする。coordinator未指定時だけ各classの1-worker fallback poolを使う。 |
| paced scheduler | `app/viewer_window.py`, `_start_raster_prefetch_pipeline`, `_advance_raster_prefetch_pipeline`, `_clear_raster_prefetch_pipeline` | raster prefetchは1 display unitだけをadmitし、そのunitがsource decodeとprepared renderを終えてterminalになるまで次unitをqueueへ入れない。budget拒否、cancel、render failureではfrontierで停止する。 |
| literal job境界 | `app/image_cache.py`, `_ImageLoadTask.run`; `app/viewer_render.py`, `ViewerRenderTask.run`; Qt queued signal boundary | ZipPlaForkのliteralな1 jobとは異なる。decode taskが`QImage`をGUI-thread signalで公開し、別`ViewerRenderTask`がscaleし、GUI threadが`QPixmap`化する。paced single laneにより実行順だけを「1 unit完了後に次unit」と同等化する。 |
| queue再計画 / 反転 | `app/viewer_window.py`, `_refresh_view`, `_schedule_prepared_display_prefetch`; `app/image_cache.py`, `preload_around`, `ensure_loaded`; `app/viewer_widget.py`, `prepare_display_units`, `_queue_render` | 新currentごとにdecode/prepared planを再構築する。`tryTake`可能な未開始jobは昇格だけでなく降格も行い、旧方向のqueued jobを残さない。実行中の1 jobはpreemptしない。 |
| input coalescing | `app/viewer_window.py`, `_queue_decode_demand`, `_apply_pending_decode_demand`, `_apply_pending_display_demand` | 16 ms input-idle境界で高速wheelの通過targetを置換し、request ID/generation確認後に最終targetだけを登録する。これはZipPlaForkの`SetWorksOrder`そのものではなくNivis独自改善。 |
| ZIP entry read / 展開 | `app/image_source.py`, `ZipImageSource._read_entry_stream` | `zipfile.ZipFile` entryを1 MiB chunkでrequest-local cancellation確認しながら、size preallocated `BytesIO`へ展開する。ZipPlaForkのdirect entry-stream decodeとは異なり、entry全体を一度buffer化する。 |
| decode | `app/image_cache.py`, `_ImageLoadTask._run_load`; `app/image_source.py`, `ZipImageSource.open_qimage_at_most`, `_read_jpeg_qimage_at_most` | standard fit JPEGは`QImageReader.setScaledSize()`でphysical Viewer size以下へdecoder-scaleする。actual/manual/non-standard、調整あり、unsupported formatはfull decode pathを維持する。 |
| resize / display-ready生成 | `app/viewer_render.py`, `ViewerRenderTask.run`, `render_qimage`; `app/viewer_widget.py`, `_queue_render` | worker-sideでtarget-size `QImage`を生成する。standardはQt、non-standardは既存Pillow resamplingを使う。 |
| publish / QPixmap | `app/viewer_widget.py`, `_on_render_completed`, `_commit_pending_display`, `_commit_display` | accepted generationの完成`QImage`だけをGUI threadで一度`QPixmap.fromImage`し、single/spread全slot terminal後にatomic commitする。 |
| paint / 暗転回避 | `app/viewer_widget.py`, `paintEvent`; `app/viewer_window.py`, `_on_viewer_content_painted`, `_release_raster_prefetch_without_paint` | old complete frameを新target完成まで維持する。commit後のpaintを確認してからprefetchを解放する。hidden/minimized/error-only frameには250 ms fallbackがあり、Browser pauseの永久残留を防ぐ。 |
| current-first cold path | `app/viewer_window.py`, `_apply_pending_decode_demand`, `_preload_image_source(immediate_only=True)` | target visualが未表示ならcurrent visible unitだけをdecode/render/publish/paintまで進め、遠方prefetchを開始しない。 |
| prefetch | `app/viewer_window.py`, `_configured_prefetch_units`, `_start_raster_prefetch_pipeline`; `app/viewer_widget.py`, `prepared_display_is_ready`, `renderWorkFinished` | current後は直近forward、直近backward、残りの新方向、反対方向の順にdisplay unitを作る。各unitのprepared terminal signalで次へ進む。 |
| source cache | `app/image_cache.py`, `ImageCache`, `_enforce_limit`, `_oldest_evictable_index` | decoded/decoder-sized `QImage`をbyte計上する。currentとvisible spreadを保護し、wanted rankの遠いpageからevictする。 |
| display cache | `app/viewer_widget.py`, `_render_cache`, `_prepared_units`, `_enforce_render_cache_limit` | target-size `QPixmap`とdisplay-unit metadataを保持し、current、pending、直近forward/backward、active paced unitを保護する。 |
| combined memory | `app/viewer_window.py`, `_enforce_combined_cache_budget`; `ImageCache.cache_bytes`; `ViewerWidget.render_cache_bytes` | 設定値をsource cacheとpixmap cacheへ別々に満額付与せず、両者のresident byte合計へ1つの上限を適用する。巨大なcurrent spreadの保護によりbest-effortで超過し得る。 |
| spread protection | `PageModel.spread_at`; `ViewerWidget.prepare_display_units`, `_protected_prepared_units`; `ImageCache._protected_indexes` | current spread partnerをcurrent相当でdecodeし、prepared cacheはcurrent、直近両側、active paced unitをdisplay-unit単位で保護する。partial spreadをatomic表示しない。 |

### 3.1 `d19554a` production cold pathの実数

前回のpaced scheduler移植後も、通常JPEG cold missのcritical pathには次の
二段構造が残っていた。

```text
16 ms decode-demand timer
  -> _ImageLoadTask
     -> ZipExtFile read -> preallocated BytesIO
     -> BytesIO.getvalue
     -> Pillow header/EXIF parser
     -> QByteArray -> QBuffer -> QImageReader target decode
  -> queued ImageCache._on_loaded
     -> source cache insert -> pageLoaded
  -> ViewerWindow._on_cache_page_loaded
     -> combined budget -> _render_spread
  -> ViewerRenderTask（通常target一致なので実resize 0回）
  -> queued ViewerWidget._on_render_completed
     -> QPixmap.fromImage -> render cache insert
     -> atomic commit -> update -> paint
```

single通常経路の`focused_index`参照はinput handler内6回、cold完了時に
さらに3回である。ただし`PageModel.focused_index`のcurrent identity一致
fast pathが成立するため、この条件では`index_for_identity`、
`list_images`、entry全走査はすべて0回であり、大ZIPの線形要因ではない。

| 項目 | prepared hit | source hit / prepared miss | full cold (`d19554a`) |
| --- | ---: | ---: | ---: |
| 固定idle待ち | 0 ms | 約16 ms | 約16 ms |
| Viewer worker task | 0 | render 1 | decode 1＋render 1 |
| cross-thread queued callback | 0 | 1 | 2 |
| ZIP entry open / 展開 | 0 / 0 | 0 / 0 | 1 / 1 |
| entry payload materialization | 0 | 0 | entry `BytesIO`＋Pillow `BytesIO`＋Qt `QByteArray` |
| JPEG parser / pixel decoder | 0 / 0 | 0 / 0 | Pillow 1 / QImageReader 1 |
| post-decode scale | 0 | 通常0 | 通常0 |
| `QPixmap.fromImage` | 0 | 1 | 1 |
| source cache lookup / insert | 2 / 0 | 2 / 0 | 3 / 1 |
| render cache lookup / insert | 2 / 0 | 3 / 1 | 3 / 1 |
| combined budget enforcement | 0 | 1 | 2 |
| slider / status更新 | 1 / 1 | 1 / 1 | 2 / 2 |
| `_sync_actions` / QAction setter | 2 / 約80 | 2 / 約80 | 3 / 約120 |
| nominal fit layout / DPR参照 | 3 / 2 | 6 / 6 | 7 / 7 |
| 実windowでのDWM attribute設定 | 2 | 2 | 2 |

`BytesIO.getvalue()`やQt implicit sharingがcopyを省略する場合はあるが、
source code上はchunk群からentry buffer、Pillow header buffer、Qt byte arrayの
最大3 payload相当の境界がある。加えて、通常portraitではresizeしない
`ViewerRenderTask`がworker切替と2つ目のqueued callbackだけを発生させていた。

### 3.2 production-only bottleneck

既存offscreen benchmarkはWindowをshowせず、BrowserWindow /
BrowserThumbnailProviderも接続しない。そのため次の実機処理を測っていなかった。

- `ViewerWindow._refresh_view`が毎ページ
  `FullscreenChromeController.reevaluate_visibility`を同期呼出しし、visible
  Windows windowでは`winId()`と`DwmSetWindowAttribute`を2回実行していた。
  fullscreen state、native handle、paletteが変わらないページ移動には不要である。
- cold currentで`begin_viewer_interactive`がBrowser thumbnail pending全件を
  lock内走査・cancelし、paint内の`contentPainted`から即resumeして30 ms後に
  Browser decodeを再開していた。次のpage decodeとCPU／memory bandwidthが
  競合し得る。
- benchmarkは`window.next_page()`を直接呼ぶため、wheel event、
  `nextRequested`、`next_page_or_scroll`、旧frame fit layout 1回を含まない。
- offscreen raster pixmapにはvisible backing store、Windows compositor、
  real screen DPR、native QPixmap upload、vsync presentationがない。

この差により、前回のscheduler変更はprefetch orderを改善しても、実機の
DWM call、Browser pause/resume cascade、16 ms gate、payload materialization、
二段worker、QPixmap uploadを残したままであり、体感差が小さかったと説明できる。

### 3.3 `ZipPlaCompatibleRasterPath`

2026-07-31の変更では、次の限定条件を満たす通常経路を新しいproduction
dispatcherとして採用した。

- `ZipImageSource`のZIP/CBZ
- JPEG entry
- single page
- `fit_window`または`fit_no_upscale`
- standard resampling、rotation 0、画像調整なし
- wide splitなし、magnifier機能OFF、page list設定OFF

呼び出し列は次のとおり。

```text
ViewerWindow._refresh_view
  -> strict eligibility / request ID / book generation
  -> ImageCache.suspend_raster_work（A→B切替時の1回だけ）
  -> ZipPlaCompatibleRasterPath.request
  -> _ZipPlaCompatibleRasterJob（同時最大1件）
     -> ZipImageSource.open_compatible_jpeg_at_most
        -> ZipFile.NameToInfo O(1)
        -> ZipExtFile 1回
        -> 1 MiB chunkをreserved QByteArrayへ格納
        -> seekable QBuffer
        -> QImageReader.size/transformation（C++側）
        -> setAutoTransform + setScaledSize + read
  -> queued GUI callback 1回
     -> stale source/epoch/request/layout照合
     -> QPixmap.fromImage 1回
     -> path-local QPixmap ring insert
     -> ViewerWidget.commit_display_ready_single
        -> QPixmap-only atomic swap -> update 1回
  -> paintEvent -> frame serial acknowledgement
```

この経路は`ImageCache` source cache、`CachedImage`、`ViewerRenderTask`、
render cache、prepared-unit cache、combined-cache enforcementを通らない。
QImageはworker resultから唯一のGUI callbackへownershipを渡し、
QPixmap化後に保持しない。current／numeric next／numeric previousの最大3枚だけを
QPixmap ringへ保持する。cache hitはworker 0、queued callback 0、
QPixmap生成0で同じartifactをcommitする。

direction reversalでは、新currentと同じactive jobだけrequest IDをadoptする。
それ以外のactive jobはcancelし、未開始なら`tryTake`する。controller自身は
先行queued jobを作らないため、旧方向queued jobは0件である。running
ZipExtFile readは1 MiB chunk境界でcancelされ、decoder内部から戻った古い結果も
QPixmap化前にrejectする。

直接移植・対応箇所:

| ZipPlaFork由来の処理 | 移植元 | NivisViewer側 |
| --- | --- | --- |
| persistent archive/index、entryをpage jobが一度だけ取得してdecoderへ渡す | `PackedImageLoader.cs:283-330`, `:1781-1917` | `app/image_source.py`, `_read_entry_qbytearray`, `ZipImageSource.open_compatible_jpeg_at_most` |
| entry→decode→display-readyを1 page jobで完結 | `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run` |
| current→next→previousを1件ずつdispatch | `ViewerForm.cs:5558-5605`, `SetBackgroundMode` / `priorityLevel` | `ZipPlaCompatibleRasterPath.request`, `_drive` |
| completion 1回でdisplay artifact公開 | `GenerarClasses.cs:266-287`, `ViewerForm.cs:5371-5468` | `ZipPlaCompatibleRasterPath._on_job_completed`, `ViewerWidget.commit_display_ready_single` |
| page単位の近傍artifact保持 | `PreFilteredImageArray`, `ResizedImageArray` | path-local最大3枚QPixmap ring。対応外機能は従来Aへfallback |

Qt/Python差として、ZipPlaForkのC#/native streamに最も近いPython製sequential
`QIODevice`はQt JPEG pluginから約1,985回/pageの`readData` callbackを発生
させ、高詳細JPEGのworker decodeが旧buffered 241.86 msに対して270.44 msに
なった。そのためproduction新Bはentryを一つのreserved `QByteArray`へ
materializeし、seekable `QBuffer`から`QImageReader`へ渡す。`BytesIO`、
`getvalue()`、Pillow header parserは通らず、Python decoder callbackもない。
ZipPlaForkは原寸source＋resized artifactを保持するのに対し、この限定pathは
display QPixmapだけを保持し、magnifier、非standard、page list等は従来
pipelineへ戻す。

付随するproduction変更:

- page refreshから不変なDWM attribute再適用を撤去した。
- compatible cache hitではBrowser laneをpauseしない。
- cold frame paint後のBrowser resumeに500 ms graceを設け、連続入力中の
  thumbnail全queue pause/resumeを避ける。
- compatible commitでslider/statusを各1回だけ更新し、page navigation時は
  history action 2件だけ更新する。full QAction同期とpage-list scanは通らない。
- active ZIP decode中の`ZipImageSource.close()`はUI threadでlock待ちせず、
  cancelを設定してreturnし、最後のrequest終了時にpersistent ZipFileを閉じる。

## 4. Historical legacy-pipeline comparison

次表は`ZipPlaCompatibleRasterPath`採用前の旧Aに対する判断であり、対象条件を
満たすZIP/JPEG production経路の最終判断ではない。旧Aがfallbackとして残る
理由と、前回paced dispatcherが実機体感を十分変えなかった差分を保存する。

| 項目 | ZipPlaForkの実装 | NivisViewerの実装 | どちらが優れているか | 理由 | 取り込み方針 |
| --- | --- | --- | --- | --- | --- |
| decode pipeline | entry readからdecode、filter、resize、display-ready生成までliteralな1 page job | decode taskとrender taskは別だが、同じ1-worker laneをpaced dispatcherで直列化 | miss時の単純さはZipPlaFork、standard JPEGのpixel量はNivisViewer | ZipPlaForkはstage間queueがない。Nivisはdecoder-scaled JPEGでfull rasterを避け、generation境界が強い | 構造移植 |
| scheduler / queue | `BackgroundMultiWorker` 1 thread、全page permutation、完了境界で再order | shared Viewer lane、1 display unitずつadmit、terminal後に次unit | NivisViewer（移植後） | literal jobは2つ残るが、遠方workをqueueへ先行登録しないため、current missを阻害しにくい | 構造移植 |
| priority制御 | currentの固定数値`M`-page band → 数値上のnext band → previous band → rest | current 800、spread/prepared 700以下、interactive 600、next 500、previous 400。decode/render共通domain | NivisViewer | currentとactual spread topologyを保護しつつ、renderがdecode priorityに負けない | 構造移植 |
| direction reversal handling | directionを追跡せず、新current基準で全order再計算 | directionを追跡し、queued decode/renderを昇格・降格し、paced planを作り直す | NivisViewer | 反転直後の直近両側を保護し、旧方向の未開始workを残しにくい | 部分移植（独自拡張を維持） |
| cache structure | source/displayを別配列で持つがpage indexとeviction lifecycleを共有 | source `QImage` cache、render `QPixmap` cache、prepared unit metadataの二層 | ZipPlaForkは単純、NivisViewerはQt機能要件に適合 | Nivisはmagnifier、resize、resampling再生成のためsource/display分離が必要だが複雑 | 維持 |
| eviction policy | display artifact bytesを基準に遠方からevictし、同page sourceも同時破棄 | sourceとpixmapを双方byte計上し、それぞれpriority-aware eviction | NivisViewerの計上精度、ZipPlaForkのlifecycle単純性 | Nivisは実resident artifactをより正確に上限へ反映する | 部分移植 |
| prefetch policy | 全page work setをcurrent近傍から1 jobずつ消化 | current paint後、1 display unitずつdecodeからprepared terminalまでpaced実行 | NivisViewer | 通過pageや複数unitの同時未完成を抑止し、設定した近傍だけに限定できる | 構造移植 |
| display apply timing | natural moveは現在bandがreadyならcold destinationへ最大1 step logical currentを進め、forced one-page/trackbarはさらに先へ進める。pixel完成とはtransaction化されない | requested targetは先へ進むがdisplayed/slider/statusはold complete frameを維持し、完成unitだけatomic commit | NivisViewer | ZipPlaForkはhalf-gray loading canvasとpaint時statusを使う。Nivisはrequested/displayedを分離し、stale completionを表示へ混入させない | Hybrid（atomic commit維持） |
| dark-frame avoidance | cold destinationへ1 step進んだ場合もreusable canvasをclearせずhalf-gray化する | old complete `QPixmap`を新unit完成まで無加工で保持 | NivisViewer | Qtで安全なatomic swapを行い、error terminalもpaint完了として扱う | 維持 |
| cold miss behavior | current pageのliteral 1 jobが完了してから次job | current visibleのみをpaced single laneでdecode→signal→render→commit→paintし、その後prefetch | 構造要件は同等、cancel/stale拒否はNivisViewer | 4096 x 6500 JPEGのforced coldでcurrent以外を先行decodeせず、53.336–60.200 msでpaintした | 構造移植 |
| memory use | full decoded sourceとresized artifact。`usedMemory`はdisplayのみ計上 | standard JPEGはdecoder-size source、source+pixmapをcombined byte計上 | NivisViewer | 大JPEGでfull source rasterを常駐させず、二重満額budgetも廃止 | 部分移植 |
| large ZIP behavior | entry streamを同じpage jobのdecoderへ渡す。optional archive-memory modeあり | entryをpreallocated `BytesIO`へ展開し、decoder-scale後に別render task | 既知の実機体感はZipPlaFork、offscreen構造検証はNivisViewerも合格 | Nivisはbuffer copyが残る一方、decoder-scaled JPEG、current-only cold start、通過job抑止が有効。実機の同一ZIP再確認は未実施 | 構造移植、entry stream直結は検討継続 |
| stale result rejection | wait-cancelとloader/array replacement | request ID、source/render generation、source identity | NivisViewer | 高速入力、resize、book replacementの古い結果を明示的に拒否できる | 維持 |
| spread protection | 通常は固定数値`M=2` bandをlevel 0に置くが、navigationのwide/cover分岐とworker bandは完全には同じtopologyでない | decode protection、prepared-unit protection、atomic multi-slot commit | NivisViewer | actual complete display-unit topologyをsource/render両層で共有し、partial spreadをcommitしない | 維持 |
| QPixmap / paint | GDI `VirtualBitmapEx`とreusable bitmap canvas | worker `QImage`、GUI-thread one-time `QPixmap.fromImage`、cached paint | NivisViewer | QtのGUI resource境界を守りつつnavigation時変換を避ける | 維持 |
| Browser contention | Viewer比較対象内にNivis相当のapplication-wide lane制御なし | current cold中はBrowser laneをpause、paint/fallbackでrelease | NivisViewer | Viewerを他画面のdecode競合から保護する | 維持 |

### 4.1 Final comparison for eligible ZIP/JPEG

| 項目 | ZipPlaForkの実装 | NivisViewerの実装 | どちらが優れているか | 理由 | 取り込み方針（維持 / 部分移植 / 構造移植 / 全置換） |
| --- | --- | --- | --- | --- | --- |
| decode pipeline | persistent entry streamからnative decoderへ渡し、同じpage jobでorientation、resize、display bitmapまで作る | 新Bはentryを一つのreserved `QByteArray`へ読み、seekable `QBuffer` / `QImageReader`でdecoder scaleし、同じjobでdisplay-ready `QImage`まで作る | native streamはZipPlaFork、Qt/Python上の実測は新B構成 | Python sequential streamは約1,985 callback/pageで旧Aより遅かった。QByteArray版は1 materializationを許容してcallbackをC++内へ戻す | 構造移植 |
| scheduler / queue | page worker 1、completion 1、完了境界でpriority orderを再評価 | `_ZipPlaCompatibleRasterJob`最大1、completion 1、先行queueを作らない | 同等、新Bはstale境界が強い | cold currentは旧A 2 task / 2 callback、新B 1 / 1 | 全置換 |
| priority制御 | currentの固定数値band → numeric next band → previous band | current完成・matching paint後に移動方向側 → 反対側 | 新B | upstreamの固定数値bandよりactual display-unit topologyを正確に保護する | 構造移植 |
| direction reversal handling | 実行中1件の終了後、新current中心にwork orderを再構築 | 未開始jobを`tryTake`、running jobへcancel、request/source/layout serialで結果棄却 | 新B | 反転coldは旧A 3 task / 3 callback、新B 2 / 2。stale commit 0 | 構造移植 |
| cache structure | original bitmap配列とresized bitmap配列をpage lifecycleで管理 | eligible pathは最大3 display-ready QPixmap ringだけ。source/prepared/render cacheを通らない | 新B | 常設logical cache 121.46→30.36 MiB、7→3 page | 全置換 |
| eviction policy | current周辺を保護し、遠方pageのsource/displayをdispose | current、next、previous以外をpruneし、source/epoch/layout/DPR key違いを残さない | 新B | 古い仕様entryと多層cache間の寿命差を除去 | 全置換 |
| prefetch policy | current完成後、浅い近傍をsingle workerで処理 | matching `framePainted`後だけ最大2近傍。rapid inputは6 ms idle graceで最終currentへcoalesce | 新B | rapid-finalのcommit前通過page decode 0。paint後page 19 / 17は別phase | 構造移植 |
| display apply timing | 完成bitmapだけをUI completionで差し替える | GUI callbackでQPixmapを1回作り、`commit_display_ready_single`でatomic swap | 同等、新Bはserial確認が強い | 未完成QImageやplaceholderをViewerへ公開しない | 構造移植 |
| dark-frame avoidance | reusable canvasの旧frameを残しloading maskを描く | 完成済み旧QPixmapを無加工保持し、新frame完成後だけswap | 新B（Qt上） | blocking probe中`clear()` 0、commit 0、旧frame保持 | 構造移植 |
| cold miss behavior | entry open→decode→resize→displayを1 jobで完了 | 1 entry、1 materialization、1 QImage、1 QPixmap、1 task、1 callback、1 paint | 新B | fresh-process順送り255.481 ms、反転268.689 ms、往復中央値262.848 ms、rapid270.425 msで旧Aより2.47–8.21%短い | 全置換 |
| memory use | originalとresizedを保持する | task-local compressed QByteArray/QImage、常設最大3 QPixmap | 新B | viewer working-set delta 195.63→65.02 MiB（-66.76%） | 全置換 |
| large ZIP behavior | archive readerとentry indexをbook中保持、entryごとにstreamを開く | persistent `ZipFile`とO(1) indexを保持し、対象entryだけ32 x 1 MiB read | 同等のindex、pipelineは新B | 682,441,432-byte、日本語名21 entryの同一SHA-256 ZIPで完走 | 構造移植 |

## 5. Fixed-revision port map

以下はすべてrevision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、
AGPL-3.0-or-later由来である。

| Upstream file / class / method | 移植した処理 | NivisViewer destination | 移植境界 |
| --- | --- | --- | --- |
| `ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage.ThreadCount = 1`; `ViewerForm.cs:3177`, `bmwLoadEachPage_DoWork` | memory-bandwidth-heavyなViewer page処理を1 execution laneに限定 | `app/image_work_coordinator.py`, `ImageWorkCoordinator`; `app/image_cache.py`, `_start_task`; `app/viewer_widget.py`, `_start_render_task`; `app/viewer_window.py`, `_build_ui` | 構造移植。C# worker実装は未コピー。 |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork`; `:5371`, completion; `:5451`, `SetNewResizedImage` | 数値priority bandから選んだ1 pageをdecodeからdisplay-ready publishまで完了し、最新orderの次pageへ進む | `app/viewer_window.py`, `_apply_pending_decode_demand`, `_start_raster_prefetch_pipeline`, `_advance_raster_prefetch_pipeline`, `_on_viewer_content_painted`; `app/viewer_widget.py`, `prepared_display_is_ready`, `renderWorkFinished` | 構造移植。Nivisはliteral 1 QRunnableではなく、decode task → GUI signal → render taskのpaced single lane。 |
| `ViewerForm.cs:5558`, `SetBackgroundMode`; `GenerarClasses.cs:247`, `SetWorksOrder`; `:266`, completion reorder | 新currentを中心に未開始workを再優先付け | `app/viewer_window.py`, `_refresh_view`, `_configured_prefetch_units`, `_schedule_prepared_display_prefetch`; `app/image_cache.py`, `preload_around`, `ensure_loaded`; `app/viewer_widget.py`, `prepare_display_units`, `_queue_render` | 構造移植。Nivisのtravel-direction追跡、両側直近保護、16 ms coalescingは独自拡張。 |
| `ViewerForm.cs:5451`, `SetNewResizedImage`; `:5471`, `ReduceUsingMemory` | 完成artifactだけをpublishし、遠方pageをmemory policyで破棄 | `app/viewer_widget.py`, `_on_render_completed`, `_commit_pending_display`, `_commit_display`; `app/image_cache.py`, `_enforce_limit`, `retain_visible_only`; `app/viewer_window.py`, `_clear_raster_prefetch_pipeline`, `_enforce_combined_cache_budget` | algorithm/structure移植。upstreamはdisplay bytesだけを計上しsourceを同時evict、Nivisはsource+pixmap双方を計上し、active unitの一時source保護をterminal時に解除する。 |
| `ViewerForm.cs:6438`, `showCurrentPage`; `:6768`, `pbView_Paint`; `:6990`, `pbView_PaintToCanvas` | 完成frameのatomicな表示とblank frame回避 | `app/viewer_widget.py`, `_commit_display`, `paintEvent`; `app/viewer_window.py`, `_on_viewer_content_painted` | 処理構造移植。GDI canvas、`LockBits`、`CopyMemory`は未コピー。 |
| `ViewerForm.cs:1903`, `NextPage`; `:1951`, `PreviousPage`; `:9279`, `movePageNatural` | 未完成表示単位へnormal currentを進めない | 現在は完全移植なし。`ViewerWidget`がold visual frameを維持する一方、`PageModel`はtargetへ進む | 差分として意図的に記録。stateとvisualを同時commitする場合は追加の構造移植候補。 |
| `PackedImageLoader.cs:1781`, `OpenImageStream`; `:1856`, `OpenInnerImageStream` | entry単位の読み出しownershipと同じpage jobのdecoderへの受け渡し | `app/image_source.py`, `ZipImageSource._read_entry_qbytearray`, `open_compatible_jpeg_at_most`; `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run` | 構造移植。C# native streamはPython callback costのため直訳せず、一つのQByteArray/QBufferへ適応した。 |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork`; `:5371-5468`, completion | eligible JPEGでentry取得からdisplay-ready artifactまでをliteralな1 jobと1 UI completionで処理 | `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run`, `_on_job_completed`; `app/viewer_widget.py`, `commit_display_ready_single` | class / processing structure移植。Qt GUI resource規則によりQPixmap化だけGUI threadで行う。 |
| `ViewerForm.cs:5558-5605`, `SetBackgroundMode`; `GenerarClasses.cs:247-287` | current完成後に近傍を一件ずつ選び、完了境界で最新orderへ戻る | `ZipPlaCompatibleRasterPath.request`, `release_prefetch`, `_drive`; `ViewerWindow._on_compatible_raster_frame_painted` | 構造移植。新Bは方向側優先、6 ms rapid coalescing、request/source/layout serialを追加。 |
| `ViewerForm.cs:2757-2771`, open; `:2882-2915`, reload; `GenerarClasses.cs`, `RunWorkerAsyncWithInterrupt` | 古いworkを新sourceへ混入させない | `app/image_cache.py`, `generation`, `_on_loaded`; `app/viewer_window.py`, request ID / `ViewerDisplayUnit`; `app/viewer_widget.py`, render generations | 目的を独立実装。Nivisのgeneration方式を維持。 |

## 6. Removed, replaced, and deliberately retained structures

今回、eligible ZIP/JPEG production pathでは次をさらに置換した。以下の
旧paced pipelineは非対応条件とfailure fallbackには残るが、新Bのcritical
pathには入らない。

- `_ImageLoadTask` → GUI load callback → `ViewerRenderTask` → GUI render
  callbackを、`_ZipPlaCompatibleRasterJob` → GUI frame callbackへ置換した。
- `BytesIO` → Pillow header parse → `QByteArray` handoffを、reserved
  `QByteArray` → seekable `QBuffer`へ置換した。
- source cache、prepared-unit cache、render cache、combined budget lookupを
  eligible current pathから撤去し、最大3件artifact ringへ置換した。
- current result callback直後にprefetchを開始する方式を、matching
  `framePainted`後に解放する方式へ置換した。
- 高速入力中の各cold requestを即時worker投入する方式を、古いworkをcancel後、
  6 ms idle graceで最新requestだけをsubmitする方式へ置換した。
- decode用poolとrender用poolを別々に同時進行させる経路を、共通の
  1-worker Viewer laneへ置換した。
- 全prefetch unitを先にqueueへ積む経路を、1 display unitだけadmitし、
  prepared terminal後に次unitを投入するpaced dispatcherへ置換した。
- priority上昇だけを許すqueue更新を、方向反転時のpriority降格にも対応する
  queue再構築へ置換した。
- source cacheとprepared pixmap cacheへ設定値を別々に満額付与する方式を、
  resident byte合計に対するcombined budgetへ置換した。
- cold currentと遠方prefetchを同時に開始する方式を、current commit/paint後に
  prefetchを解放する方式へ置換した。
- 最後にadmitした遠方sourceがpipeline完了後もprotectedに残る方式を、
  terminal時にcurrent visibleだけへsource保護を戻す方式へ置換した。

次はfallbackと非対応機能のため撤去していない。

- `ImageCache`のdecoded/decoder-sized `QImage` cache
- `ViewerWidget`のdisplay-ready `QPixmap` cacheとprepared-unit metadata
- decode taskとrender taskの型およびGUI signal境界
- request ID、source generation、render generation、source identity確認
- coordinatorを注入しないtest/fake用のlocal one-worker fallback pools
- Pillow/PySide6/PDFiumに適合した既存decode/render実装

eligible ZIP/JPEG新Bはliteralな1 page QRunnableと1 GUI completionへ
置換した。ただしC#／GDI+コードの逐語コピーではなく、archive/worker ownership、
work order、completion/publish順をPySide6へ構造移植したものである。
旧Aの二段taskはpage list、magnifier、rotation、non-standard、非JPEG、
decoder failureのfallbackとして残る。

## 7. Historical legacy-A ready/cold measurement status

次表はcompatible新B採用前のpaced single-lane、共通priority、combined
cache実装を対象とする旧A baselineである。
`QT_QPA_PLATFORM=offscreen`で、21 entry、4096 x 6500 JPEG、3840 x 2106
Viewer、日本語名を含む15,313,338 byteの一時ZIPを使った。JPEGは生成時間を
抑えるため最大8種類の画素内容をentry名を変えて再利用した。実アプリ、
native入力、外部アプリは起動していない。値は2026-07-31の
`scripts/benchmark_viewer_navigation.py` target-decode runである。

| Scenario | Handler median / max | Request-to-paint median / max | Worker / stale-work evidence | Memory / retained pages |
| --- | --- | --- | --- | --- |
| ready順送り | 0.292 / 0.505 ms | 5.082 / 6.147 ms | prepared hit、decodeなし | warm window |
| ready逆方向 | 0.261 / 0.329 ms | 5.044 / 6.025 ms | prepared hit、decodeなし | warm window |
| ready往復 | 0.232 / 0.259 ms | 5.100 / 5.441 ms | prepared hit、decodeなし | warm window |
| 順送りcold miss | 0.301 / 0.301 ms | 59.775 / 59.775 ms | page 11だけをdecode、1 atomic commit、旧frame保持 | forced eviction |
| 逆方向反転直後cold miss | 0.309 / 0.309 ms | 53.336 / 53.336 ms | 旧running page 11をcancelしtarget page 9へ移行、far page 12は未開始 | forced eviction |
| 往復cold miss（6 legs） | 0.391 / 0.411 ms | 53.787 / 54.416 ms | page 11 / 10だけを交互に6 decode、各leg 1 commit | targetを各legでevict |
| 高速連続入力停止後の最終page cold miss（8 inputs） | 0.088 / 0.299 ms、合計0.933 ms | final paint 60.200 ms | page 18だけをdecode、通過page 11–17は未開始、1 commit | final targetをforced cold |
| current周辺保護 / 通過page抑止 | N/A | N/A | page 11、12、13を保護。高速入力の通過pageはdecodeなし | 保護3 pageを保持 |
| old-direction queued/running job | N/A | N/A | running page 11はcancel観測点まで進むが、queued far page 12は開始せずpage 9を優先 | stale commitなし |
| 暗転 / empty frame | N/A | blocked中も旧frame | requested/slider 11でもdisplayed 10を保持、`clear()` 0回、blocked中commit 0回、完了時1回 | empty frameなし |
| process working set / peak | N/A | N/A | baseline 351.07 MiB、sampled max 497.11 MiB、delta 146.04 MiB | 終了時487.32 MiB、process peak 517.36 MiB。ZIP/JPEG生成も含む |
| source+pixmap combined bytes / 保持page数 | N/A | N/A | source 70.85 MiB + render 50.62 MiB = 121.47 MiB / 128 MiB | source 7 entries、render 12 entries、prepared 5 units、current周辺を保持 |

historical paced-dispatcher前のready値と比べ、ready paintは順送り
5.294→5.082 ms、逆方向5.510→5.044 ms、往復6.055→5.100 msへ短縮した。
warm windowから8入力でprepared frontierを越える`rapid_frontier`のfinal
paintは70.297→53.015 msへ17.282 ms、24.6%短縮した。
forced-cold 4種には同じ計測方法の変更前値がないため、上表を新しい回帰baseline
とし、異なる旧cold計測と直接比較しない。

offscreen結果は構造回帰の証拠であり、最終判断は同一実ZIPを用いた実機体感とする。

### 7.1 Final fresh-process A/B after compatible adoption

2026-07-31の最終runはA/B別fresh process、21 entry、4096 x 6500高詳細JPEG、
3840 x 2106 Viewer、682,441,432-byte ZIPで実行した。A/BのZIP SHA-256は
`7bf798a4eee4a895c56d6e2a3415aa1a6ab47e4228d41d0118d24a39124e35f1`
で一致する。

| Scenario | 旧A request→paint | 新B request→paint | B差 |
| --- | ---: | ---: | ---: |
| 完全cold初回 | 270.349 ms | 266.759 ms | -1.33% |
| ready順送り | 6.216 ms | 7.629 ms | +1.413 ms |
| ready逆方向 | 5.107 ms | 6.939 ms | +1.832 ms |
| ready往復 median | 5.394 ms | 6.312 ms | +0.918 ms |
| 順送りcold | 278.333 ms | 255.481 ms | -8.21% |
| 反転直後cold | 275.505 ms | 268.689 ms | -2.47% |
| 往復cold median / max | 277.500 / 279.248 ms | 262.848 / 278.292 ms | median -5.28% |
| rapid-final cold | 287.891 ms | 270.425 ms | -6.07% |

1 cold pageのcurrent-critical処理は、旧Aが1 entry / 1 `BytesIO` /
3 whole-payload API handoff / 1 QImage / 1 QPixmap / 2 task /
2 GUI callback / 1 paint、新Bが1 entry / 1 `QByteArray` /
0追加whole-payload handoff / 1 QImage / 1 QPixmap / 1 task /
1 GUI callback / 1 paintだった。順送りdecode終了→paintは8.462→5.823 ms。
新Bはpaint後に近傍を読むため、順送りの総計には別phaseの1 entry、
rapid-finalにはpage 19 / 17の2 entryが加わる。通過page 11–17の
最終commit前decodeは0である。

memory-pressureでは旧Aがbaseline 349.07 MiB、sampled max 544.70 MiB、
delta 195.63 MiB、logical cache 121.46 MiB / 7 page、新Bが
175.90 MiB、240.92 MiB、65.02 MiB、30.36 MiB / 3 pageだった。
process peakにはZIP/JPEG生成やQt allocator high-waterも含まれるため、
viewer baselineからのsampled deltaをA/B判断に使った。

単発ready hitの新Bはworker/QImage/QPixmap生成0、atomic commit 1、paint 1で
ある。ready往復では直前paint後prefetchのcancel完了callbackが3回だけcurrent
intervalへ重なったが、ZIP read、materialization、QImage/QPixmap生成は0だった。
0.918–1.832 msの差はdirect-frame request/serial検証、paint acknowledgement、
prefetch cancel bookkeepingの固定費で、coldの支配点ではない。offscreen
timingだけで実機体感を断定しない。

同じ行列でdecoder-sized JPEGを意図的に無効化した`--full-decode`診断も
完走した。これは標準JPEG表示経路ではなく、full rasterを必要とする形式・条件の
残存costを見る比較である。forced coldは順送り207.049 ms、反転208.696 ms、
往復中央値209.128 ms、高速入力後221.845 msだった。通過page抑止、旧方向cancel、
暗転回避はtarget-decodeと同じく合格した。128 MiB圧迫時は、完了した遠方unitの
一時source保護が残る不具合を修正した後、source 76.17 MiB + render 40.49 MiB =
116.66 MiB、decoded source 1件となった。一方、複数のfresh windowと生成ZIPを
同一processで反復する診断全体のworking-set deltaは1,712.74 MiB、peakは
5,084.01 MiBであり、full-raster pathの大きな残存riskを示す。

## 8. Historical offscreen measurements before the paced dispatcher

The measurements below were recorded during earlier prepared-cache and
decoder-sized-JPEG work. They predate at least part of the current shared
priority domain, one-display-unit paced raster dispatcher, and combined cache
budget. They are retained as historical baselines and must not be reported as
current after-change results. Section 7 contains the reproducible current run.

These measurements used generated in-memory data and offscreen Qt. They are
regression evidence, not a substitute for testing the packaged application on
the user's 4K display and storage device.

### Decoder-sized large-JPEG ZIP comparison

The earlier run used the production `ViewerWindow` through a predecessor of
`scripts/benchmark_viewer_navigation.py`: a temporary ZIP with 21 entries,
Japanese entry/path names, 4096 x 6500 JPEG pages, a 3840 x 2106 Viewer,
single-page mode, standard fit-window rendering, a 6-forward/4-backward plan,
and a 512 MiB setting then applied to the decoded-source cache. It did not
launch the real application or generate native input. The current script and
cache semantics have since changed.

Direct decode comparison for the same 4096 x 6500 JPEG payload:

- Pillow full decode: 48.726 ms median.
- Qt full decode: 76.699 ms median.
- Qt JPEG decoder-scaled decode to 1361 x 2160: 26.101 ms median.

Production-window results with decoder-sized JPEG enabled:

- forward: 0.329 ms median handler, 5.294 ms median request-to-paint;
- reverse: 0.282 ms median handler, 5.510 ms median request-to-paint;
- repeated forward/backward: 0.292 ms median handler, 6.055 ms median
  request-to-paint;
- eight uninterrupted requests crossing the prepared frontier: 1.466 ms total
  handler time and 70.297 ms from first request to final-page paint;
- each decoded preview source is about 10.12 MiB; ten were resident
  (101.21 MiB) at the final instantaneous cache snapshot.

The same run with decoder-sized JPEG disabled:

- forward sequence: 0.347 ms median handler, 59.983 ms median
  request-to-paint, 169.851 ms maximum;
- reverse sequence: 0.311 ms median handler, 5.467 ms median
  request-to-paint, 210.486 ms maximum;
- repeated forward/backward: 0.385 ms median handler, 21.128 ms median
  request-to-paint;
- eight uninterrupted frontier requests: 1.296 ms total handler time and
  230.075 ms to final-page paint;
- final decoded cache: six full sources totaling 457.03 MiB.

The 512 MiB full-decode run can retain only about six 4096 x 6500 RGB sources,
so it cannot keep the configured 11-page working set resident. This is itself
the observed working-set difference; the sequence timings must not be read as
an all-ready cache microbenchmark. Decoder-sized mode can retain the complete
planned window and avoids that raw-raster pressure. Before decode-demand
coalescing, the decoder-sized rapid-frontier result was 148.747 ms; starting
only the latest idle target reduced it to 70.297 ms.

- Source raster: 4096 x 6500 RGB888 (76.17 MiB)
- 4K-fit target: 1361 x 2160 (8.41 MiB output)
- Previous unconditional RGBA boundary:
  - 101.56 MiB intermediate raster
  - 319.404 ms high-quality render in the same comparison run
- Format-preserving RGB boundary:
  - 76.17 MiB intermediate raster
  - 202.855 ms high-quality render in the same comparison run
- Exact-target fast path: 0.039 ms, no Pillow conversion
- Current cold renders:
  - moire reduction: 136.059 ms
  - high quality: 205.385 ms
  - smooth: 123.854 ms
  - pixel: 64.886 ms
- GUI `QPixmap.fromImage` for the full source: 33.825 ms
- Repeated smooth 4K paint: 5.832 ms median in that run
- Preparing 11 configured high-quality display units:
  - 11 worker requests and 11 Pillow conversions
  - 1920.280 ms total
  - 123.36 MiB retained display-ready pixmaps
- Applying 17 already-ready forward/reverse targets:
  - 0.035 ms median, 0.055 ms p95
  - zero new workers and zero new Pillow conversions
  - one atomic commit per navigation
- A cold non-standard miss: 173.143 ms to atomic commit, one worker and one
  Pillow conversion
- Process working-set peak in the render benchmark: 426.95 MiB. The 20 logical
  pages shared one injected source raster, so this is intentionally not an
  estimate of a real 20-page decoded-source cache.

### Standard ready/cold comparison

An additional temporary benchmark created a ZIP containing 21 actual
4096 x 6500 JPEG entries (832,263 bytes each; 17,479,603-byte ZIP). Temporary
scripts, archives, and timing output were removed after the run.

- JPEG `Image.load()` decode, 20 samples:
  - 69.03 ms median
  - 74.44 ms p95
- Before the fix, with every source already decoded:
  - wheel handler: 15.89 ms median, 16.63 ms maximum
  - request to paint: 21.71 ms median, 23.11 ms maximum
  - full-source `QImage` to `QPixmap`: 21 conversions for 21 requests,
    approximately 15.78 ms each
- After the fix, with the configured 11-page working set ready:
  - `standard` wheel handler: 0.055 ms median, 0.218 ms maximum
  - `standard` request to paint: 0.961 ms median, 1.364 ms maximum
  - 32 prepared hits, zero misses, zero navigation-time workers, zero
    navigation-time `QImage` to `QPixmap` conversions
  - ten uninterrupted wheel handlers: 0.837 ms total, 3.214 ms to the
    coalesced final paint, ten state commits and one paint
  - `high_quality` wheel handler: 0.058 ms median, 0.192 ms maximum
  - `high_quality` request to paint: 0.910 ms median, 1.153 ms maximum
- Cold `standard` display-size miss with the source raster already decoded:
  - wheel handler: 0.140 ms median, 0.177 ms maximum
  - request to paint: 19.66 ms median, 74.08 ms maximum
  - zero navigation-time `QPixmap` conversions; one display worker was active
    while the previous frame remained visible
- Working set in the final ready-standard run:
  - 125.04 MiB after source conversion
  - 283.11 MiB with 11 display-ready pixmaps
  - each 1361 x 2160 32-bit pixmap accounts for about 11.21 MiB; the 11
    retained artifacts account for about 123.36 MiB before Qt allocator and
    widget overhead
- Wide-page split preparation at 4096 x 6500:
  - two GUI-thread half-image deep copies: 11.492 ms median
  - two shared `QImage` handles with worker-side crop: 0.0028 ms median
  - both split identities retain the source cache key; crop range remains part
    of the render key

A final rerun after the cache-eviction, priority, DPR, and lifecycle boundary
fixes used the same 21-page, 4096 x 6500 generated JPEG ZIP and an 11-page
prepared working set:

- `standard`:
  - wheel handler: 0.258 ms median / 0.569 ms maximum
  - request to paint: 5.162 ms median / 8.428 ms maximum
  - 32 ready hits, zero misses, zero navigation-time workers, and zero
    navigation-time QImage-to-QPixmap conversions
  - ten uninterrupted handlers: 0.953 ms total; 2.206 ms to the coalesced
    paint
- `high_quality`:
  - wheel handler: 0.170 ms median / 0.721 ms maximum
  - request to paint: 1.848 ms median / 7.124 ms maximum
  - 32 ready hits and zero navigation-time workers/conversions
  - ten uninterrupted handlers: 1.708 ms total; 8.146 ms to the coalesced
    paint
- process working set was approximately 125 MiB after one decoded source and
  284 MiB after the 11 display-ready artifacts.

The navigation portion intentionally reused one decoded `QImage` for all
logical page identities so it could isolate display-artifact costs. Its working
set is not an estimate for retaining 21 independently decoded source rasters.

These results isolate display preparation from archive decode. A true cold ZIP
request also includes the measured JPEG decode time and storage/archive
latency, but neither runs in the wheel handler or paint event.

### Earlier production-window navigation

This earlier offscreen measurement used the production `ViewerWindow`,
`ViewerWidget`, and `ViewerRenderTask` path at a 3840 x 2106 viewport with 21
raw-ready 4096 x 6500 RGB888 pages. Decode was intentionally excluded here so
input-handler, display preparation, and paint could be counted independently.

- One cold `standard` page:
  - 1.908 ms input handler in the latest confirmation
  - 56.893 ms request to paint, including the 16 ms input-idle boundary
  - one demand resize, one target-size `QPixmap`, one commit, and one paint
- Ten ready pages forward:
  - 1.380 ms median / 1.948 ms maximum input handler
  - 7.463 ms median / 8.868 ms maximum request to paint
  - zero demand workers, resizes, and `QImage` to `QPixmap` conversions
- Four ready pages in the reverse direction:
  - 1.287 ms median / 2.238 ms maximum input handler
  - 5.675 ms median request to paint
  - zero demand workers and conversions
- Six already-viewed forward/backward operations:
  - 1.414 ms median / 3.060 ms maximum input handler
  - 9.210 ms median request to paint
  - zero workers and conversions
- Six uninterrupted requests crossing the ready frontier:
  - 0.391 ms median / 0.923 ms maximum input handler in the latest
    confirmation
  - 2.753 ms total handler time
  - one demand worker and one target-size `QPixmap`
  - intermediate cold pages 10 and 11 produced no worker, commit, or paint;
    only the latest page 12 was materialized
  - 55.354 ms from the first request to the final paint

The rapid test was repeated with 21 distinct physical QImage buffers
(approximately 1599.61 MiB of raw raster data): handler median was 0.554 ms,
maximum was 1.161 ms, total was 3.888 ms, and only the latest cold target
created a worker and pixmap. Rolling prefetch began only after that target had
committed and painted. Shutdown left zero active render threads, tasks, pending
demands, or active demand/prepared timers.

## 9. Additional whole-application improvements

- Browser thumbnail memory hits now pass separate implicitly shared `QImage`
  handles through the provider and model. Qt copy-on-write still isolates
  later mutation, while removing two GUI-thread deep pixel copies per hit.
  A 1024 x 1024 RGBA comparison measured 0.5210 ms per deep copy versus
  0.0045 ms per shared-handle construction.
- Direct, non-recursive folder books use `os.scandir()` metadata and filter
  extensions before asking whether an entry is a file. Recursive listing,
  symbolic-link behavior, natural sorting, and disappearing-entry handling
  keep their previous contracts.
- ZIP entry reads remain seekable for Pillow but now write directly into one
  size-preallocated `BytesIO`, checking request-local cancellation between
  1 MiB reads. For a 48 MiB incompressible payload, six-run medians were
  31.611 ms / 50.002 MiB peak Python allocation for stored ZIP and
  66.984 ms / 52.033 MiB for deflate. The previous chunk-list plus final join
  measured 32.635 ms / 96.008 MiB and 62.458 ms / 96.008 MiB respectively.
  `tracemalloc` does not include native zlib or OS allocations.
- In a deterministic fake-reader cancellation comparison, the new ZIP path
  reported `process_cancelled` in 4.235 ms median (4.327 ms p95), versus
  144.261 ms completion wait for an equivalent loop without cancellation
  observation points.

## 10. Confirmed follow-up work

The following issues were measured or statically confirmed but deliberately
left outside this page-navigation change:

- Non-`standard` manual zoom still asks Pillow for the full zoomed output.
  A 4096 x 6500 page at 8x is about 6.35 GiB at 4 bytes per pixel before DPR;
  DPR 2 can make the requested area about 25.4 GiB. A safe solution needs
  viewport cropping/tiling or a documented quality-preserving cap.
- The combined cache budget now counts source `QImage` and prepared `QPixmap`
  under one configured total. Current/visible spread protection is intentionally
  stronger than the byte bound, so one unusually large current display unit can
  exceed the setting.
- NivisViewer still changes logical page state before a cold visual unit is
  ready. The old complete frame prevents darkness, but slider/status and image
  can temporarily refer to different pages. Matching ZipPlaFork exactly would
  require pending navigation state and an atomic model+visual commit.
- Eligible ZIP/JPEG新Bはcomplete entryを一つのreserved `QByteArray`へ
  materializeする。Python sequential `QIODevice`直結はfull payloadを
  避けられるが、約1,985 callback/pageにより高詳細decodeが遅くなったため
  production採用しなかった。将来native extension、Qt private archive device、
  または別native decoderを検討する場合はライセンスと実測が必要である。
- Eligible新Bはliteral 1 runnableになったが、Qt規則上QPixmap生成とatomic
  commitには1 queued GUI callbackが必要である。順送りのdecode終了→paintは
  5.823 msで、ここにはQPixmap、commit、offscreen paintが含まれる。
- ready hitは旧Aより0.918–1.832 ms遅い。worker/QImage/QPixmap生成は0である。
  単発hitではqueued worker callbackも0だが、ready往復では直前paint後prefetchの
  cancel完了callbackが3回だけ重なった。差はdirect-frame request/serial
  validation、paint acknowledgement、prefetch cancel bookkeepingの固定費で
  ある。cold改善と引き換えに許容したが、実機入力で悪化が見える場合はhit専用の
  synchronous commitとready往復時のprefetch抑制をさらに短縮する候補が残る。
- If a source-sized standard render is still running when zoom changes again,
  the stale generation is rejected correctly but the latest generation may
  repeat that one preparation. Completed source-sized artifacts are reused.
- When the page-list dock is visible, its legacy thumbnail path can still
  perform a large-image Smooth scale on the GUI thread. It is independent of
  the normal hidden page-list navigation path and should be moved to a worker
  in a separate change.
- 新Bのlogical artifactは30.36 MiB / 3 pageだが、fresh-process
  working-set deltaは65.02 MiBだった。task-local compressed QByteArray、
  QImage、Qt allocations、decoder bufferは常設artifact accounting外なので、
  設定値はhard process-memory limitではない。

## 11. Viewer-wide subsystem comparison and replacement decision

This section supersedes the earlier assumption that the narrow
`ZipPlaCompatibleRasterPath` could become the final Viewer architecture by
being extended one option at a time.  The comparison was performed before the
next implementation step and covers the complete path from book ownership to
paint and shutdown.

The ZipPlaFork reference remains commit
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` under AGPL-3.0-or-later.  The main
reference points are `PackedImageLoader.cs` (`PackedImageLoader` construction,
entry table, `OpenImageStream`, `Dispose`), `GenerarClasses.cs`
(`BackgroundMultiWorker.SetWorksOrder`, completion and interrupt handling), and
`ViewerForm.cs` (`bmwLoadEachPage_DoWork`, `priorityLevel`,
`SetNewResizedImage`, `ReduceUsingMemory`, `showCurrentPage`, and
`pbView_Paint`).  The attribution and license notice in sections 1 and 5 and
`THIRD_PARTY_NOTICES.md` apply to the structural port selected below.

### 11.1 Ten-subsystem comparison

| Subsystem | ZipPlaFork design | Current NivisViewer design | NivisViewer problem | Benefit of the ZipPlaFork structure | Port difficulty | Effect on NivisViewer-specific features | Decision |
|---|---|---|---|---|---|---|---|
| 1. Archive management | `PackedImageLoader` opens the book once, retains one archive/entry table, opens only the requested entry stream, and disposes the archive after the viewer worker has stopped. | `BookSession` owns an `ImageSource`; `ZipImageSource` already retains one `ZipFile`, cached entry names, request-local cancellation, deferred close, and retired-source lifetime.  It also exposes legacy `BytesIO`, compatible `QByteArray`, and experimental sequential-device decode entry points. | The persistent ZIP lifetime is sound, but ownership and cancellation knowledge are split between `BookSession`, `ImageCache`, `ZipPlaCompatibleRasterPath`, and the source.  Three decode-facing APIs permit three page lifecycles. | One book owner and one entry-open contract make close, cancellation, and reuse explicit and prevent two viewer consumers from decoding the same entry. | Medium-high | Folder, external RAR/7z, PDF, asynchronous open, Windows sharing, and Japanese names require adapters. | **Structural port.** Retain the persistent-handle/deferred-close behavior, but put it behind the new book runtime/page-source contract. |
| 2. Page state | `currentPage` advances only through navigation rules that consult the ready/known-size frontier; the accepted current page, trackbar, and shown image are updated by one Viewer form. | Logical current is `PageModel`; requested state is `ViewerWindow._active_request_id` plus `ViewerDisplayUnit`; displayed state is `ViewerWidget._spread/_images`; the compatible path has another current request/frame serial. | Logical page and related UI can advance while the old visual frame remains.  Legacy and compatible paths commit slider/status at different times, and rapid input has no single authoritative requested/displayed state. | A single presentation transaction prevents logical/visual disagreement and gives reversal and stale-result rejection one owner. | High | Spread, lazy wide-page discovery, history, bookmarks, slideshow, slider and reading progress must keep their semantics. | **Full replacement** by an explicit requested/displayed presentation state after the page runtime cutover. |
| 3. Image-load pipeline | One `bmwLoadEachPage_DoWork` page job performs entry open, decode, EXIF/filter work, target resize, and creation of the display artifact before one completion is published. | Legacy path is `_ImageLoadTask` to source `QImage`, then a second `ViewerRenderTask` to resize/rotate/filter, then prepared/pixmap publication.  The compatible path is a second end-to-end pipeline limited to ZIP/JPEG/single/standard/fit/no rotation/no adjustments/no page list/no magnifier. | Feature options switch the whole pipeline.  Source and display work have separate callbacks, schedulers, caches, failure rules, and generations; the narrow fast path is a bolt-on rather than the Viewer. | A page/display-slot job has one critical path and one result.  Decoder fallback stays inside the job instead of returning to another Viewer architecture. | High | All raster formats, spread slots, split/crop, rotation, resampling, image adjustments, manual zoom, DPI and magnifier must be expressible in a render specification. | **Full replacement** for ZIP books by one runtime-owned entry-to-display-ready job. |
| 4. Asynchronous processing | `BackgroundMultiWorker` uses one Viewer worker. `SetWorksOrder` replaces the page permutation; after a completion the worker chooses the first unstarted item in the latest order.  Book replacement uses interrupt/wait cancellation. | The production coordinator normally supplies one Viewer lane, but `ViewerWindow`, `ImageCache`, `ViewerWidget`, and the compatible path each own queue or pending-job state.  Without the coordinator, decode and scale can use separate one-thread pools. | One physical lane does not make one scheduler: obsolete running render work cannot be preempted, generations are duplicated, and shutdown must drain several owners. | One active page job and a replaceable work order bound obsolete work to the currently running decoder and make current/next/previous ordering observable. | High | Browser must retain a separate lane; PDF priorities and injected test pools need adapters. | **Structural port / Viewer scheduler full replacement.** Keep Nivis generation validation and Qt thread-affinity rules. |
| 5. Prefetch | `priorityLevel` orders the current fixed numerical `M`-page band first, numeric next band second, previous band third, then remaining pages; a changed current recomputes the unstarted order. Memory admission can stop/rework the frontier. | Window raster plan, `ImageCache` wanted/protected/ranks, Widget prepared-unit requests, compatible-path neighbors, and multiple idle/paint timers all plan related work. | Four planners must agree. Direction reversal can leave a running legacy decode or render, and cache admission/prefetch release are owned by different objects. | A replaceable work order provides one actual current unit -> forward neighbor -> reverse neighbor policy and stops remote work when the page-artifact budget is full. | High | Spread partner comes from actual topology; configurable direction and PDF rolling behavior remain policy inputs. | **Full replacement** inside the new runtime. |
| 6. Cache and memory | Page-indexed source information and `VirtualBitmapEx` display artifacts share a lifecycle and one memory reduction order. `ReduceUsingMemory` evicts farthest pages using the current work order. | `ImageCache` stores source `QImage`; `ViewerWidget` stores render `QPixmap`, last-rendered aliases, and prepared-unit state; the compatible path stores a separate three-pixmap ring. A Window loop tries to rebalance independent budgets. | The same page can have several unrelated lifetimes and ledgers. Entering/leaving the compatible mode discards one cache and warms the other. The compatible three-page cap is not a byte budget. | A page record with source/display components, one byte ledger, and current/spread/next/previous protection makes eviction and reversal reuse coherent. | High | Magnifier may require a full source artifact; one unusually large visible spread may intentionally exceed the budget. | **Full replacement** by a runtime-owned page artifact cache; preserve explicit visible-unit protection. |
| 7. Display update | `SetNewResizedImage` publishes a completed result and `showCurrentPage` paints it. During a cold load ZipPlaFork grays the preceding canvas rather than clearing it. | Legacy `ViewerWidget` waits for all spread slots to become terminal and atomically swaps them; direct mode also commits one completed pixmap while the old frame remains. Paint acknowledgement releases prefetch/browser work. | Atomic old-frame retention is good, but direct-versus-legacy guards permeate the Widget, while slider/status/page-list selection are committed separately. | One `commit_frame` boundary can keep NivisViewer's superior old-frame retention while removing path-specific Widget modes and synchronizing UI state. | Medium | Pan, zoom, magnifier interaction, gestures, fullscreen chrome and error placeholders remain surface behavior. | **Structural port**, not a literal visual copy. Retain Nivis atomic old-frame behavior and replace the two publication modes with one frame contract. |
| 8. Page list and thumbnails | The Viewer launches catalog viewing separately rather than sharing its in-window loader/cache. `CatalogForm` owns a distinct loader and a globally one-at-a-time thumbnail task; `ThumbViewer.PaintPart` requests only visible rows plus a small margin and clears images outside it. | The Viewer eagerly builds all `QListWidgetItem` rows. It consumes Viewer `ImageCache` results, linearly finds the row, then smooth-scales a large `QImage` and creates `QIcon` on the GUI thread. Merely showing the dock disables the compatible path. Browser thumbnails have a better dedicated lane/cache. | Thumbnail work competes with or changes the Viewer pipeline and can block the GUI. There is no visible-row scheduler for the Viewer page list. | A separate visible-range thumbnail session cannot invalidate or select the main Viewer runtime and can be paused while a cold current page is outstanding. | Medium | Filtering, current selection, bookmark decoration, thumbnail size/DPI, and click navigation remain. | **Full replacement** of the Viewer page-list thumbnail subsystem; retain Browser's independent visible-range/disk-cache design. |
| 9. Display features | Single/spread, binding direction, divided pages, filters, magnifier, rotation and fullscreen are integrated around the same loader/work-order/page arrays rather than choosing a different fast pipeline. | NivisViewer has richer explicit `PageModel`, layout, fit/manual zoom, split-range, resampling, EXIF, magnifier and DPI handling, but almost every feature makes the compatible pipeline ineligible. | Feature support and performance architecture are coupled: turning on a feature destroys the direct cache and returns to the legacy two-stage path. | Treating the feature set as request/render specifications keeps one execution structure while preserving Nivis behavior. | High | This is almost the complete visible feature surface and needs contract-level regression coverage. | **Keep NivisViewer feature policy; structurally replace its execution path.** |
| 10. Errors and shutdown | Worker exceptions become terminal error images/results; book replacement interrupts and waits for the current worker, clears page arrays, and disposes the old loader. | Legacy errors become terminal `CachedImage` records and can atomically join a spread; compatible failures maintain a Window failed-key set and retry the legacy pipeline. BookSession retired sources, direct-path wait, Widget render wait, cache wait, and coordinator shutdown are separate. | Failure can silently change architecture, and source retirement cannot ask one owner whether all consumers have stopped. Shutdown is special-cased for every pipeline. | One result type (`ready`, `error`, `cancelled`, `stale`) and one runtime shutdown make error behavior and archive lifetime deterministic. | Medium-high | Broken/unsupported entries, book switching, old-frame retention, asynchronous open and application shutdown must remain robust. | **Structural port.** Retain deferred close, terminal error frames and generation rejection; remove failure-triggered Viewer fallback. |

### 11.2 Main NivisViewer design problems

The dominant issue is ownership fragmentation, not a missing decoder tweak:

1. A normal raster page can be represented simultaneously by an
   `ImageCache` source record, a `ViewerWidget` render/prepared record, and a
   compatible-path artifact.  Each has a separate eviction and generation.
2. `ViewerWindow` is the effective scheduler but does not own a page job.  It
   coordinates several timers and queues implemented by other objects.
3. A UI feature is currently allowed to choose between two Viewer engines.
   Page-list visibility, spread, rotation, magnifier, adjustment, resampling,
   or fit mode can discard the compatible cache and restart the legacy path.
4. Logical, requested, completed, displayed, and painted page identities do
   not belong to one state machine.
5. Page-list thumbnail creation uses Viewer decode results and GUI-thread
   large-image scaling instead of a separate visible-range pipeline.

The current diff confirms that the compatible path was added beside the old
architecture: the substantial additions to `viewer_window.py`,
`viewer_widget.py`, `image_source.py`, and `image_cache.py`, plus the new
`zippla_compatible_raster_path.py`, did not remove the legacy raster loader,
render scheduler, prepared cache, or their prefetch planners.  Extending its
eligibility one condition at a time would preserve this root problem.

### 11.3 Structures to retain

The replacement must preserve these NivisViewer strengths rather than copying
ZipPlaFork literally:

- Qt GUI-thread-only `QPixmap` creation and request/source generation checks;
- `PageModel` spread, LTR/RTL, cover, wide-page, and split policy, initially as
  a pure topology/layout input;
- the completed-old-frame retention and all-slots-terminal atomic frame swap;
- fit/manual zoom, resampling, rotation, EXIF, image adjustments, magnifier,
  PDF render specification, and device-pixel-ratio behavior;
- asynchronous book open, retired-source close, Windows path/share behavior,
  external archive adapters, and Japanese filenames;
- Browser's dedicated thumbnail provider, memory/disk cache, and visible-row
  scheduling, while making Viewer cold work able to suppress active Browser
  work at a clear boundary.

### 11.4 Replacement roadmap

The work is grouped into four subsystem-sized changes, in performance-impact
order.  These are replacement boundaries, not a list of new helpers to attach
to the existing paths.

1. **`ZipRasterBookRuntime` full cutover.** One runtime owns a ZIP book's page
   request, one-active-job work order, entry-to-display-ready pipeline,
   current/next/previous page artifact cache, eviction, cancellation and
   shutdown. Every ZIP display request uses it; page-list visibility, spread,
   rotation, filter, fit or magnifier must not select the legacy pipeline.
2. **`ViewerPresentationState` full replacement.** Make requested and displayed
   display units explicit and commit the accepted frame, slider, status,
   page-list selection, history/progress policy and painted acknowledgement as
   one transaction.
3. **`ViewerPageListRuntime` full separation.** Replace eager Viewer-cache
   thumbnails with a visible-row worker/cache that never changes the main
   Viewer runtime and never scales large source images on the GUI thread.
4. **`BookPageSource` unification and legacy raster removal.** Split source
   ownership/entry access from decode/render, migrate folder and external
   archives to the raster runtime, adapt PDF to the common presentation
   contract, then delete the legacy `ImageCache`/prepared pipeline.

### 11.5 First structural port selected

The first implementation target is **`ZipRasterBookRuntime`**, not an
archive-only or cache-only change. The existing `ZipImageSource` already gets
the most important persistent archive/index behavior right; replacing only it
would leave the two-stage pipeline and mode-dependent engine switch intact.

The cutover contract is:

```text
one ZIP book
  -> one runtime
  -> latest display-unit request + replaceable work order
  -> at most one active page job
       entry read -> decode -> EXIF/filter -> split/rotate/resize
       -> display-ready QImage result
  -> GUI-thread QPixmap creation
  -> one complete frame commit; preceding frame remains visible until then
  -> paint acknowledgement permits shallow next/previous work
```

The following old ZIP structures become removal targets once the cutover is
complete: standalone `ZipPlaCompatibleRasterPath`,
`ImageCache.suspend_raster_work`, compatible eligibility/timer/failure-key
state in `ViewerWindow`, ZIP participation in `_ImageLoadTask` and
`ViewerRenderTask`, ZIP prepared-display/paced-prefetch state, and
`ViewerWidget` direct-versus-legacy mode guards.  Folder, PDF, and external
archive handling may temporarily remain behind an explicitly named legacy
runtime boundary; there must be no per-feature fallback within a ZIP book.

### 11.6 Implemented first cutover

The first replacement unit is now implemented as a **book-level production
cutover**, not another eligibility path:

| ZipPlaFork source | Ported processing structure | NivisViewer destination |
|---|---|---|
| `PackedImageLoader.cs:283`, constructor; `:1197`, ZIP entry table; `:1781` / `:1856`, entry stream ownership; `:2601`, disposal | One runtime is created for one already-indexed persistent `ZipImageSource` and retired before its book source is closed. | `app/book_session.py`, `BookSession._replace_viewer_runtime`, `_retire_viewer_runtime`, `_release_retired_viewer_runtime`; `app/zip_raster_book_runtime.py`, `ZipRasterBookRuntime` |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | One display-unit runnable performs all logical pages in the unit: entry read, decode, EXIF-aware source creation, adjustments, wide split, rotation, filter/resize, and display-ready `QImage`. | `app/zip_raster_book_runtime.py`, `_ZipRasterUnitJob.run`, `_render_unit`, `_decode_page`, `_split_ranges`; existing `viewer_render.render_qimage` is the Qt/Pillow render adapter used inside that same job. |
| `GenerarClasses.cs:247-339`, `SetWorksOrder` / completion selection; `ViewerForm.cs:5371-5415`, completion; `:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | Fixed numerical current/next/previous bands define the unstarted order. At most one job is active; navigation does not cancel it, its completed bitmap remains cacheable, and the first unfinished page from the newest order starts at the completion boundary without waiting for paint. | `ZipRasterBookRuntime.request`, `_adopt_request`, `_active_job_is_artifact_compatible`, `_drive`, `_submit`, `_on_job_completed`; `ViewerWindow._zip_runtime_request` |
| `ViewerForm.cs:5371`, worker completion; `:5451`, `SetNewResizedImage`; `:6438`, `showCurrentPage` | The sole queued GUI completion creates `QPixmap` objects and publishes only a terminal complete single/spread/split frame. The preceding complete frame remains owned by the Widget until that transaction. | `ZipRasterBookRuntime._on_job_completed`; `ViewerWidget.commit_display_ready_frame`; `ViewerWindow._on_zip_runtime_frame_ready` |
| `ViewerForm.cs:5471`, `ReduceUsingMemory` | Source and display components belong to one cached frame/page lifecycle. One work-order-aware byte ledger evicts the farthest non-current display unit. | `app/zip_raster_book_runtime.py`, `_ZipRasterFrameStore`, `set_retention_order`, `can_admit_prefetch`, `put`, `_prune`, `_retention_rank`; `ZipRasterBookRuntime.set_cache_limits`, `request`, `_drive` |

Production ownership is now:

```text
BookSession
  -> one ZipRasterBookRuntime for one ZipImageSource
ViewerWindow._refresh_view
  -> ZipRasterRequest for every ZIP mode and feature
ZipRasterBookRuntime
  -> one active end-to-end display-unit job
  -> one page-record cache/work order/shutdown owner
ViewerWidget
  -> one complete-frame commit and paint acknowledgement
```

The following old production structures were removed from `ViewerWindow`:

- `_compatible_raster_request` and its ZIP/JPEG/single/fit/rotation/filter/
  page-list/magnifier eligibility matrix;
- compatible failure keys and failure-triggered fallback to the legacy path;
- compatible path ownership, signals, page-list visibility deactivation, and
  rotation/magnifier fallback/replay state;
- per-feature activation/deactivation of two ZIP Viewer engines.

For ZIP books, `ImageCache` is now suspended once at the book-runtime boundary;
`_ImageLoadTask`, legacy raster prefetch, `ViewerRenderTask`, and the prepared
display cache do not participate in page navigation. Folder, PDF, and external
archive sources still use that legacy subsystem until roadmap item 4. The old
standalone `app/zippla_compatible_raster_path.py` is no longer referenced by
production and remains only as historical A/B code pending removal together
with the old benchmark harness.

Nivis-specific behavior retained in the cutover includes complete-old-frame
retention, GUI-thread-only QPixmap creation, request/source generation checks,
single and spread ordering, LTR/RTL, cover/wide policy, wide split, all fit
modes, manual zoom, rotation, EXIF, resampling, image adjustments, magnifier
source handles, DPI-specific render specifications, terminal error frames,
Browser lane gating, and deferred source close.

Known boundaries after this first unit:

- logical `PageModel` navigation still advances before the frame transaction;
  `ViewerPresentationState` is roadmap item 2;
- Viewer page-list thumbnails are not yet sourced from the runtime and their
  legacy GUI-thread scale must be replaced by roadmap item 3;
- the Widget still names the runtime surface `direct display mode`; its old
  prepared-render guards can be removed after folder/external/PDF adapters use
  the same frame contract;
- decoder-scaled JPEG is a strategy inside the one job. Rotation, wide split,
  adjustments, non-standard resampling, and active magnifier deliberately use
  a full source so the runtime does not switch architectures;
- one running Pillow/native decode may finish after reversal, but its request
  cannot publish and no second Viewer job runs concurrently.

### 11.7 Post-cutover ownership corrections and validation

Integration review found and corrected the following subsystem-boundary
defects before validation:

- pending-open tokens are separate from the active book epoch, so a failed or
  cancelled replacement open cannot invalidate the still-displayed ZIP
  runtime;
- a retired `ZipImageSource` remains open until both its runtime worker and
  queued GUI completion have drained; after `ImageCache` itself is drained,
  non-runtime sources such as PDF still close synchronously;
- a prefetch artifact rejected by the byte budget is marked complete/blocked
  for that work order instead of being decoded in a loop;
- same-key current work and still-useful neighbor work adopt the latest
  request instead of being cancelled by every Window refresh;
- failure to start the current worker produces one terminal frame rather than
  leaving the interactive lane waiting forever;
- runtime shutdown owns both worker completion and queued-result drainage;
- the atomic Widget boundary rejects an incomplete two-slot spread.

The production runtime was then measured without showing a window or sending
native input. `scripts/benchmark_zip_runtime_navigation.py` created one
temporary 243,839,098-byte ZIP containing 12 identical-payload, deterministic
4096 x 6500 detailed JPEG entries. Each entry was 20,319,795 bytes. The
viewport was 1200 x 800, the normal artifact budget was 256 MiB, and the
pressure budget was 6 MiB. Times are offscreen evidence only and do not prove
real-device responsiveness.

| Scenario | Final request -> commit / paint | ZIP entry reads | Observable full-payload materialization | Source / display QImage | QPixmap.fromImage | GUI callbacks (frame / artifact / commit) | Paints (content / frame) | Jobs | Sampled working-set peak delta | Retained cache | Transit requested-page decodes | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| cold forward | 125 / 125 ms | 3 / 60,959,385 B | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.281 MiB | 3 pages / 8,414,880 B | 0 | settled |
| cold reversal | 109 / 125 ms | 3 completed / 60,959,385 B; 1 cancelled read | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 4 | 43.375 MiB | 3 pages / 8,414,880 B | 1 already-running obsolete page | settled; 1 stale result rejected |
| forced-cold roundtrip | 109 / 109 ms | 4 completed / 81,279,180 B; 1 cancelled read | 8 / 162,558,360 B | 4 / 4 | 4 | 2 / 4 / 2 | 2 / 2 | 5 | 44.273 MiB | 3 pages / 8,414,880 B | 2 | settled; 1 stale result rejected |
| rapid final (requests 2..7) | 125 / 125 ms | 3 / 60,959,385 B | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.516 MiB | 3 pages / 8,414,880 B | 1 (page 6 reused as final neighbor); pages 2..5: 0 | settled |
| ready hit | 0 / 0 ms | 0 / 0 B | 0 / 0 B | 0 / 0 | 0 | 1 / 0 / 1 | 1 / 1 | 0 | 0.012 MiB | 3 pages / 8,414,880 B | 0 | cache hit |
| 6 MiB memory pressure | 125 / 125 ms | 3 / 60,959,385 B | 6 / 121,918,770 B | 3 / 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.066 MiB | 2 pages / 5,609,920 B | 0 | settled; no resubmit loop |

For the final displayed frames, worker-completion to GUI-ready time was 0 ms
at the probe's millisecond resolution; GUI-ready to explicit offscreen paint
was 0 ms except for one 16 ms event-pump interval during reversal. The
remaining cold critical path is therefore inside entry read plus Qt JPEG
decode, not repeated GUI commits or paints. In particular, each completed
JPEG currently has two observable full compressed-payload materializations:
one `BytesIO` and one `getvalue()` handoff. Decoder/backend work is deliberately
left as a later optimization only after the Viewer-wide structural cutover.

The monolithic 1,429-test process exposes a pre-existing Qt state-order hang
at the second application-controller test when another QApplication/session
test file precedes it (`test_app_icon.py` and `test_book_session.py` both
reproduce the ordering). `test_application_controller.py` passes all 27 tests
in its own process. Running the suite in file-group processes completed all
1,429 collected tests.
The first PDF split run also exposed a close-order regression; the fix above
was applied and the complete 73-test PDF group then passed.

### 11.8 Implemented second cutover: `ViewerPresentationState`

Roadmap item 2 is now implemented as a presentation-state replacement.  It is
not a cache or decoder adjustment.  `PageModel` remains the requested logical
topology, while one new state owner decides when a completed frame becomes the
displayed book/page and when its dependent UI and persistence values may
advance.

The fixed upstream revision and AGPL provenance remain those recorded in
sections 1 and 5.  The relevant structural source is
`source/ZipPla/ViewerForm.cs`: `bmwLoadEachPage_EachRunWorkerCompleted`
(`:5371`), `SetNewResizedImage` (`:5451`), `showCurrentPage` (`:6438`), and
`pbView_Paint` / `pbView_PaintToCanvas` (`:6748-6990`).  ZipPlaFork keeps the
accepted current page, completed resized artifact, trackbar, and Viewer paint
coordination inside one Viewer-form publication sequence.  NivisViewer now
ports that single-owner/publication structure, while deliberately retaining
its stronger behavior of leaving the preceding frame unmodified until an
entire replacement frame is ready.

The token, epoch, layout/DPR validation, immutable Python snapshots, history
transaction, saved-progress policy, Qt signals, and complete-old-frame swap
are independent NivisViewer implementations.  They are not line-for-line C#
translations.  The structural adoption is recorded as
AGPL-3.0-or-later-derived because the accepted-current/completed-artifact/UI
publication organization was taken from ZipPlaFork.

| Former owner or update site | Former meaning/problem | Replacement owner and boundary |
|---|---|---|
| `PageModel.current_index` / `focused_index` and `ViewerPageNavigationController._move` | Input immediately moves the logical/requested page.  Callers could mistake it for the visible page. | They remain request topology only. `ViewerPresentationState.request_frame` snapshots the requested single/spread display unit, direction, book, layout and DPR without changing the displayed snapshot. |
| `ViewerWindow._active_request_id`, `_visible_page_indexes`, `_display_unit` | Window-owned request serial, requested pages and readiness tracker were independent mutable fields. | `ViewerPresentationState.pending_request_serial`, `requested`, `requested_page_indexes`, and `display_tracker`. Read-only compatibility properties remain only for isolated legacy tests/callers. |
| `ViewerWindow._applied_display_request_id` | ZIP meant post-swap, prepared hit meant synchronous post-swap, and legacy cold meant render start before the actual swap. | `ViewerPresentationState.displayed.token.request_serial` means accepted presentation commit on every path. `ViewerWidget.frameCommitted` is the sole pixel-to-semantic commit bridge. |
| `_go_to_index_with_history`, `_go_to_model_move_with_history`, `_on_page_navigation_changed`, `_go_back_in_page_history`, `_go_forward_in_page_history` | History and metadata progress advanced at input time, including pages never shown during rapid input. | Navigation supplies `PresentationNavigation`; back/forward stacks mutate only in `commit_frame`. `BookSession.notify_page_changed` is emitted only by `_apply_presentation_commit`. |
| `_render_spread`, `_on_zip_runtime_frame_ready`, `_apply_pending_display_demand` | Slider, status and page-list selection had path-dependent pre/post-commit timing; legacy marked a request applied at render start. | These paths only feed terminal slots and a frame token to `ViewerWidget`. `_on_viewer_frame_committed` validates and commits; `_apply_presentation_commit` then updates slider, status, page-list, history actions and progress as one GUI-turn transaction. |
| `_update_slider`, `_update_status`, `_sync_page_list_selection` | They read `model.focused_index`, sometimes combining a requested path/page with the old frame's resolution. | They project only `slider_page_index`, `status_values`, failure state and `displayed_page` from `ViewerPresentationState`. A user-moved slider or page-list row is restored to the last committed value while the request loads. |
| `_save_current_reading_position`, `_queue_metadata_progress` | They saved the requested model focus, so rapid transit pages or an unrendered final page could become progress. | They save the committed book key and `progress_values`. Normal navigation uses the displayed commit page; the explicit “open first page but retain saved resume position” policy is carried in the initial request and committed deterministically. |
| `ViewerWidget._spread` / `_images`, `_commit_display`, `commit_display_ready_frame` | The Widget correctly owned pixels but emitted no common frame identity for all paths. | `ViewerFrameCommit` contains the opaque presentation token, Widget serial, complete spread identity, page indexes, image identities and failures. Both direct and prepared paths emit it only after the atomic pixel swap. |
| `ViewerWindow.open_path`, failed-open callback, empty-book path and shutdown | Pending replacement, active displayed book, and late callbacks used several unrelated generation fields. | `begin_replacement_open`, `fail_replacement_open`, `clear_book`, and `close` fence requests while preserving or clearing the committed snapshot according to the explicit book-state contract. |

`ViewerPresentationState` now exclusively owns the following semantic state:

- requested page/display unit and the last committed displayed page/unit;
- pending request serial and state-owned committed-frame serial;
- navigation direction and navigation/history intent;
- requested book identity, committed book epoch, layout signature and DPR;
- per-slot loading/readiness/failure, whole-frame loading and failure state;
- committed slider value, status values, history page and progress page;
- back/forward history stacks and pending replacement-open/closed fences.

The production transition is:

```text
EMPTY or COMMITTED(old frame)
  -- input/request --> REQUESTED(new token, requested moves, old display/UI stays)

REQUESTED
  -- newer input or reversal --> REQUESTED(new token; earlier tokens become stale)
  -- non-frame failure -------> FAILED_PENDING(old display/UI stays)
  -- exact complete frame ----> Widget atomic pixel swap
                                -> PRESENTATION COMMIT
                                -> displayed + slider + status + page-list
                                   + history + progress advance together

BOOK_REPLACEMENT_PENDING
  -- open failure ------------> COMMITTED(old book remains)
  -- source opened -----------> REQUESTED(new epoch, old frame remains)
  -- first exact frame -------> COMMITTED(new active presentation)

CLOSED
  -- any queued callback -----> rejected; no semantic mutation
```

Only a frame whose book epoch/source identity, request serial, complete display
unit, layout signature and DPR all match the current token can commit.  A
newer request makes every intermediate completion stale, including results
from the old navigation direction.  Stale frames do not update pixels,
displayed state, slider, status, history or progress.

For a spread, every logical slot must be terminal before the Widget may swap
or the state may commit. A genuine end-of-book missing partner is represented
by a complete one-slot display unit; an in-flight half-spread is not. A broken
or unsupported page can be a terminal error slot: if the complete error frame
is atomically swapped, that page becomes the displayed page exactly once and
the failure is exposed by the committed status. A failure that does not
produce a frame leaves the old displayed page intact.

Temporary loading/error messages used for book probing remain a separate
status-overlay layer; they do not redefine the committed page. Paint
acknowledgement still releases prefetch and first-frame gates, but it no
longer determines slider/history/progress. Those values are fixed at the
atomic frame commit.

The remaining compatibility surface is intentionally narrow:

- `_active_request_id`, `_applied_display_request_id`,
  `_visible_page_indexes`, and `_display_unit` are projections/adapters over
  the new state for legacy white-box tests and remaining pipeline calls; there
  is no duplicate production storage;
- `ViewerDisplayUnit` remains the per-request slot-readiness tracker owned by
  the presentation request and is not a second displayed-state owner;
- `PageModel` still owns requested topology and layout navigation, never the
  displayed presentation;
- `ViewerWidget` owns pixels and paint mechanics, not page history or reading
  progress.

The next replacement boundary is `ViewerPageListRuntime`.  Page-list selection
is already a read-only projection of the committed presentation.  The next
unit will take ownership only of row materialization, visible-range thumbnail
requests, thumbnail worker/cache lifetime, delayed updates and pause/resume
against current-page work.  A page-list click may submit a navigation request,
but that runtime must never assign displayed page, slider, history or progress
and must never select or invalidate the main Viewer decode pipeline.

Validation used only syntax checks, fake/temp data and offscreen Qt.  All
1,434 collected regression tests passed across controlled file/test-process
groups.  The new state contracts, Window/Widget integration, ZIP runtime/book
lifetime, metadata progress, prepared-render boundary, navigation/page-list/
magnifier/rotation/DPI behavior, PDF, external archive and image-source groups
are included.

A single combined Qt process remains unreliable because GUI state survives
between unrelated tests.  `test_application_controller.py` stops in Browser
fallback-icon painting when its first two tests share a process, while all 27
tests pass in individual processes.  `test_sprint15_models.py`,
`test_sprint18_navigation_followup.py`, and
`test_viewer_resampling_magnifier.py` also require an already-created session
`qapp` or their established companion group.  The full regression therefore
used those explicit isolation/bootstrap boundaries.  It also found three old
`_commit_display` test spies that did not accept the new `frame_token` keyword;
the adapters were corrected and the complete 39-test resampling/magnifier file
then passed.  These are test-harness boundary findings, not hidden production
fallbacks.

The production ZIP offscreen probe was rerun after the presentation cutover
with the same 12-entry, 4096 x 6500 detailed-JPEG fixture used in section 11.7
(20,319,795 bytes per entry; 243,839,098-byte ZIP; 1200 x 800 viewport).  It
does not claim real-device improvement and deliberately does not alter cache
counts or decoder strategy.

| Scenario | final request -> semantic-adjacent Widget commit / paint | ZIP reads / bytes | observable whole-payload copies / bytes | source + display QImage | QPixmap.fromImage | frame/artifact/commit callbacks | content/frame paints | jobs | sampled peak WS delta | retained pages / bytes | transit request decodes | worker-complete -> paint |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cold forward | 125 / 125 ms | 3 / 60,959,385 | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.066 MiB | 3 / 8,414,880 | 0 | 0 ms |
| cold reversal | 109 / 109 ms | 3 completed / 60,959,385; 1 cancelled | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 4 | 43.074 MiB | 3 / 8,414,880 | 1 | 0 ms |
| forced-cold roundtrip | 109 / 109 ms | 4 completed / 81,279,180; 1 cancelled | 8 / 162,558,360 | 4 + 4 | 4 | 2 / 4 / 2 | 2 / 2 | 5 | 44.477 MiB | 3 / 8,414,880 | 2 | 0 / 0 ms |
| rapid final (requests 2..7) | 125 / 125 ms | 3 / 60,959,385 | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.293 MiB | 3 / 8,414,880 | 1 (page 6; pages 2..5 zero) | 0 ms |
| ready hit | 0 / 0 ms | 0 / 0 | 0 / 0 | 0 + 0 | 0 | 1 / 0 / 1 | 1 / 1 | 0 | 0.016 MiB | 3 / 8,414,880 | 0 | n/a |
| 6 MiB pressure | 141 / 141 ms | 3 / 60,959,385 | 6 / 121,918,770 | 3 + 3 | 3 | 1 / 3 / 1 | 1 / 1 | 3 | 43.344 MiB | 2 / 5,609,920 | 0 | 16 ms |

Every scenario settled before its timeout, produced exactly one final-frame
commit per final displayed request (two for the intentionally two-leg
roundtrip), and left no benchmark process behind.  The cold critical path
remains archive read plus JPEG decode; presentation commit did not add extra
QImage, QPixmap, GUI callback, paint or job work.  Real application launch and
native input remain intentionally untested.

## 12. Viewer-wide follow-up audit and third structural replacement

This audit was not limited to the reported large-ZIP cold miss. It re-read the
production paths for ZIP, folder/single-image books, RAR/7z, PDF, the Viewer
page list, presentation/UI projection, worker coordination, book replacement,
and shutdown before choosing the next unit. The fixed ZipPlaFork reference is
still revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later. The preserved license and copyright notices remain in
`licenses/ZipPlaFork/AGPL.txt`, `licenses/ZipPlaFork/About.txt`, and
`THIRD_PARTY_NOTICES.md`.

### 12.1 Newly confirmed subsystem differences

| Subsystem | ZipPlaFork fixed-revision structure | NivisViewer state at the start of this audit | Performance/stability consequence | Difficulty / Nivis feature impact | Decision |
|---|---|---|---|---|---|
| ZIP work order and completed-frame retention | `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, completion selection `:298-303`) retains a page-wide priority permutation. `source/ZipPla/ViewerForm.cs`, `priorityLevel` (`:5586-5605`) ranks current/next/previous/remaining pages. `ReduceUsingMemory` (`:5471-5524`) uses that order only when memory must be reduced. | `ViewerWindow._zip_runtime_request` made a correct three-unit active frontier, but `ZipRasterBookRuntime._prune_frames` treated those three keys as the complete cache membership and immediately removed every other completed frame before checking the configured unit/byte budget. A nominal 10-page cache therefore retained roughly three single pages. | Every move beyond the immediate neighbor converted a previously completed page back into a cold ZIP read/decode. Short forward/backward/roundtrip operation could not benefit from the configured cache even with ample memory. | Medium. No visible feature contract needs to change; eviction must still protect the current complete single/spread/split frame and obey the byte budget. | **Rank 1; structural replacement implemented in this section.** |
| ZIP archive/index/lifetime | `PackedImageLoader` retains the book/index and opens an entry stream per page, then disposes after the Viewer worker stops. | `BookSession` + `ZipImageSource` already retain one `ZipFile` and entry index, fence callbacks by epoch, and defer source close until runtime work drains. | No repeated ZIP reopen was found on the production runtime. The remaining cold cost is entry materialization/decode, not book lifetime. | High risk to replace; Nivis shutdown behavior is stronger. | **Maintain.** Optimize entry transport only after larger ownership cutovers. |
| Folder and single-image books | The same Viewer page worker and artifact arrays are used rather than a separate feature engine. | Both use `FolderImageSource` but return to legacy `_ImageLoadTask -> ImageCache -> ViewerRenderTask -> prepared QPixmap` with independent source/render caches and timers. JPEG/WebP target decode helpers already exist. | A common folder operation still pays two scheduling stages and keeps the old production architecture alive. A running Pillow decode is only stale-rejected, not source-cancelled. | Medium. Low source-adapter difficulty, but PageList currently depends on `ImageCache.pageLoaded`. | **Rank 3; structural port after PageList separation.** |
| RAR/7z/CBR/CB7 | ZipPlaFork's packed-loader family keeps archive/page ownership inside the Viewer loader. | `SevenZipImageSource` indexes once, but each page invokes a new 7-Zip/WinRAR process, materializes full stdout bytes, then performs a full Pillow decode. `solid` is known but not used for work order or extraction policy. | Process startup, repeated archive traversal (especially solid archives), whole-entry copying, and full-resolution decode can dominate. Merely connecting this source to the ZIP runtime would not remove the main cost. | High. Requires a book-scoped `ExternalArchivePageSource`, cancelable extraction/session policy, and solid-aware strategy while preserving optional external-tool behavior. | **Rank 4; full source/runtime boundary replacement.** |
| PDF | ZipPlaFork integrates document pages into its loader/page arrays. | `PdfImageSource` and `PdfiumService` already keep one document, target-render by viewport/DPR, serialize PDFium calls, deduplicate work, cancel, and close deterministically. The outer `ImageCache` worker waits on the PDF service worker and then converts PDF pixels through PIL before QImage/render preparation. | Document reopen is not a problem. Double asynchronous ownership and pixel conversion remain, but the source-specific target renderer is already mature. | High. Rotation, annotation, DPR, page size, service priority, and shutdown must remain intact. | **Rank 5; sibling `PdfBookRuntime` using the common frame contract, not a forced raster decoder.** |
| Viewer page list / thumbnail | `source/ZipPla/CatalogForm.cs`, `ThumbViewerItem` (`:30231`, `LoadAsync :30280-30302`, `Clear :30304-30308`) and `ThumbViewer.PaintPart` (`:32490-32525`) keep catalog thumbnail work separate, globally one-at-a-time, and limited to the visible region plus a small margin. `preRenderScroll` (`:32562-32673`) reuses the existing canvas while scrolling. | `ViewerWindow._rebuild_page_list` synchronously creates every `QListWidgetItem`; selection and thumbnail updates scan every row. `PageThumbnailProvider.create_icon` performs smooth large-QImage scaling plus `QPixmap.fromImage` on the GUI thread. It consumes legacy Viewer-cache results; ZIP runtime results do not populate it. Once shown, hiding/fullscreen does not reliably release all icon memory or stop legacy icon updates. | Visible page list can delay the first current request at open, delay current-frame handling inside `pageLoaded`, create O(page-count × decoded-count) row lookup, and retain unbudgeted icons. It is also the main dependency preventing removal of `ImageCache` for folder/external sources. | Medium-high. Filtering, committed selection, click navigation, size/DPR, and book epochs must remain; thumbnail work needs its own read-only source/session lifetime. | **Rank 2; full `ViewerPageListRuntime` replacement next.** |
| Source/display artifact reuse after layout change | `ViewerForm.cs` page-indexed `PreFilteredImageArray`, `OriginalImageInfoArray`, `ResizedSizeArray`, and `ResizedImageArray` (`:39-42`) preserve decoded source when only resized output is invalidated (`:2882-2896`, worker-start reuse `:2957-2988`). | `_UnitKey` includes the full render spec and `_CachedFrame` owns source QImage plus display pixmap together. `invalidate_layout` clears both, so resize/DPI/rotation/magnifier/layout changes can re-read and re-decode. | Feature changes may repeat archive work even though page navigation is otherwise ready. | High. Decode-resolution sufficiency, filters, rotation, split and magnifier source ownership need a page record with source/display variants, not a second independent cache. | **Fold into rank 3 common page-artifact runtime.** |
| Presentation/UI update | ZipPlaFork accepts the completed resized artifact and current/UI state in one Viewer-form sequence. | `ViewerPresentationState` now owns requested/displayed serials and atomically projects slider, status, history and progress from `ViewerWidget.frameCommitted`. | No new critical-path duplicate state update was found. Toolbar/menu projection still runs after commit but does not decode or scale images. | Low immediate benefit; changing it risks the completed-old-frame contract. | **Maintain.** |
| Browser/background work and active state | ZipPlaFork thumbnails use one semaphore; its remaining Catalog background-mode controller mainly affects metadata workers. | `ImageWorkCoordinator` reserves one Viewer lane and pauses the Browser lane while a cold current frame is outstanding, resuming after paint. | Nivis has the clearer priority boundary. Copying ZipPlaFork's CPU-count/thread-priority logic would not improve the actual thumbnail decoder path. | Low benefit. | **Maintain Nivis coordinator; use it from the future PageList runtime.** |
| Error, book switch and close | ZipPlaFork cancels/waits, clears arrays, disposes loader/bitmaps. | Terminal error frames, immutable request/book/layout checks, retired runtime/source drainage, failed replacement-open preservation, and asynchronous close are already explicit. | No forced GC or repeated book open was found. Literal synchronous wait/old-frame clear would be a regression. | High risk. | **Maintain Nivis lifetime; extend its ownership list when new runtimes are added.** |

The most important newly found defect was therefore not a decoder choice or a
cache-count preference. It was a **cache ownership error**: the active
scheduling frontier and the completed-artifact retention set were the same
mutable tuple. That contradicted the ZipPlaFork structure already cited as the
porting source.

### 12.2 Ranked replacement roadmap after the audit

| Rank | Large work unit | Real-device frequency/impact | Duplicate work and GUI impact | Old structure removable | Risk | Result |
|---:|---|---|---|---|---|---|
| 1 | **ZIP work-order / retention / eviction owner** | Every normal ZIP navigation; directly affects short reversal and roundtrip outside three pages. | Removes repeated entry read/decode/QImage/QPixmap/callback work for completed pages; no extra GUI stage. | Old `_frames` ownership and desired-set purge. | Medium-low because frame/publication contracts stay unchanged. | **Implemented now.** |
| 2 | **`ViewerPageListRuntime` + virtual row model** | Optional (default hidden), but severe when visible and a prerequisite for deleting legacy cache consumers. | Removes GUI smooth-scale, all-row item construction/search, offscreen thumbnail work, and main-source/cache coupling. | `PageThumbnailProvider`, PageList calls from `ImageCache.pageLoaded`, eager `QListWidgetItem` lifecycle. | Medium-high. | **Next.** |
| 3 | **Source-independent `RasterBookRuntime` / page artifact record for folder and single-image books** | Very common source types; every page still uses the legacy two-stage path. | Collapses decode→resize→publish and source/display lifetime; enables layout-only display invalidation. | Folder/single participation in `ImageCache`, prepared-display scheduler/cache, raster decode/display timers. | High; schedule after PageList no longer consumes the legacy cache. | Planned. |
| 4 | **Book-scoped external archive page source for RAR/7z** | Source-dependent but potentially dominant for solid/large archives. | Targets process-per-entry, repeated archive traversal and whole stdout copies, not merely Viewer callbacks. | `SevenZipImageSource.read_entry -> bytes` page lifecycle and per-page CLI session. | High and backend-specific. | Planned after common runtime contract. |
| 5 | **`PdfBookRuntime` plus source/display variant reuse** | PDF/resize/rotation-specific. | Removes outer worker wait and redundant pixel conversion while keeping PDFium target render. | PDF participation in `ImageCache`/prepared scheduler. | High; current PDF lifetime is already sound. | Planned last among these five. |

PageList was explicitly re-evaluated rather than accepted mechanically. It is
ranked second because it is normally hidden, whereas the selected cache defect
is on every ZIP request. It remains the next replacement because its direct
`ImageCache` dependency would otherwise force duplicate full-resolution decode
or missing thumbnails when folder/single/RAR/7z move to the common runtime.

### 12.3 Implemented replacement: `_ZipRasterFrameStore`

`app/zip_raster_book_runtime.py` now has one explicit owner for completed ZIP
frames. The old `ZipRasterBookRuntime._frames`, `_frame_bytes`, and
`_prune_frames` responsibilities were removed from the runtime and replaced by
`_ZipRasterFrameStore`:

- `ZipRasterRequest.navigation_direction` carries the committed navigation
  policy from `ViewerPresentationState` into the retention plan;
- `set_retention_order` records the current -> direction neighbor -> reverse
  neighbor frontier without declaring every other completed page invalid;
- `_retention_rank` represents the remaining full-book order by page distance
  and direction, avoiding an O(all pages) Python allocation on every wheel
  event;
- `put` retains a completed frame until the combined source/frame hard or soft
  byte policy actually requires reduction; there is no retained-unit cap;
- `_prune` evicts the lowest-ranked non-current frame only under real pressure;
- `can_admit_prefetch` allows a newly important neighbor to replace a farther
  frame, but stops low-priority decode before it would merely evict an
  equal-or-better retained frame;
- the active execution stays one-worker-wide.  The final production policy in
  sections 19--20 releases a four-forward/one-reverse complete-unit startup
  runway at commit and then walks the book lazily under the combined byte
  policy; physical paint is not a scheduling gate.

The adapted processing source is:

| ZipPlaFork source / method | Adopted structure | NivisViewer destination |
|---|---|---|
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, `:298-303`) | Latest work order selects useful work without invalidating already completed artifacts. | `ZipRasterBookRuntime.request`, `_drive`; `_ZipRasterFrameStore.set_retention_order` |
| `source/ZipPla/ViewerForm.cs`, `priorityLevel` (`:5586-5605`) | Current, forward/reverse neighbors, then remaining pages define one importance order. | `ViewerWindow._zip_runtime_request`; `ZipRasterRequest.navigation_direction`; `_ZipRasterFrameStore._retention_rank` |
| `source/ZipPla/ViewerForm.cs`, `ReduceUsingMemory` (`:5471-5524`) | Completed source/display artifact is disposed from the least important end only when memory must be reduced. | `_ZipRasterFrameStore.put`, `_prune`, `_eviction_candidate`, `_frame_bytes`, `set_limits` |

This is a direct structural port/adaptation and is recorded as
AGPL-3.0-or-later-derived. It is not a line-for-line C# translation. The
distance rank is an independent Python representation of the full priority
order, and GUI-thread-only QPixmap ownership, immutable request/epoch/layout
validation, byte accounting, old-frame retention and pre-decode admission are
NivisViewer-specific extensions.

Production behavior changed from:

```text
new request
  -> active keys = current / next / previous
  -> delete every cached frame outside active keys
  -> enforce unit/byte budget
```

to:

```text
new request
  -> replace active job frontier = current / next / previous
  -> re-rank all completed frames around current + direction
  -> keep completed frames unchanged while within unit/byte budget
  -> if a nearer missing neighbor outranks a retained far frame, decode once
     and evict the far frame
  -> if the current frame already exhausts memory, stop background admission
     before ZIP read/decode
```

No display feature chooses another engine. Single/spread, LTR/RTL, cover/wide
policy, split pages, fit/manual zoom, rotation, EXIF, filters, resampling,
magnifier source, DPR, terminal error frame, atomic old-frame replacement,
failed book replacement and deferred archive close remain unchanged.

### 12.4 Offscreen evidence for the replaced contract

The benchmark uses one temporary offscreen ZIP with 12 identical detailed JPEG
entries, each 1600 x 2400 and 2,930,032 bytes, a 35,161,942-byte archive, a
1000 x 720 viewport, and a normal 192 MiB artifact budget. The new
`retained_roundtrip` walks from page 1 through pages 2, 3, 4 and 5, waits for
each production frame/neighbor order to settle, then returns to page 2 without
clearing the runtime. A is a benchmark-only reproduction of the removed
three-unit membership purge; B is the production store. Both use the same
`ViewerWindow -> ZipRasterBookRuntime`, decoder, render path and payload.

| Metric for final return to page 2 | Old A: three-unit purge | New B: retained frame store |
|---|---:|---:|
| request -> frame commit / paint | 31 / 31 ms | 0 / 0 ms |
| final frame cache hit | no | yes |
| ZIP entry opens / bytes | 3 / 8,790,096 | 0 / 0 |
| observable full-payload materializations / bytes | 6 / 17,580,192 | 0 / 0 |
| source / display QImage outputs | 3 / 3 | 0 / 0 |
| `QPixmap.fromImage` | 3 | 0 |
| GUI callbacks (frame / artifact / presentation commit) | 1 / 3 / 1 | 1 / 0 / 1 |
| paints (content / runtime frame) | 1 / 1 | 1 / 1 |
| worker jobs | 3 | 0 |
| transit-page decode / unpainted decode | 0 / 2 | 0 / 0 |
| retained pages / accounted bytes | 3 / 7,096,896 | 6 / 14,193,792 |
| scenario working-set delta / sampled peak delta | 3.465 / 8.492 MiB | 0.012 / 0.012 MiB |
| settled / cleanup cancellation | yes / no | yes / no |

The extra 7,096,896 accounted bytes in B are intentional reuse within the
configured 192 MiB budget, not unbounded icon/source memory. Under an extreme
1 MiB budget where the current frame itself accounts for 2,365,632 bytes, B
submitted exactly one current job, opened/read one 2,930,032-byte ZIP entry,
made two observable payload materializations (5,860,064 bytes), one source and
one display QImage, one QPixmap, one queued callback and one paint. It retained
only the current page, recorded one `prefetch_admission_stops`, settled, and
did not decode next/previous pages only to evict them.

The deliberately cold scenarios remain structurally unchanged: current is
still decoded first, prefetch starts only after paint, at most one worker runs,
and stale reversal results cannot publish. On the smaller fixture, cold
forward remained 31/31 ms with 3 jobs/3 reads, reversal remained 31/31 ms with
4 jobs and one stale cancellation, forced-cold roundtrip was 16/16 ms before
and 15/15 ms after, rapid-final was 31/31 ms before and 32/32 ms after, and a
ready hit remained 0/0 ms. These offscreen values do not establish real-device
improvement; they show that the replacement removes repeated work on retained
pages without changing the cold current-frame pipeline.

Validation used syntax/import checks and only pytest/offscreen Qt with
fake/mock/temp sources. The complete 1,435-test collection passed across
controlled process groups: 1,408 tests passed in isolated file/known-qapp
groups, and all 27 `test_application_controller.py` cases passed in separate
bounded processes. The directly related runtime, presentation, BookSession,
ViewerWindow/Widget, page-list selection, magnifier/rotation/DPI, PDF,
external-archive, metadata/progress, image-source and navigation groups are
included. A combined application-controller process still exhibits the known
Qt state-order stall and was not used as evidence. No real application,
native input, external GUI application, commit, or push was invoked.

### 12.5 Removed and remaining old structures

| Old structure | Classification after this change |
|---|---|
| `ZipRasterBookRuntime._frames` as a runtime-owned unranked dictionary, `_prune_frames` desired-set purge, and runtime `_frame_bytes` | **Removed/replaced now** by `_ZipRasterFrameStore`. |
| Three active work keys used as the completed-cache membership set | **Removed now.** They are scheduling/protection input only. |
| Prefetch that decodes despite a current-only byte budget, then discovers the result cannot coexist | **Removed now.** Admission stops before the ZIP read. |
| `PageThumbnailProvider` and PageList thumbnail calls from `ImageCache.pageLoaded` | **Remove in rank 2.** Still production for legacy sources only. |
| Folder/single/RAR/7z/PDF `ImageCache` + prepared display pipeline | **Maintain only as explicit other-format fallback until ranks 3-5 migrate.** Never a ZIP feature fallback. |
| `ZipPlaCompatibleRasterPath` and its old A/B | **Production-external historical code.** Remove/move to history after the remaining benchmark evidence no longer needs it. |
| compatibility-only page-state properties | **Remove after remaining legacy runtime callers/tests migrate.** They do not own production state. |

### 12.6 Next replacement boundary and real-device checks

The next subsystem is `ViewerPageListRuntime`, bounded as follows:

- own a lightweight page-index/row model and O(1) reverse map;
- request only the visible viewport plus a small margin;
- run one low-priority thumbnail job, replace the order during scrolling, and
  reject old book/spec generations;
- pause/cancel while the main current frame is cold and resume only after its
  accepted paint;
- perform decode/orientation/thumbnail scale off the GUI thread; perform only
  accepted visible QPixmap/QIcon upload on the GUI thread;
- own a byte budget and release offscreen/hidden artifacts;
- never assign displayed page, slider, history or progress; selection remains
  a projection of `ViewerPresentationState`;
- never share a ZIP entry lock/handle in a way that can block the current
  Viewer job; BookSession must defer the dedicated thumbnail source close
  until its one job and queued completion drain.

Real-device verification for the current replacement should use the same large
ZIP and compare: sequential navigation beyond three pages, immediate reversal,
two-to-eight-page roundtrips, repeated oscillation near the configured cache
edge, rapid wheel then return, single/spread and LTR/RTL, rotation/resize/DPI
invalidation, memory-pressure behavior, book replacement, Viewer close, and
Windows working-set stability. Expected evidence is fewer cold flashes/pauses
and no additional read/decode on a page still within the configured budget;
offscreen results alone are not a claim of improved physical-device feel.

## 13. Overnight whole-Viewer audit and fourth structural replacement

This audit started from NivisViewer commit
`da24bdd47fc706327b3c70cb0fa64bbeb450eee8` (`Separate ZIP frame retention
from the active work frontier`).  `HEAD`, `origin/main`, and `origin/HEAD` were
identical, and the worktree had no modified or untracked files.  That commit is
therefore the protected baseline for this unit.

The ZipPlaFork reference was checked again on 2026-08-01.  Remote `HEAD` and
`refs/heads/master` still both resolve to the fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`; there are no newer upstream
Viewer or Catalog changes to merge into this comparison.

### 13.1 Updated unresolved subsystem comparison

The comparison below was completed before implementing another subsystem.  A
"page move" frequency means the path can affect normal wheel/click/key
navigation; "optional-visible" means it is absent with the page-list dock
hidden but can be severe once the dock is enabled.

| Candidate subsystem | ZipPlaFork fixed-revision design | NivisViewer design at the audit baseline | NivisViewer problem and real-device relevance | Frequency / GUI and duplicate work | Port difficulty, feature effect, and risk | Replacement boundary and expected effect |
|---|---|---|---|---|---|---|
| Page list / thumbnail | `CatalogForm.cs` separates lightweight `ThumbViewerItem` metadata from images. `ThumbViewer.PaintPart` loads only the visible region plus one small margin, clears images outside it, and `ThumbViewerItem.LoadAsync` is globally one-at-a-time. Data-index/show-index arrays provide direct mapping and changed cells are redrawn locally. | `ViewerWindow` owns a `QListWidget`, synchronously creates every `QListWidgetItem`, scans every row for selection and every accepted thumbnail, and calls `PageThumbnailProvider.create_icon` on the GUI thread. The thumbnail source is the legacy full-size `ImageCache`; ZIP runtime artifacts do not populate the list. Hidden/fullscreen state can retain all icons after the list has once been shown. | Opening a large visible list can run O(all pages) widget creation before the current request. A legacy current/prefetch callback can smooth-scale a large image and upload a pixmap before main-frame preparation. Icons are outside the Viewer byte ledger, and the legacy-cache dependency blocks removal of the old folder/RAR/PDF pipeline. | **Optional-visible, every accepted legacy decode and every presentation selection.** GUI work is O(rows) lookup plus large-image scale/upload; row/icon memory and source decode can be duplicated. | Medium-high. Filtering, click/keyboard navigation, committed selection, DPR/size/rotation/filter generations, broken pages, all source types, book switch and close must remain. A thumbnail worker must not share the ZIP entry lock with current work. | **Full replacement:** `QAbstractListModel/QListView` virtual rows plus a book-owned `ViewerPageListRuntime`, visible+margin order, one low-priority worker, dedicated thumbnail source/artifact cache, byte budget and epoch/spec rejection. Expected to remove GUI scaling/all-row searches and current-cache coupling when the dock is visible. |
| Folder / single-image Viewer | The same page-worker/work-order/display-artifact structure is used for packed and ordinary images. | `FolderImageSource` has useful JPEG/WebP target-decode helpers, but production returns to `_ImageLoadTask -> ImageCache -> ViewerRenderTask -> prepared QPixmap`; source and display caches, timers and callbacks are separate. A single-image open is a folder book using this same path. | Every folder page miss crosses two schedulers and two caches. Running Pillow work cannot be source-cancelled, layout changes can repeat preparation, and the path keeps most legacy raster state alive. | **Common, every folder/single page move.** Normally one GUI callback per source and one per render plus cache bookkeeping; decoded and prepared artifacts overlap. | High as a full deletion, medium after PageList no longer consumes `ImageCache`. Spread, wide-page discovery, magnifier, adjustments, rotation and folder snapshot ordering must remain. | **Structural replacement after PageList:** source-independent raster book runtime/page artifact record using the existing presentation frame contract. Expected to remove the two-stage scheduler and enable display-only invalidation. |
| RAR / 7z / CBR / CB7 | Packed-image ownership remains book scoped and entry work participates in the same page order/cache lifecycle. | `SevenZipImageSource` keeps the entry listing but starts an external 7-Zip/WinRAR process for every page, materializes all stdout bytes, then performs full Pillow decode through the legacy pipeline. `solid` is recorded but not used by extraction order. | Process startup, repeated archive traversal and full-entry/full-raster materialization can dominate, especially for solid archives. Merely attaching the current source to a new scheduler would leave the primary cost intact. | **Format-dependent, every external-archive miss.** Low GUI cost itself, but high process/I/O/CPU duplication; reversal cancels by terminating the per-entry request after work may already have started. | High and backend-specific. Must retain optional external tools, Japanese paths, output/size limits, cancellation, solid behavior and safe process shutdown. Persistent-library support is not assumed and no dependency may be added. | **Full source/runtime replacement:** book-scoped `Rar7zBookRuntime`/page source with listing reuse, cancelable extraction policy and solid-aware work order. Expected effect can be large but is not safe as the first overnight cutover. |
| PDF | Document pages share the Viewer page order and page artifact lifecycle. | `PdfImageSource`/`PdfiumService` already keep one document, target-render by viewport/DPR, serialize/prioritize/deduplicate PDFium work and close via a control barrier. The outer `ImageCache` worker waits for the service, converts pixels through PIL, and then enters prepared rendering. | Archive/document lifetime is already sound. Remaining costs are double asynchronous ownership, PIL/pixel conversion, and source/prepared duplication rather than document reopen. | **PDF-only, every miss/rerender.** Callback and pixel conversion duplication; current priority inside the PDF service is already strong. | High. Native page rotation, annotations, zoom/DPR target size, labels, magnifier and shutdown ordering are mature and easy to regress. | **Sibling `PdfBookRuntime` later:** preserve PdfiumService and expose the common complete-frame/cache/paint contract. Expected to remove the outer wait/conversion stages without forcing PDF through a raster decoder abstraction. |
| Viewer UI critical path | Accepted resized image, current page and trackbar are coordinated in the Viewer form; Catalog redraw is local. | `ViewerPresentationState` now commits displayed page, slider, status, history and progress with `ViewerWidget.frameCommitted`. Page-list selection still performs a linear row search. Toolbar/menu projection is separate but does not decode or resize pixels. | The previous logical/visual split is resolved. No new slider/status/history/progress critical-path owner was found. Page-list selection remains the material synchronous outlier. | **Every committed page**, but current semantic projections are small; PageList adds O(rows). | Low benefit/high regression risk for another transaction rewrite. Fullscreen chrome, pan/zoom, slideshow and metadata policy are Nivis-specific. | **Maintain presentation transaction; move only PageList selection to O(1) model mapping.** No separate UI rewrite is justified now. |
| Cache / memory | Page-indexed source and resized arrays share one page lifecycle; `ReduceUsingMemory` evicts from the least-important end. Catalog images outside the visible range are explicitly disposed. | ZIP now has `_ZipRasterFrameStore`; other formats retain `ImageCache` plus Viewer prepared/render cache. PageList `QIcon/QPixmap` memory is unbudgeted. ZIP source/display variants are still invalidated together on layout changes. | The largest fixed ZIP retention defect is resolved. The next unbounded ownership is PageList icons. Legacy formats still duplicate source/display lifetime, and ZIP layout-only changes may repeat decode. | ZIP store: every ZIP request and now bounded. PageList: optional-visible and grows with visited rows. Legacy caches: every non-ZIP page. | PageList budget is medium risk; common artifact records are high risk because magnifier/source-resolution sufficiency must remain. | **First bound PageList independently; then introduce a common page-artifact record with source/display variants.** Do not retune ZIP counts or decoders in this unit. |
| Input / navigation | The worker consumes the latest work order; completed work remains cacheable, and the next order is chosen after completion. | Wheel/click/key navigation coalesces requests; `ViewerPresentationState` owns requested/displayed serials and direction; ZIP runtime has a one-job current/next/previous frontier with stale rejection and retained frames. Legacy sources keep their older decode/display demand timers. | ZIP navigation semantics are no longer the primary structural gap. PageList scroll has no visible-row scheduler because thumbnails are incidental cache callbacks. Non-ZIP reversal can leave one stale decode and a separate stale render. | **Every input.** ZIP duplicate transit work is bounded; PageList and legacy sources are the remaining work-order gaps. | Low risk to preserve current navigation; high risk to unify all source engines at once. | **Maintain navigation/presentation contracts.** PageList gets an independently replaceable visible order; folder/external/PDF migrate one book runtime at a time. |
| Book switch / close | Page workers are interrupted/waited before packed loaders and bitmaps are disposed. | `BookSession` separates pending-open from active epoch, retires ZIP runtime/source until workers and queued GUI completions drain, preserves the old frame on failed replacement, and PDF closes through a service barrier. PageList currently owns no worker/source and icons live until widget/model clearing. | Existing main-source lifetime is stronger than a literal synchronous port. A new PageList source would become an unsafe extra owner unless BookSession retires its runtime and waits for both worker completion and queued-result rejection. | **Every book switch/close.** Low steady-state GUI cost; high consequence if wrong. | Medium for a dedicated runtime, very high for broad lifetime unification. Deadlock or premature archive/document close is a stop condition. | **Extend BookSession ownership only for PageList runtime now.** The runtime owns its forked read-only source, cancels and drains before close; failed replacement leaves the active runtime/model untouched. |

### 13.2 Ranked replacement units

The already-completed ZIP frame-retention change is not ranked again.  Scores
are qualitative (`5` is strongest/highest); risk is inverse (`5` is most
risky).  "Overnight" means the boundary can be completed and reviewed without
leaving a mixed production engine.

| Rank | Large work unit | Real-device effect | Production frequency | Current/UI contention | Old code removable | ZipPla clarity | Risk | Overnight completion | Decision |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | **`ViewerPageListRuntime` + virtual row model** | 4 | 2 when hidden / 5 when visible | 5 | 4 | 5 | 3 | 4 | **Implement as this unit.** It is the next roadmap prerequisite and has a closed ownership boundary. |
| 2 | **Source-independent `FolderRasterBookRuntime` + page artifact record** | 4 | 5 | 3 | 5 | 4 | 4 | 2 | Next after PageList. Start only when the PageList no longer reads `ImageCache`. |
| 3 | **Book-scoped `Rar7zBookRuntime` / external archive page source** | 5 for affected books | 2 | 2 | 4 | 4 | 5 | 1 | High expected upside, but external-process/solid-archive lifetime is too broad for the first cutover. |
| 4 | **`PdfBookRuntime` preserving PdfiumService** | 3 | 2 | 2 | 3 | 3 | 5 | 1 | Defer; current document lifetime and prioritization are already correct. |
| 5 | **Common source/display variant and legacy prepared-pipeline removal** | 4 | 5 | 3 | 5 | 4 | 5 | 1 | End state spanning ranks 2-4, not a single safe overnight patch. |

### 13.3 Selected replacement contract

The selected subsystem is `ViewerPageListRuntime`; this decision follows the
audit rather than treating the previous roadmap ordering as automatic.  The
production boundary is:

```text
BookSession
  -> one ViewerPageListRuntime per installed book
       -> lazy independent thumbnail source/session
       -> latest visible rows + small margin
       -> at most one low-priority worker job
       -> target-sized QImage cache with a byte budget
       -> epoch/spec/visibility rejection and explicit shutdown

ViewerWindow
  -> QAbstractListModel / QListView lightweight rows
  -> viewport rows -> runtime work order
  -> accepted target-sized QImage -> one GUI QPixmap/QIcon upload
  -> click submits navigation only
  -> committed ViewerPresentationState projects selection through O(1) map
```

The runtime must pause/cancel while a current Viewer frame is cold, resume
only after the accepted paint/fallback releases the interactive lane, and use
an independent ZIP handle so a thumbnail cannot own the main
`ZipImageSource` lock.  Folder, external-archive and PDF thumbnail sources are
forked lazily off the GUI thread.  BookSession retirement must keep the main
source and the runtime alive until its worker and queued completion drain.

The immediate removal targets are eager `QListWidgetItem` ownership,
`PageThumbnailProvider`, GUI-thread smooth scaling, PageList calls from
`ImageCache.pageLoaded`, all-row selection/thumbnail scans, and hidden
unbudgeted icons.  `ViewerPresentationState`, the main ZIP runtime/cache,
Viewer atomic old-frame swap, Browser thumbnail subsystem, PDF service and
external archive backend remain independent and unchanged by this unit.

### 13.4 Implemented replacement: `ViewerPageListRuntime`

The selected boundary is now implemented.  It is one subsystem replacement,
not a thumbnail helper attached to the old `QListWidget`:

```text
BookSession (one owner per installed book)
  -> ViewerPageListRuntime
       -> lazy source-specific read-only fork
       -> replaceable visible-first, direction-aware row order
       -> one active low-priority job
       -> entry read -> target decode/render -> orientation/filter/rotation
          -> final-size QImage
       -> desired/spec/book-generation validation
       -> 32 MiB LRU QImage byte cache

ViewerWindow (GUI projection only)
  -> ViewerPageListModel / QListView virtual rows
  -> visible rows + one viewport ahead / half viewport behind (capped)
  -> accepted final-size QImage -> QPixmap/QIcon once
  -> page click -> navigation request only
  -> frame commit -> O(1) committed-page selection
```

The production integration is split deliberately:

- `app/viewer_page_list_runtime.py` owns the virtual model, work order,
  one-job scheduler, cancellation, target-sized artifact, cache accounting,
  stale rejection and clone shutdown.  Worker code creates no `QPixmap` or
  `QIcon`.
- `app/book_session.py` creates exactly one runtime for each installed book,
  retires it on switch/close, and retains the main source until both native
  work and its queued completion have drained.
- `app/image_source.py` provides lazy independent folder, ZIP and 7-Zip/RAR
  thumbnail sessions.  The external-archive fork reuses the immutable listing
  snapshot instead of starting a second listing process.
- `app/pdf_image_source.py` opens a separate PDF document session through the
  existing `PdfiumService`; PageList renders directly to the thumbnail target
  and closes that document after callback drainage.
- `app/viewer_window.py` maps visible rows first, then one viewport in the
  current scroll direction and at most half a viewport behind (96 ahead / 48
  rear rows maximum).  It uses row-to-page mapping after filtering, performs
  accepted `QPixmap.fromImage` only for visible rows, and projects the
  committed presentation page through direct page/row mapping.  It pauses
  PageList when a ZIP, folder/external-archive, or PDF current frame is cold
  and resumes on accepted paint (or the existing post-completion hidden-window
  fallback).

Hidden/fullscreen PageList state has zero model rows, zero desired jobs, no
open thumbnail-source fork, and retains only the bounded 32 MiB QImage LRU.
That cache lets reopening the optional dock republish recently used images
without decoding again; its independent archive/PDF source clone is released
when hidden and reopened lazily on a cache miss.  Changing thumbnail size,
DPR, rotation or adjustments changes the immutable spec; late results from
the preceding spec cannot upload.  A rapid scroll replaces the order, gives
newly visible rows priority, and cancels a lower-priority prefetch when needed.
Only visible rows own QIcon/QPixmap projections; the runtime republishes its
cached QImage when a prefetched row becomes visible.  Cache artifacts are
retained by LRU recency rather than viewport membership, with the byte budget
as the sole eviction bound.

Book replacement has a two-phase presentation boundary.  Source installation
stages the new runtime but does not relabel the old frame.  The preceding row
projection remains visible but disabled and no longer holds the retired
QObject.  The first complete replacement `ViewerPresentationState` frame
commit activates the new runtime/model atomically.  A failed replacement never
installs or stages a new runtime, so the active book/list remains usable.

### 13.5 Exact ZipPlaFork provenance and adaptation map

The fixed source is ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, file
`source/ZipPla/CatalogForm.cs`.  The structure originated in upstream commit
`8ea492821efa95ac66483246c5b17fa71b400f0e`; thumbnail concurrency is one at
the fixed revision.  The following control flow is directly structurally
ported and is therefore recorded as AGPL-3.0-or-later-derived:

| Upstream class / method | Adopted behavior | NivisViewer destination |
|---|---|---|
| `ThumbViewerItem.LoadAsync`, `ThumbViewerItem.Clear` | One thumbnail load lifecycle and explicit release outside the useful region. | `_ViewerPageThumbnailJob`; `ViewerPageListRuntime._drive`, `set_visible`, byte-bounded `_put_cache` LRU |
| `ThumbViewer.PaintPart`, `ThumbViewer.DrawItem` | Materialize/publish only items required by the painted region; update the completed item locally. | `ViewerWindow._update_page_list_visible_work`, `_on_page_list_thumbnail_ready`; `ViewerPageListModel.set_thumbnail` |
| `ThumbViewer.preRenderScroll`, `ThumbViewer.OnMouseWheel` | Re-evaluate the useful range after scrolling rather than filling the book. | `ViewerWindow._schedule_page_list_visible_work`; visible-first `request_visible_pages` with row-mapped directional read-ahead |
| `ThumbViewer.SilentSet` and data/show-index mappings | Separate lightweight item identity from the displayed row and provide direct mapping. | `ViewerPageListModel.page_index_at`, `row_for_page`, filtered mapping |
| `ThumbViewer.Clear` | Dispose thumbnail ownership when the view/book no longer needs it. | `ViewerPageListRuntime.cancel` / `shutdown`; hidden state releases the source clone but retains the bounded LRU; `ViewerPageListModel.clear` |
| `CatalogForm.bmwMakePreview_RunWorkerStarting` lightweight item setup | Metadata rows do not eagerly own decoded images. | `ViewerPageListModel.rowCount` / `data` |

NivisViewer's Qt model, book/spec generations, byte accounting, source forks,
cooperative cancellation, PDF target render, GUI-only pixmap upload,
presentation-commit staging and callback-drained source lifetime are independent
Python/Qt adaptations.  ZipPlaFork's paint-time task creation, static global
semaphore, 33 ms sleep, GDI drawing/bitmap code and alternate-data-stream cache
were not copied.  License text and upstream notices remain in
`licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`; the notice
mapping is also recorded in `THIRD_PARTY_NOTICES.md`.

### 13.6 Removed old PageList ownership

The following production structures were removed rather than left as a feature
fallback:

| Old structure | Result |
|---|---|
| `QListWidget` plus one `QListWidgetItem` for every page | Replaced by `QListView` + `ViewerPageListModel`; unfiltered row lookup allocates no page-index tuple/reverse map and creates no per-page item objects. |
| Linear current-page and thumbnail item scans | Replaced by `row_for_page` / `page_index_at` mappings. |
| `PageThumbnailProvider.create_icon` GUI smooth-scale of the full decoded image | Class removed.  Worker publishes a final-size QImage; GUI performs only accepted pixmap/icon upload. |
| PageList mutation from `ImageCache.pageLoaded` | Removed.  PageList has its own source, queue, artifacts and callbacks for every supported book source. |
| `_page_list_dirty`, eager `_rebuild_page_list`, `_update_page_list_thumbnail` | Removed.  Hidden state clears model/runtime ownership; visibility reconstructs only virtual metadata and the current work order. |
| Icons accumulated for every page visited while the list was ever visible | Replaced by visible-row-only icons; bounded read-ahead images stay in the runtime LRU and are lazily republished on visibility. |

The Browser thumbnail provider/cache is a separate subsystem and was not
changed.  `ImageCache` and the prepared-display scheduler remain only for the
main non-ZIP Viewer until the ranked folder/external/PDF runtimes replace them;
PageList is no longer a reason to preserve those structures.

### 13.7 Direction-aware PageList read-ahead correction

The source comparison above uses the fixed ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, specifically
`source/ZipPla/CatalogForm.cs`: `ThumbViewer.PaintPart` identifies the visible
portion and `preRenderScroll` re-evaluates work as the scroll position moves;
`ThumbViewerItem.LoadAsync` keeps thumbnail execution to one worker lane.  The
integration adopts that visible-window/replaceable-order boundary, then adapts
it to Qt with an explicit visible prefix, one-viewport forward read-ahead, a
half-viewport rear range, 96/48 row caps, and a short idle grace before the
prefetch lane begins.  Filtered windows are computed in view-row space and
mapped through `ViewerPageListModel.page_index_at`, so page-number gaps do not
change the scroll distance.  This is not all-pages eager work, and it does not
reuse the full-page raster cache.

Offscreen synthetic tests now cover visible-before-prefetch ordering,
forward/back cache reuse without duplicate decodes, long-jump reprioritization,
filtered-row mapping, viewport resizing, hidden source release, and the hard
cache-byte bound.  These checks establish scheduler/cache behavior only; they
do not establish native Windows scrolling feel.

In the deterministic three-visible/three-ahead forward-and-return fixture,
the first ahead batch reached **3 ready thumbnails**.  Scrolling forward and
back issued **6 cache hits, 9 misses, and 9 unique decodes** for pages 3--11;
the returning visible rows were republished from QImage cache without another
source decode.  A separate 1,500-pixel synthetic artifact fixture exercised
the production default and stayed at or below **32 MiB** while evicting older
LRU entries.  These are offscreen runtime measurements, not native UI timing.

### 13.8 Offscreen PageList A/B

`scripts/benchmark_viewer_page_list_runtime.py` uses offscreen Qt and one real
temporary stored JPEG ZIP.  A is a benchmark-only reproduction of the removed
PageList ownership (2,000 eager items, full-size decoded QImage presented to a
GUI smooth-scale/upload callback); it is **not** the superseded Viewer frame
engine.  B calls the production `ViewerPageListModel` and
`ViewerPageListRuntime`.  The physical ZIP contains one deterministic entry
mapped to 2,000 logical ids so both sides read identical 60,629-byte payloads.
The image is 1,600 x 2,400, thumbnail edge 160, visible range six, scroll order
six positions, artificial cancellable entry delay 8 ms, and B QImage budget
204,800 bytes.  No window was shown and no native input or external GUI was
used.

Detailed counters and worker-completion timestamps are disabled in the
BookSession production runtime.  The benchmark and focused runtime tests opt
in through `collect_metrics=True`; no timestamp log or per-job metrics object
churn is left on the normal Viewer path.

| Scenario | A / B elapsed ms | A / B row build ms | A GUI scale ms / B | A work items / B jobs | A / B queued callbacks | A / B successful transit decodes | A / B ZIP reads (bytes) | A / B QImage outputs | A / B QPixmap uploads |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hidden 2,000-page book | 8.511 / 0.295 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| first visible range | 189.678 / 85.298 | 6.826 / 0.127 | 28.858 / 0 | 6 / 6 | 6 / 6 | 0 / 0 | 6 / 6 (363,774 / 363,774) | 6 full / 6 target | 6 / 6 |
| rapid scroll to final range | 1,140.669 / 84.913 | 6.639 / 0.114 | 157.398 / 0 | 36 / 7 | 36 / 7 | 30 / 0 | 36 / 6 (2,182,644 / 363,774) | 36 full / 6 target | 36 / 6 |
| current-frame pause/resume | 188.255 / 115.009 | 6.767 / 0.109 | 26.259 / 0 | 6 / 7 | 6 / 7 | 0 / 0 | 6 / 6 (363,774 / 363,774) | 6 full / 6 target | 6 / 6 |
| six-range memory pressure | 1,157.438 / 550.485 | 6.755 / 0.117 | 167.346 / 0 | 36 / 36 | 36 / 36 | 30 / 30 | 36 / 36 (2,182,644 / 2,182,644) | 36 full / 36 target | 36 / 36 |

B's rapid and pause cases each submitted one cancelled job and rejected one
stale callback; rapid scrolling produced no successful transit decode.  While
the current-frame pause was held, its submitted-job count stayed `1 -> 1` and
entry reads stayed `0 -> 0`; after resume it restarted the requested work.

| Scenario | A / B retained pages | A icon bytes / B icon+QImage bytes | B QImage cache pages / bytes / evictions | A / B working-set delta MiB | A / B sampled peak delta MiB | B source open/fork/close |
|---|---:|---:|---:|---:|---:|---:|
| hidden | 0 / 0 | 0 / 0 | 0 / 0 / 0 | 0.680 / 0.062 | 0.680 / 0.062 | 1 / 0 / 1 |
| first visible | 6 / 6 | 307,200 / 547,840 | 2 / 136,960 / 4 | 3.988 / 1.547 | 14.965 / 1.547 | 2 / 1 / 2 |
| rapid scroll | 36 / 6 | 1,843,200 / 547,840 | 2 / 136,960 / 4 | 3.578 / 0.984 | 14.559 / 0.980 | 2 / 1 / 2 |
| pause/resume | 6 / 6 | 307,200 / 547,840 | 2 / 136,960 / 4 | 1.797 / 1.238 | 12.781 / 1.238 | 2 / 1 / 2 |
| memory pressure | 36 / 6 | 1,843,200 / 547,840 | 2 / 136,960 / 34 | 4.164 / 3.863 | 15.148 / 3.859 | 2 / 1 / 2 |

The retained-byte columns are conservative but not symmetric: A counts the
icons owned by the removed list but not the legacy `ImageCache` source raster;
B counts both visible icons and its QImage cache.  Working-set samples are
process-wide and affected by allocator retention/order.  They support bounded
ownership but are not a real-device measurement.

Every successful A ZIP read materialized one entry `BytesIO`; every successful
B read did the same and the current `ZipImageSource.open_qimage_at_most` added
one `BytesIO.getvalue()` full-payload copy.  Thus B performed 6 extra full
copies in the visible/pause cases and 36 in the memory-pressure case.  This is
reported as the remaining PageList source bottleneck rather than hidden by
fallback.  Both sides intentionally recorded zero paint events because the
benchmark measures artifact publication without showing a view; request to
actual screen paint still requires real-device confirmation.

### 13.8 Validation, remaining risk and next boundary

The major contracts cover virtual mapping, latest visible-order replacement,
one active job, pause/cancel/stale rejection, byte-budget/hidden cleanup,
clone close after queued callback drainage, and presentation-atomic book-list
switching.  Related offscreen regression includes BookSession, all built-in
source families, PDF, Viewer presentation/rendering and ZIP runtime.  The
final collection contains 1,440 tests and all pass under controlled process
boundaries: 1,348 tests in 81 independent file processes, all eight
`test_sprint15_models.py` cases with an explicitly created offscreen
`QApplication`, and all 84 application-controller/navigation-follow-up nodes
in independent processes.  The combined application-controller and
navigation-follow-up files retain their existing shared-Qt-state teardown
stall.  One application-controller node also timed out once in the automated
node loop and passed immediately on a direct isolated retry; no assertion
failed.  These conditions are reported rather than treating process teardown
as production evidence.

Remaining risks are: cooperative cancellation cannot interrupt every Pillow
codec after native decode has entered; the visible QIcon set is range-bounded
but Qt pixmap memory is not part of the QImage byte counter; first PageList use
opens one additional source/document session; filtering is O(all page names)
only when filter text changes; and ZIP JPEG thumbnail target decode still
materializes/copies the whole compressed entry once.  Offscreen timings do not
prove physical-device responsiveness.

The next structural replacement remains the source-independent
`FolderRasterBookRuntime` / page artifact record.  Its boundary starts at the
main Viewer's folder/single-image request and ends at complete-frame publish;
it may remove the `ImageCache -> ViewerRenderTask -> prepared display` split.
It must not absorb PageList, Browser thumbnails, `ViewerPresentationState`, or
the ZIP runtime, and it was deliberately not started in this unit.

## 14. Balanced modernization audit and decoded-source/display-frame split

### 14.1 Corrected objective and fixed baselines

The objective is not to turn NivisViewer into the same application as
ZipPlaFork.  ZipPlaFork remains the performance reference for archive,
work-order and page-artifact lifetime decisions; NivisViewer remains the UX
and modern-Qt reference where its contract is stronger.  The four decisions
used below are:

- **A / ZipPlaFork**: adopt the upstream structure because it is clearly
  simpler, faster and sufficiently safe;
- **B / NivisViewer**: retain the NivisViewer structure because its UX,
  feature semantics or structured lifetime is stronger;
- **C / Hybrid**: use the fast ZipPlaFork internal principle behind a
  NivisViewer external/Qt contract;
- **D / New design**: neither implementation is sufficient for a modern x64,
  high-DPI, large-image Viewer.

The NivisViewer audit started from clean worktree HEAD
`1445d106bdfc6b0445295fbdac311628836feb0c` (`Replace the viewer page list
with a virtual, book-scoped runtime`).  On 2026-08-20, ZipPlaFork remote
`HEAD` and `refs/heads/master` still resolved to the fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.  Its license remains
AGPL-3.0-or-later and the preserved text/notices remain in
`licenses/ZipPlaFork/AGPL.txt`, `licenses/ZipPlaFork/About.txt`, and
`THIRD_PARTY_NOTICES.md`.

### 14.2 Actual ZIP call sequences used for the comparison

ZipPlaFork's fixed-revision cold page path is:

```text
ViewerForm input / command
  -> moveToNextPage / MoveToPreviousPage / movePageNatural
  -> currentPage + showCurrentPage
  -> SetBackgroundModeIfPausing
       -> if paused: SetBackgroundMode
            -> priorityLevel(current unit, index-increasing next,
                             index-decreasing previous, remaining)
            -> BackgroundMultiWorker.SetWorksOrder
       -> if active: keep the running job
  -> bmwLoadEachPage_DoWork (one page worker)
  -> PackedImageLoader.OpenImageStream / OpenInnerImageStream
  -> ImageLoader.GetFullBitmap + EXIF/filter/orientation
  -> GetResizedSize + BitmapResizer / post-filter
  -> VirtualBitmapEx
  -> BackgroundMultiWorker completion callback
  -> if currentPage changed while active: SetBackgroundMode for latest current
  -> SetNewResizedImage / ResizedImageArray
  -> showCurrentPage + pbView_Paint / pbView_PaintToCanvas
```

The production NivisViewer ZIP path at the start of this audit was the
following historical path (its paint gate is superseded by sections 19--20):

```text
wheel / shortcut / slider / virtual PageList
  -> ViewerPageNavigationController + PageModel
  -> ViewerWindow._refresh_view
  -> ViewerPresentationState.request_frame (requested only)
  -> ViewerWindow._zip_runtime_request
  -> ZipRasterBookRuntime.request
  -> _ZipRasterFrameStore lookup / latest work order
  -> _drive(current, then paint-gated next, then previous)
  -> _ZipRasterUnitJob.run (one display-unit worker)
  -> ZipImageSource persistent ZipFile entry read
  -> QImageReader or Pillow decode + EXIF/filter
  -> calculate_spread_layout + render_qimage
  -> queued ZipRasterBookRuntime._on_job_completed
  -> GUI-thread QPixmap.fromImage
  -> ViewerWidget.commit_display_ready_frame
  -> ViewerPresentationState.commit_frame
  -> slider/status/PageList/history/progress projection
  -> ViewerWidget.paintEvent
  -> framePainted -> release_prefetch
```

NivisViewer already has one production ZIP engine for single/spread, LTR/RTL,
rotation, resampling, image adjustment, wide split, magnifier source and PageList
visibility.  `ZipPlaCompatibleRasterPath`, the legacy `ImageCache` decode,
prepared-display scheduler and normal `ViewerRenderTask` are not reachable from
the ZIP main-display path.  PageList uses its own virtual model, runtime,
worker/cache and independently opened thumbnail source.

### 14.3 Strength/weakness comparison and A/B/C/D decision

| Subsystem | ZipPlaFork: strengths / weaknesses | NivisViewer: strengths / weaknesses | Modern third option and decision |
|---|---|---|---|
| Archive access | **+** One book-lifetime loader/index and one entry stream per page job; no managed whole-entry byte array on its normal ZIP path. **-** Open/index/sort is tied to a large synchronous `ViewerForm.OpenFile`. Replacement/reload waits before replacing the loader, but Viewer close has no explicit worker-drain/resource-graph shutdown. The direct GDI stream path also relies on a bitmap remaining valid after the entry stream is disposed. | **+** Persistent `ZipFile`, O(1) `NameToInfo/getinfo`, book epoch, cancellable request and callback-drained deferred close. **-** current JPEG path materializes a `BytesIO` and then a `getvalue()` handoff. | **C.** Keep `BookSession`/deferred close and owned-pixel lifetime; retain the ZipPla page-job/entry-stream principle without copying its GDI stream-lifetime assumption. A single seekable payload decoder remains a later optimization. |
| Page loading | **+** Entry read through display bitmap is one literal worker job. **-** Page arrays and UI form own many responsibilities. | **+** ZIP uses one display-unit QRunnable and publishes only terminal units. **-** Other raster sources still use the legacy multi-stage pipeline. | ZIP **C, already adopted**. A source-independent raster runtime is the next cross-format design. |
| Decode | **+** Direct stream-to-GDI decode and retained prefiltered source. **-** `System.Drawing`, .NET 4.5.2 and old external WebP/Susie/ffmpeg choices are not a modern decoder strategy; the stream/bitmap lifetime assumption is unsafe to translate literally. | **+** target-sized JPEG via `QImageReader`, EXIF auto transform, Pillow fallback and WebP support. **-** extension/backend branching is embedded in source methods; valid JPEG still has a compressed-payload copy. | **D.** Future capability-driven decoder registry with owned output pixels; do not translate GDI+ or Python `QIODevice` callbacks literally. |
| Resize | **+** Reuses retained source and creates resized output in the page job. **-** CPU/GDI-oriented implementation and manual bitmap lifetime. | **+** Qt/Pillow resampling modes, physical DPR target and same ZIP unit job. **-** source and layout frame shared one invalidation key at audit start. | **C/D.** Keep Qt render semantics; separate decoded source from display variants (implemented below). |
| Worker / scheduler | **+** One Viewer page job and replaceable `SetWorksOrder`; current/next/previous simplicity. **-** active stale work is not cancelled, reordering waits for its completion, all-page ordering/sorting remains, and `WorkSetGuid` is effectively `Guid.Empty` rather than a generation fence. Resize may use internal parallel execution, so this is not a one-compute-thread design. | **+** One raster lane, latest current, cooperative entry cancel, request/source epoch stale fence, 4 + 1 startup runway and seamless lazy book-wide continuation. **-** legacy non-raster formats still have load/render/prepared stages. | ZIP/Folder **C, superior Hybrid**. Keep the completion-order principle, not `BackgroundWorker`, ineffective generation or thread-priority details. |
| Prefetch | **+** One page job at a time and priority is rebuilt around current; exact `ResizedImageArray` hits bypass new work. **-** priority is always current then index-increasing neighbor then index-decreasing neighbor, not navigation-direction aware; active stale work finishes first, work eventually covers the book, and there is no paint acknowledgement. | **+** accepted commit releases a direction-aware four-forward/one-reverse complete-unit priority prefix, then the one worker continues the lazy book-wide iterator under combined byte admission. Exact-frame hits bypass worker/upload; paint remains an ownership/UI boundary. | **C.** Preserve Nivis complete-unit, direction-aware, byte-driven scheduling while retaining ZipPla's simple completion-boundary frontier. |
| Cache | **+** `PreFilteredImageArray` and `ResizedImageArray` separate source/display while sharing page lifecycle; an exact ready hit paints without a worker. **-** array ownership and manual dispose are form-wide and tied to GDI objects. | **+** deterministic unit/byte budget and ready frame worker bypass. **-** `_UnitKey` previously coupled decoded QImage to viewport/DPR/rotation/zoom frame identity. | **D.** Book-scoped decoded-source store plus layout-specific frame store, with paired validity and one combined budget (implemented). |
| Memory management | **+** source plus resized bytes are counted and least-useful pages are reduced. **-** budget changes with `ActiveForm` and volatile physical memory, eviction runs from an approximately 500 ms GUI timer, several pages can remain over the nominal budget, and scaled/source artifacts for one page are discarded together. | **+** explicit byte/unit settings and protected current complete frame. **-** Qt/plugin/transient compressed allocations remain unobservable; PageList has a separate budget. | **D.** Deterministic combined ledger now covers ZIP QImage+QPixmap without double-counting implicit shares; future runtimes need reservations and soft OS-pressure input. |
| Page state | **+** simple current page. **-** `currentPage`, trackbar and status can move before the target bitmap is ready; old CPU canvas is gray-masked as loading. | **+** `ViewerPresentationState` separates requested/displayed and commits slider/status/history/progress with a complete frame. **-** bookmark/export commands still use the requested `PageModel.current_index` in a few paths. | **B.** Preserve PresentationState; later move bookmark/export defaults to committed state. |
| Display commit | **+** completed resized bitmap is inserted in one UI completion. **-** no explicit epoch/layout/DPR token or immutable complete-spread transaction. | **+** complete single/spread atomic swap, old-frame retention, stale rejection and GUI-only QPixmap. **-** commit projects a broad action refresh. | **B.** Keep NivisViewer. Optimize projection only after profiling. |
| Page list / thumbnail | **+** visible-region loading and release rather than eager full-book thumbnails. **-** paint-time Catalog machinery, GDI ownership and a static semaphore shared by all ThumbViewers; cancellation is not passed into an active `GetThumbnail`. | **+** virtual Qt model, visible margin, O(1) mapping, independent source/cache/worker, pause/cancel, byte budget. **-** first use opens a second archive/document session. | Historically **D**, now **B / maintain**. NivisViewer's completed modern design is stronger than direct Catalog translation. |
| Input handling | **+** compact direct command mapping. **-** ready-frontier navigation can prevent rapid input from reaching a final cold page. | **+** wheel/shortcut/slider/PageList converge on one navigation/presentation boundary; rapid input coalesces to latest request. **-** requested PageModel moves ahead of displayed by design, so non-presentation commands must use the right owner. | **B/C.** Keep Nivis UX and ZipPla-style internal work-order rebuild. |
| Spread / LTR / RTL | **+** ordinary numerical `M=2` pages share level-0 priority; natural `NextPage`/`PreviousPage` generally waits for the current band before entering one cold band. **-** fixed numerical worker bands can disagree with wide/cover navigation topology, and logical current/loading paint are intertwined. | **+** pure `PageModel.spread_at` topology, complete-unit publication, wide split and RTL half order. **-** an initially unknown wide page can cause unit reconstruction. | **B**, while retaining only the useful complete-neighborhood priority principle and combining it with source reuse. |
| Rotation / EXIF | **+** orientation/filter is integrated in the page job. **-** old GDI/manual DPI model. | **+** QImageReader/Pillow EXIF and worker-side rotation; rotation is in presentation tokens. **-** previously forced archive re-decode because source/frame invalidated together. | UX **B**, artifact lifetime **D implemented**. |
| Magnifier | **+** `resetMagnifier`/`MagnifierCanvas`/`bwMagnifierMaker_DoWork` normally reuse `PreFilteredImageArray` and avoid another archive decode. **-** a separate worker/cache can contend with main resize and has no book epoch/request serial/DPR contract. | **+** retained source, selectable crop, quality mode, stale render key and full-resolution request. **-** audit found direct-display mode rejected the final magnifier render task. | **C/D.** Reuse the ZipPla retained-source principle behind the Nivis crop/UX and modern stale fence; direct-mode interactive render is enabled below. |
| Zoom / fit | **+** source/resized separation supports new resized output. **-** UI/bitmap code is tightly coupled. | **+** fit modes, manual zoom/pan and multiple resampling modes. **-** prior source/frame key caused repeated decode for variant changes. | UX **B**, source/display lifetime **D implemented**. |
| DPI | **+** manifest/manual DPI support for its WinForms era. **-** not a Qt6/fractional-DPR design. | **+** `devicePixelRatioF`, physical render targets, DPR-stamped pixmaps and stale-DPR rejection. **-** native upload memory is not fully measurable. | **B.** Do not port old WinForms DPI compensation. |
| Book switch | **+** serializes loader replacement after the old page job returns. **-** synchronous/open-form coupling; failed replacement clears current paths/slider instead of rolling back the old presentation. | **+** pending replacement, failed-open rollback, old displayed state, epoch and retired runtime/source drainage. **-** open start currently clears old runtime cache, so failed replacement may need re-decode after recovery. | **B.** Preserve Nivis lifetime; consider delaying old artifact retirement separately. |
| Close / shutdown | **-** `ViewerForm_FormClosing` saves settings but has no explicit main-worker drain or loader/page/magnifier/canvas resource-graph shutdown; `PackedImageLoader.Dispose` exists but is not explicitly reached from Viewer close. | **+** runtime/PageList/PDF drain, cooperative cancel and final source close after worker+callback lifetime. **-** native decoder cancellation is not always immediate. | **B.** NivisViewer is unambiguously stronger here. |
| Error handling | **+** a broken entry becomes a display-sized `VirtualBitmapEx` error artifact, preserving page completion. **-** broad catches have no typed error/epoch/serial ownership. | **+** typed terminal error frame, complete spread semantics, no feature engine fallback or double commit. **-** Qt failure followed by Pillow can reread the entry. | **B** for semantics; retain the upstream error-artifact principle while payload reuse is later **D** work. |
| Folder / single image | One Viewer loader structure covers it. | Modern UX/features, but main display still uses `ImageCache -> ViewerRenderTask -> prepared display`. | **C**, rank 2: source-independent `RasterBookRuntime`, while retaining Nivis presentation and widget contracts. |
| ZIP / CBZ | Strong archive and one-job principles, weaker presentation/cancel/modern decoder boundaries. | One production runtime, persistent archive, current-first lane, atomic frame, virtual PageList; remaining payload copy and source/frame coupling at audit start. | **C/D.** Keep the Hybrid engine; implement the artifact lifetime split now. |
| RAR / 7z / CBR / CB7 | Packed loader owns the book, but direct SharpCompress/GDI translation would import old constraints. | Entry index is retained, but each page may start a 7-Zip/WinRAR process and materialize stdout; solid archives are costly. | **D**, rank 3: book-scoped, cancellable, solid-aware external-archive runtime. |
| PDF | Integrated into the old loader family. | `PdfiumService` already owns document lifetime, priority, dedup, target render, cancel and close barrier; outer legacy Viewer stages remain. | Service **B**, outer pipeline **C**, rank 4 `PdfBookRuntime`. |
| UI-thread work | UI completion and GDI canvas paint are central; open/index/sort can be synchronous. | ZIP keeps archive/decode/resize off GUI; GUI does QPixmap upload, atomic state projection and paint. PageList scaling moved off GUI. | **B.** Keep Nivis Qt affinity/ownership; only the archive/work-order internals are Hybrid. Measure upload/paint before considering GPU/QRhi. |
| WebP / AVIF / future formats | WebP relies on old external integrations; no AVIF design. | WebP has Qt/Pillow paths; installed decoder capability and declared extensions are not yet unified. | **D**, rank 5 capability-driven decoder registry. GPU is conditional on measured upload/paint dominance. |

### 14.4 Ranked structural work

| Rank | Work unit | Real-device / UX impact | Complexity and compatibility |
|---:|---|---|---|
| 1 | ZIP decoded-source / display-frame lifetime split, including the direct-mode magnifier boundary | Removes repeated ZIP read/decode on resize, DPI, rotation, zoom, wide/spread reconstruction and magnifier resolution transitions; restores the ZIP magnifier render. | Medium-high. Preserves the existing ZIP runtime, presentation, spread and decoder contracts. **Implemented in this section.** |
| 2 | Source-independent `RasterBookRuntime` for folder/single images | Removes the two-stage legacy loader/render/prepared pipeline from a common Viewer path. | High. Must preserve file-change semantics, magnifier and all Viewer presentation features. **Implemented in section 15.** |
| 3 | Book-scoped RAR/7z runtime | Potentially removes process-per-entry and repeated solid-archive traversal, the largest format-specific cold cost. | Very high resource/cancellation/backend risk. |
| 4 | `PdfBookRuntime` around the retained `PdfiumService` | Removes outer cache/worker/conversion stages while retaining target rendering. | High rotation/DPR/annotation/shutdown compatibility risk. |
| 5 | Capability-driven decoder registry | Modern WebP/AVIF/native backend selection and one-payload fallback. | Medium-high; lower current ZIP page-turn impact than ranks 1-4. |

GPU compositing is not ranked until measurement shows that GUI-thread
`QPixmap.fromImage`/paint, rather than archive read and decoder work, dominates
the physical-device interval.

### 14.5 Implemented rank 1: page-artifact lifetime ownership

`app/zip_raster_book_runtime.py` now separates two lifetimes:

1. `_ZipRasterSourceStore` owns immutable decoded `QImage` sources by book
   epoch, source identity, image id, pixel-affecting adjustments, decoded pixel
   size and full/preview status.  A preview is reusable only when it meets the
   later target; a full source satisfies every later layout.
2. `_ZipRasterFrameStore` owns layout-dependent complete `QPixmap` frames by
   display unit and full render spec.  Cached frames reference source keys but
   do not own/count a second QImage copy.

The runtime resolves source hits before creating `_ZipRasterUnitJob`.  A source
hit still creates one worker job because resize/rotation/display preparation is
off the GUI thread, but it performs no archive entry read and no image decode.
An exact frame hit continues to create no job, callback or QPixmap.

The two stores share the configured book byte budget.  Decoded QImage bytes and
display QPixmap estimates are counted once; source eviction also removes frame
variants that would otherwise expose a pixmap without its magnifier source.
Current complete source/frame ownership is protected and may soft-overflow the
budget for one exceptional page rather than blanking the Viewer.
Prefetch admission now estimates both a missing decoded source and its physical
display frame instead of looking only at QPixmap bytes.  It does not discard a
completed artifact in advance, so cancellation cannot exchange a ready frame
for work that never commits.  This is a soft retained-artifact budget: transient
decoder/Pillow/upload allocations remain outside the ledger.  Unknown lazy page
dimensions use the largest observed source in the same book as a pragmatic
estimate; a highly dissimilar following page can therefore still create one
transient overshoot before normal eviction/failure suppression takes effect.
Only the source variant matching the active current adjustments is protected;
old brightness/contrast/gamma variants remain evictable.

`invalidate_layout()` now cancels the old work order and clears only display
frames.  Book close/switch or explicit `clear_artifacts=True` clears both
stores.  Therefore resize, DPR, rotation, zoom, wide/spread reconstruction and
magnifier transitions reuse a sufficient source and upgrade a preview to full
resolution at most once.

`app/viewer_widget.py` continues to reject normal legacy render jobs while a
book runtime owns direct display, but now permits `purpose="magnifier"`.  This
is a NivisViewer UX projection over the committed runtime QImage, not a switch
back to the old main Viewer engine.  The stale `key.purpose` reference in the
direct-mode prepared-display guard was also reduced to an unconditional direct
mode return.
`app/viewer_window.py` marks only preview ZIP sources with `rendered_size` for a
full-raster upgrade.  A full ZIP source no longer enters the PDF-only resolution
request branch and wait forever for a handler that intentionally ignores ZIP.
Direct-mode magnifier crops use the widget-owned one-thread lens pool rather
than the book runtime's single Viewer coordinator lane.  Their stale result is
still rejected, but a non-interruptible crop can no longer queue ahead of a
current-page archive/decode job.

Removed/replaced ownership in this unit:

| Previous structure | Result |
|---|---|
| `_CachedFrame` owned both source QImage and layout QPixmap under one `_UnitKey` | Replaced by source keys plus independent source/frame stores. |
| `invalidate_layout()` called `cancel(clear_artifacts=True)` | Replaced by display-only invalidation; decoded source remains book-scoped. |
| Frame byte accounting rediscovered/deduplicated source QImages inside each frame | Replaced by one source ledger plus pixmap-only frame ledger. |
| A source could not be reused across layout keys | Replaced by preview sufficiency/full-resolution lookup before decode. |
| Direct display rejected every `ViewerRenderTask`, including the magnifier | Normal main renders remain rejected; the explicit magnifier crop render is admitted. |

### 14.6 Provenance boundary

The performance principle is informed by ZipPlaFork fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:

- `source/ZipPla/ViewerForm.cs` fields `PreFilteredImageArray`,
  `OriginalImageInfoArray`, `ResizedSizeArray`, `ResizedImageArray`;
- `source/ZipPla/PackedImageLoader.cs` constructor (`:283-330`),
  `PackedImageEntry` (`:375-400`), `getZipArchiveEntries` (`:1197-1243`),
  `OpenImageStream` / `OpenInnerImageStream` (`:1781-1867`) and
  `SeekableStream` (`:2731-2890`);
- `source/ZipPla/ImageLoader.cs` `GetJpegOrientation` /
  `LoadRotateBitmap` (`:382-400`) and `GetFullBitmap(Stream)` (`:559-689`);
- `bmwLoadEachPage_DoWork` (`:3177-3550`) and its reuse of retained source;
- `NextPage` / `PreviousPage` (`:1903-2002`) and complete-spread gating;
- per-entry error artifact creation (`:3565-3579`);
- page completion/publication (`:5371-5415`);
- `SetNewResizedImage` (`:5451-5468`);
- `ReduceUsingMemory` (`:5471-5524`);
- `priorityLevel` (`:5586-5605`);
- paint/loading-mask paths (`:6768-6858`, `:6990-7321`);
- `source/ZipPla/GenerarClasses.cs`,
  `BackgroundMultiWorker.SetWorksOrder` (`:247-264`) and completion/next-work
  selection (`:266-303`).

The existing `ZipRasterBookRuntime`, unit job and work-order/cache ranking remain
the recorded direct AGPL-3.0-or-later structural port.  The new
`_ZipRasterSourceStore`, resolution-sufficiency lookup, paired source/frame
validity, combined Qt byte ledger, PresentationState integration and
direct-mode magnifier boundary are a new Python/Qt design rather than a
line-for-line translation.  No new dependency or license was added.

### 14.7 Validation and remaining boundary

The focused contract protects: preview reuse for a smaller layout, one-time
full-resolution upgrade, reuse of that full source for later DPR/layout work,
GUI-thread source attachment, complete spread/frame behavior, one ordered job
lane, stale rejection, budget admission, shutdown, and magnifier task tracking
while direct display is active.  Detailed offscreen A/B numbers are recorded
by `scripts/benchmark_zip_runtime_layout_reuse.py`; they remain supporting
evidence and do not establish physical-device responsiveness.

Final offscreen validation used independent pytest processes.  The focused
Viewer/runtime set passed 227 tests.  The complete suite was split into three
fresh processes and passed 651 + 507 + 283 = 1,441 tests.  One 20 ms
fullscreen-hide timer assertion missed its 30 ms deadline during an earlier
concurrent run; the test passed alone and its entire 507-test partition passed
on two clean reruns, so no product change was made to hide that scheduling
flake.  Syntax/import checks and `git diff --check` also passed.  One duplicate
subagent split process remained after its agent was interrupted; after its PID,
parent and executable were verified, only that test process was stopped and the
same 651-test partition passed sequentially.  No spawned test/benchmark process
remains.  The unrelated pre-existing ComfyUI Python process was neither stopped
nor modified.

The controlled default run used one temporary ZIP_STORED detailed JPEG,
2,400 x 3,600 pixels, 6,592,317 compressed bytes, a 1,200 x 800 viewport and a
256 MiB book budget.  Each side ran in a fresh offscreen child process with its
own `ZipImageSource` and `ZipRasterBookRuntime`.  A reproduced the old
layout-invalidation ownership by calling `cancel(clear_artifacts=True)` before
every variant; B used production `invalidate_layout()` and retained decoded
sources.  Neither side created a `ViewerWindow`, native input or an external
GUI application.

| Operation | A old clear-all ms / reads / decodes | B source-reuse ms / reads / decodes |
|---|---:|---:|
| Initial decoder-sized fit | 60.154 / 1 / 1 | 58.621 / 1 / 1 |
| Smaller resize | 43.725 / 1 / 1 | 2.106 / 0 / 0 |
| Full-resolution rotation upgrade | 95.530 / 1 / 1 | 95.260 / 1 / 1 |
| DPR/manual-zoom variant | 83.632 / 1 / 1 | 13.036 / 0 / 0 |
| Full-source magnifier-equivalent variant | 84.692 / 1 / 1 | 12.951 / 0 / 0 |
| **Total** | **367.824 / 5 / 5** | **182.047 / 2 / 2** |

| Counter / memory | A old clear-all | B source reuse | Difference |
|---|---:|---:|---:|
| ZIP entry bytes read | 32,961,585 | 13,184,634 | -19,776,951 (-60.0%) |
| Observable whole-payload API copies / bytes | 2 / 13,184,634 | 1 / 6,592,317 | -1 / -6,592,317 |
| Source-cache hits / misses | 0 / 5 | 3 / 2 | +3 hits / -3 misses |
| Worker jobs / queued callbacks | 5 / 5 | 5 / 5 | 0 / 0 |
| Display QImage outputs / `QPixmap.fromImage` | 5 / 5 | 5 / 5 | 0 / 0 |
| Final source / frame / total cache bytes | 25,920,000 / 8,640,000 / 34,560,000 | 25,920,000 / 8,640,000 / 34,560,000 | identical |
| Retained source pages / frame units | 1 / 1 | 1 / 1 | identical |
| Sampled peak working-set delta | 166.141 MiB | 97.703 MiB | -68.438 MiB |
| Process-lifetime peak delta | 170.156 MiB | 112.406 MiB | -57.750 MiB |
| End working-set delta | 97.191 MiB | 46.703 MiB | -50.488 MiB |

B is 2.020x faster (185.777 ms saved) for this five-layout sequence because it removes three ZIP
reads and decodes; it does not hide work by reducing render jobs, callbacks,
display QImages or pixmap uploads.  Final retained logical cache ownership is
identical.  The memory values are process-wide and allocator-sensitive, and
the magnifier-equivalent step measures source/display preparation rather than
an actual widget paint.  A separate offscreen integration contract confirms
that a direct-mode ZIP frame now completes the real magnifier task.

A final production-path supplement used `ViewerWindow ->
ZipRasterBookRuntime`, eleven 1,200 x 1,800 detailed JPEG pages (1,649,004
bytes per ZIP entry), an 800 x 600 viewport and explicit offscreen
`QWidget.render` paint.  It is not an old/new comparison; it checks the latest
path after the structural change.  All scenarios settled, every transit-page
decode count was zero, and ready/retained hits created no job, callback,
decode or QPixmap upload.

| Scenario | request→commit / paint ms | ZIP reads / decodes | jobs / stale | paints | retained pages | sampled peak delta MiB |
|---|---:|---:|---:|---:|---:|---:|
| cold forward | 15 / 15 | 1 / 1 | 3 / 0 | 1 | 3 | 4.582 |
| reversal | 15 / 15 | 0 / 0 | 4 / 0 | 1 | 4 | 0.035 |
| cold roundtrip | 0 / 0 | 0 / 1 | 5 / 1 | 2 | 3 | 2.602 |
| rapid final | 16 / 16 | 1 / 1 | 3 / 0 | 1 | 3 | 3.918 |
| retained roundtrip | 0 / 0 | 0 / 0 | 0 / 0 | 1 | 6 | 0.020 |
| ready | 0 / 0 | 0 / 0 | 0 / 0 | 1 | 3 | 0.012 |
| memory pressure | 15 / 15 | 0 / 0 | 1 / 0 | 1 | 1 | 0.043 |

The 0/15/16 ms values reflect offscreen event-loop/paint quantization and must
not be interpreted as physical-device page-turn latency.  Cancelled partial
entry bytes are also outside the source counter, as documented by the script.

Remaining known work includes the two compressed-payload materializations on
the normal JPEG path, Pillow/native decoder regions that cannot be interrupted
immediately, and Qt/plugin/native upload allocation outside the byte ledger.
A running high-quality magnifier `ViewerRenderTask` has no cooperative inner
cancel.  It is now isolated from the book Viewer lane, but can briefly contend
for CPU/memory bandwidth and shutdown still waits for its inner crop.  A
preview-to-full magnifier upgrade also uses `invalidate_layout()`, preserving
decoded sources but clearing unrelated ready neighbor frames; a targeted
`ensure_full_source` boundary should replace that broad frame invalidation.
Requested-page bookmark/export residue, broad post-commit action projection,
and all non-ZIP legacy main-display runtimes also remained at that point.  The
next structural boundary was rank 2, the source-independent
folder/single-image `RasterBookRuntime`; section 15 records its implementation.

## 15. Folder / directly-opened image RasterBookRuntime replacement (2026-08-20)

### 15.1 Scope and preserved product semantics

The performance target is the common Folder main-display path, not a WinForms
clone and not a change to NivisViewer's open behavior.  NivisViewer continues
to treat an image path as the selected starting page of its parent-folder book.
A folder containing one supported image is naturally a one-page runtime and
has no neighbor work.  Changing every image-path open into an isolated page
would remove existing next/previous navigation and is therefore outside this
performance replacement.  This is the deliberate resolution of the literal
"single image = one-page book" wording against the higher-level requirement to
preserve NivisViewer's established parent-folder navigation UX; no hidden
feature fallback is involved.

The start-of-work worktree was clean at NivisViewer HEAD
`925a17239296ace6b590ddcad469a7d61f022d9d`.  The fixed ZipPlaFork reference is
unchanged: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.  No
dependency or license was added.

### 15.2 Old production path

Before this replacement, both a Folder and an image path selected from that
Folder followed this sequence:

```text
path / folder snapshot
  -> FolderImageSource
  -> BookSession installs no viewer_runtime
  -> ViewerWindow._refresh_view
  -> decode-demand timer / ImageCache.preload_around
  -> _ImageLoadTask
  -> file full-byte read
  -> target JPEG QImageReader or WebP QImageReader or Pillow + EXIF
  -> ImageCache QImage + pageLoaded callback
  -> ViewerWindow._render_spread / display-demand timer
  -> ViewerWidget._prepare_display
  -> ViewerRenderTask
  -> resize / split / rotation
  -> queued GUI callback
  -> QPixmap.fromImage + prepared/render cache
  -> complete frame commit
```

The source and display stages used the same Viewer worker lane but were two
jobs with two callbacks and two cache owners.  `ImageCache` and
`ViewerWidget` independently evicted source and prepared artifacts, while
`ViewerWindow` reconciled their budgets and timers.  An exact prepared-frame
hit was fast, but a source-only hit still required the second worker.  A
running Folder Pillow/QImageReader decode could only be stale-rejected after it
returned.

### 15.3 New shared runtime contract

The existing ZIP execution core is now the source-neutral
`RasterBookRuntime`.  `ZipRasterBookRuntime` and the new
`FolderRasterBookRuntime` are thin source-type subclasses; they do not fork the
scheduler, stores, publication callback or shutdown path.  Neutral public
aliases live in `app/raster_book_runtime.py`; the implementation is temporarily
retained in its historical `app/zip_raster_book_runtime.py` module to keep the
already-tested ZIP import surface stable.  Moving the physical core module is
cleanup, not a second engine.

```text
BookSession (one source epoch)
  -> one RasterBookRuntime (Folder or ZIP source constraint)
ViewerWindow input
  -> ViewerPresentationState.request_frame
  -> RasterRequest(current, directional next, reverse neighbor)
RasterBookRuntime
  -> exact complete-frame lookup (ready hit: no worker)
  -> one active display-unit job
       -> cached sufficient decoded source, or file read + decode + EXIF/filter
       -> spread/split layout + rotation + physical-DPR resize
       -> one terminal display QImage result
  -> one queued GUI completion
       -> QPixmap.fromImage
       -> decoded-source store + complete-frame store under one budget
  -> ViewerWidget.commit_display_ready_frame
  -> ViewerPresentationState atomic commit and UI projections
  -> paint acknowledgement
  -> release next/previous prefetch
```

`invalidate_layout()` clears layout-dependent frames only.  A sufficient
decoder-sized preview is reused for smaller resize/DPR variants; one full
source upgrade satisfies later rotation, zoom, DPI and magnifier needs.  The
current complete frame and its active source remain protected, and prefetch is
admitted against the combined QImage/QPixmap ledger.  Exact ready hits emit the
frame synchronously without another worker, file read, decode, callback or
pixmap upload.

`BookSession` now retires every `RasterBookRuntime` through the same
callback-drain graph.  A Folder file handle is not kept for the book lifetime:
`FolderImageSource` closes it after the payload read and the runtime retains
only owned decoded pixels.  The old source remains alive until an in-flight
worker and its queued GUI callback have drained; a late result is rejected by
source epoch, identity and current work order.

### 15.4 Design decision table

| Boundary | ZipPlaFork strength used | NivisViewer strength retained / new design | Decision |
|---|---|---|---|
| Work order | One page worker and current-centered replaceable order | Navigation-direction neighbor order, request/source epochs and paint gate | **C / Hybrid** |
| End-to-end job | Decode, resize and display artifact belong to one work item | Qt worker affinity, physical DPR, modern decoders and complete spread transaction | **C / Hybrid** |
| Source/display lifetime | Retained source and resized image are separate reusable artifacts | Immutable QImage source plus GUI-owned QPixmap frame, combined deterministic byte budget | **D / New design** |
| Ready hit | Completed resized image bypasses worker | Atomic token validation and PresentationState commit | **C / Hybrid** |
| Page state / old frame | Complete page is the useful publication unit | requested/displayed separation, old-frame retention, atomic slider/status/history/progress | **B / NivisViewer** |
| High DPI / rotation / fit / zoom | Reuse source before producing another resized bitmap | Qt6 fractional DPR and Nivis feature/render contracts | **B/C** |
| Magnifier | Reuse retained full source | Nivis selection/crop UX and stale render key; only the lens projection task remains Widget-local | **C / Hybrid** |
| PageList | Separate visible-region thumbnail work | Virtual model, independent source/cache/worker and pause during current work | **B / NivisViewer** |
| Book switch / close | Do not dispose pixels still needed by a page job | pending replacement rollback, retired runtime/source graph and callback drain | **B / NivisViewer** |

### 15.5 Production ownership after replacement

Folder and directly-opened image parent books now use the runtime for single,
spread, LTR/RTL, split-wide, rotation, fit, manual zoom, image adjustments,
DPI/source upgrade, magnifier source and PageList-visible operation.  There is
no feature eligibility switch back to the old main engine.

The following old structures are removed from **Folder/single production main
display**, but not deleted globally:

- `_ImageLoadTask` / decoded `ImageCache` ownership;
- decode-demand and legacy raster-prefetch timers;
- normal-page `ViewerRenderTask`;
- prepared-display scheduler/cache and its independent eviction decisions;
- `ImageCache.pageLoaded` as a Folder display callback.

They remain explicitly for PDF, RAR/7z and custom/test `ImageSource` values
until those source families receive their own runtime.  The magnifier's
high-quality crop `ViewerRenderTask` also remains: it is an interactive
projection of the committed runtime source, not a fallback page engine.
PageList remains the independent `ViewerPageListRuntime` and thumbnail source;
it never consumes the main Folder source store.

Clipboard image and page-info commands formerly read `ImageCache` directly.
Runtime-backed books now project these values from
`ViewerWidget.displayed_source_snapshot()`, which uses the atomically committed
displayed page rather than a possibly newer requested page.  The legacy cache
lookup remains only as the other-format adapter.

### 15.6 Direct-port provenance

No new Folder-specific code was copied from ZipPlaFork.  Folder reuses the
already recorded AGPL-derived structural port in `RasterBookRuntime`:

| ZipPlaFork fixed-revision source | Adopted processing principle | NivisViewer implementation |
|---|---|---|
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, `:266-303`) | One active job selected from a replaced current-centered order | `RasterBookRuntime.request`, `_drive`, `_submit`, `_cancel_active_job` |
| `source/ZipPla/ViewerForm.cs`, `bmwLoadEachPage_DoWork` (`:3177-3550`) | Read/decode/resize/display artifact in one page work item | `_ZipRasterUnitJob.run`, `_decode_page`, `_render_unit` (shared by Folder and ZIP despite the historical private name) |
| `ViewerForm.cs`, `SetNewResizedImage` (`:5451-5468`) | Publish only a completed resized artifact | `RasterBookRuntime._on_job_completed` and `ViewerWidget.commit_display_ready_frame` |
| `ViewerForm.cs`, `priorityLevel` (`:5586-5605`) and `ReduceUsingMemory` (`:5471-5524`) | Current-centered retention and farthest-useful eviction | source/frame stores, combined budget and prefetch admission |

Folder file access, Qt/Pillow decode, EXIF, DPR, source/frame separation,
generation fences, PresentationState, atomic commit, paint acknowledgement,
virtual PageList and structured shutdown are NivisViewer/modern Qt designs.

### 15.7 Folder production-path A/B

The final default A/B used fresh child processes and the same temporary
detailed-JPEG folders on both sides: eight 900 x 1,350 small pages, eight
2,400 x 3,600 large pages, an alternating mixed folder, a one-page folder and
a two-page replacement folder.  The viewport was 1,200 x 800 and the soft
book budget was 256 MiB.  A used a benchmark-only generic `ImageSource`
adapter around the real `FolderImageSource`, which selects the retained
`ImageCache -> ViewerRenderTask` production path without adding a product
fallback flag.  B used an ordinary `FolderImageSource` and aborted unless
`FolderRasterBookRuntime` and direct-display mode were active.  Windows file
cache state was not flushed.

| Aggregate counter | A old two-stage | B Folder runtime | A - B |
|---|---:|---:|---:|
| File reads / returned bytes | 34 / 144,890,230 | 34 / 139,232,524 | 0 / 5,657,706 |
| Observable full-payload copies / bytes | 68 / 289,780,460 | 68 / 278,465,048 | 0 / 11,315,412 |
| Successful decode calls | 34 | 34 | 0 |
| Direct source QImages / Pillow source images / Pillow-to-QImage | 33 / 1 / 35 | 30 / 4 / 35 | +3 / -3 / 0 |
| Display QImages / `QPixmap.fromImage` | 36 / 35 | 38 / 37 | -2 / -2 |
| Viewer jobs / GUI callbacks | 70 / 68 | 38 / 38 | **32 / 30 fewer in B** |
| Duplicate / transit decodes | 2 / 8 | 2 / 8 | 0 / 0 |
| Display commits / content paints | 19 / 20 | 19 / 20 | 0 / 0 |
| Scenario elapsed total | 1,908 ms | 1,736 ms | 172 ms; A/B = 1.099x |
| Peak sampled retained cache | 33,016,160 bytes | 52,335,024 bytes | B +19,318,864 |
| Maximum per-scenario sampled working-set delta | 86.500 MiB | 99.941 MiB | B +13.441 MiB |
| Close time (millisecond-rounded) | 0.0 ms | 0.0 ms | 0.0 ms |

| Scenario | A elapsed / input-to-commit / input-to-paint ms | B elapsed / input-to-commit / input-to-paint ms | A reads / decodes / jobs | B reads / decodes / jobs |
|---|---:|---:|---:|---:|
| Small forward | 187 / 0 / 0 | 93 / 0 / 0 | 5 / 5 / 10 | 5 / 5 / 5 |
| Large forward | 344 / 0 / 0 | 265 / 0 / 0 | 5 / 5 / 10 | 5 / 5 / 5 |
| Mixed cold forward | 140 / 31 / 31 | 109 / 16 / 16 | 3 / 3 / 6 | 3 / 3 / 3 |
| Mixed reversal | 141 / 93 / 93 | 125 / 94 / 94 | 4 / 4 / 7 | 4 / 4 / 4 |
| Mixed cold roundtrip | 235 / 47 / 47 | 204 / 47 / 47 | 6 / 6 / 12 | 6 / 6 / 6 |
| Mixed rapid final | 125 / 31 / 31 | 125 / 16 / 16 | 3 / 3 / 6 | 3 / 3 / 3 |
| Mixed ready hit | 32 / 0 / 0 | 16 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Resize | 16 / n/a / 16 | 16 / n/a / 16 | 0 / 0 / 0 | 0 / 0 / 0 |
| Rotation | 203 / 0 / 203 | 187 / 94 / 187 | 2 / 2 / 5 | 1 / 1 / 3 |
| DPR change | 31 / 0 / 31 | 188 / 172 / 188 | 0 / 0 / 2 | 2 / 2 / 4 |
| Magnifier | 141 / n/a / 125 | 79 / n/a / 63 | 1 / 1 / 2 | 0 / 0 / 0 |
| Switch large to mixed | 78 / 15 / 15 | 78 / 16 / 16 | 2 / 2 / 4 | 2 / 2 / 2 |
| Switch small to large | 110 / 47 / 47 | 110 / 47 / 47 | 2 / 2 / 4 | 2 / 2 / 2 |
| Switch to one-page folder | 94 / 78 / 78 | 125 / 109 / 109 | 1 / 1 / 2 | 1 / 1 / 1 |
| One-page ready hit | 31 / 0 / 0 | 16 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

Ready hits on both paths avoided reads, decodes and jobs; B also emitted its
cached complete frame without a GUI callback or pixmap upload.  B's structural
gain is clearest in the removal of the second normal-page job/callback: it
roughly halves those counts while preserving the same 19 semantic commits and
20 explicit offscreen paints.  Magnifier reuse removed one full decode in B.
The default sequence nevertheless needed the same total decode count because
some high-resolution layout transitions legitimately upgraded a decoder-sized
preview.  In particular, the DPR scenario was slower in this single run and
the reversal's final input latency was also worse, so the aggregate result is
not evidence that every interaction improved.

The retained-cache result is a regression: B keeps sufficient decoded sources
across layout variants while A often drops them.  The sampled working-set
maximum was 13.441 MiB higher in the final run, and earlier identical/default
and quick runs changed its sign and magnitude.  Native decoder, Pillow conversion,
atomic old/new frame overlap and QPixmap allocation are not all in the logical
cache ledger, so no memory improvement is claimed.  The numbers are reported
rather than treated as a pass.  Both sides closed all 4 created sources, had zero open
workers, zero unfinished cache/render tasks and a fully drained coordinator.
No benchmark child remained.

This benchmark uses explicit offscreen `QWidget.render`, approximately 5 ms
working-set sampling, hot OS file cache and application-visible counters.
`input -> paint` is event-loop-quantized and observable full-copy counts do not
include unobservable plugin/native sharing.  It is supporting evidence only;
physical-device responsiveness and memory must be checked separately.

### 15.8 Remaining risk and next boundary

A Folder file read, Pillow decode or WebP QImageReader decode already running
inside native/library code cannot be interrupted safely.  A reversal cancels queued
work and rejects the obsolete result, but the one running decode can occupy the
Viewer lane until it returns.  The runtime also counts retained artifacts, not
transient compressed payload, Pillow conversion, old/new atomic-swap overlap or
native QPixmap allocation, so physical peak working set can exceed its soft
budget.

Two deterministic integration stalls were found while validating the new
Folder path.  A worker-side JPEG `QImageReader.read()` could overlap lazy GUI
icon/plugin work (`QIcon.pixmap` or showing the PageList dock) and stop both
threads on Windows/offscreen Qt.  Folder JPEG target decode now stays wholly in
the worker's Pillow/libjpeg path (`draft`, EXIF transpose and bounded
`thumbnail`) and returns a detached QImage.  Cold raster demands also enter the
same six-millisecond replaceable request timer, so already-queued Browser paint
can finish before worker dispatch.  Pixel decode never moves to the GUI thread.
ZIP stream decode is unchanged, and Folder WebP still uses Qt's plugin reader;
that unobserved combination remains a targeted real-device risk.

Validation also closed two state/lifetime holes exposed by the cutover.  A
complete error frame for the initially requested broken page now releases the
interactive-open gate while retaining a successful spread partner, and Viewer
close re-stages that window's committed progress snapshot before flushing the
shared metadata queue.  Neither change lets requested-only state become saved
or displayed.

The next structural format boundary is RAR/7z.  `SevenZipImageSource` currently
retains an entry index but may launch an external extraction process and
materialize stdout for each page.  It must not simply subclass the Folder
runtime until process cancellation, solid-archive ordering, clone ownership
and close semantics are moved into a book-scoped external-archive source.

### 15.9 Validation result

Syntax and import checks passed for all changed/new Python modules.  The final
collection contains 1,446 tests.  They passed across per-file independent
offscreen pytest processes (416 + 747 + 283), including the Folder/ZIP
runtimes, source/frame reuse, stale reversal, ready hit, PageList separation,
single/spread/LTR/RTL, rotation, magnifier, DPR, broken-image error frame,
book replacement, metadata progress, PDF and external-archive regressions.

Validation exposed two test-harness assumptions rather than product fallback
requirements.  The PageList replacement test waited for the now-unused
`ImageCache` and was changed to wait for the first committed presentation
frame.  Three Browser delegate tests instantiated QWidget-derived objects
without a QApplication; the Windows native crash disappeared after declaring
their existing `qapp` precondition, and that file then passed 8/8.  One broad
parallel sweep of `test_browser_file_operations.py` and one first run of a
Sprint 14 file did not terminate, but the complete files passed immediately on
fresh-process rerun (21/21 and 60/60), and every parameterized node also passed
independently.  No benchmark/test child remained after validation.

No real application, native input, external GUI application, commit or push
was used.  Offscreen timing and sampled working set do not establish a
physical-device improvement.

## 16. ZIP / Folder navigation critical-path replacement (2026-08-20)

### 16.1 Fixed-reference call chain

The comparison remains fixed to ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` (AGPL-3.0-or-later).
Its relevant navigation chain is:

```text
KeyboardShortcut.Control_MouseWheel / Owner_KeyDownOrUp
  -> Command.NextPage / PreviousPage
  -> ViewerForm.moveToNextPage / MoveToPreviousPage
  -> movePageNatural
  -> NextPage / PreviousPage checks ResizedImageArray's ready frontier
  -> currentPage + showCurrentPage
  -> BackgroundMultiWorker.SetWorksOrder
  -> bmwLoadEachPage_DoWork (read/decode/filter/resize)
  -> SetNewResizedImage
  -> showCurrentPage / paint
```

Source locations are `KeyboardShortcut.cs:436-448,653-682`,
`ViewerForm.cs:1903-2002,3177-3550,5371-5415,5451-5468,5558-5605`, and
`GenerarClasses.cs:247-264,298-338`.  ZipPlaFork does not coalesce rapid cold
input: once navigation reaches an unready page, later next/previous operations
are effectively ignored until that ready frontier advances.  Its active job is
not preempted; only unstarted work is reordered.

NivisViewer keeps the user's final target instead of dropping input:

```text
ViewerWidget wheel/key signal
  -> input kind (discrete / wheel / key initial / key repeat / slider scrub)
  -> ViewerPageNavigationController / PageModel requested target
  -> ViewerPresentationState.request_frame
  -> ViewerWindow builds current -> directional next -> reverse neighbor
  -> exact frame lookup
       hit: synchronous complete-frame publication
       miss, discrete/leading input: immediate RasterBookRuntime.request
       miss, proven rapid wheel/repeat: RasterBookRuntime.stage
             (immediate order/cancel/retention fence)
             -> cadence-derived short trailing admission / release boundary
             -> one active display-unit job
  -> one queued GUI completion / QPixmap upload
  -> atomic complete-frame commit
  -> slider/status and semantic PresentationState commit
  -> paint acknowledgement
  -> bounded neighbor prefetch
```

### 16.2 Comparative decisions

| Boundary | ZipPlaFork | NivisViewer before this change | Decision and result |
|---|---|---|---|
| Rapid cold input | Stops at the ready frontier; no final-target coalescing | One adaptive rule delayed discrete input and wheel alike | **Hybrid/new design:** discrete and the leading/low-rate wheel packet dispatch immediately. The second same-direction rapid packet proves a burst; only then is the latest target staged. Input is not dropped. |
| Work-order reversal | Reorders unstarted work around latest `currentPage`; active work finishes | New order reached the runtime only after the timer | **ZipPla principle adopted:** order, retention and obsolete cancellation change at input time. Nivis epoch/serial cancellation remains stronger. |
| Ready hit | Reads completed resized image directly | Already bypassed timer, worker, decode and `QPixmap.fromImage` | **Nivis maintained:** synchronous atomic commit remains; per-input cache-limit pruning was removed. |
| Cold current / prefetch | One worker, active work not preempted | One active job; delayed staging could let old-direction work continue | **Nivis Hybrid maintained/improved:** current first, cancel/stale fence, then a memory-admitted 4 + 1 complete-unit runway and seamless lazy book-wide warm-up from accepted commit. Paint acknowledges ownership but no longer gates scheduling. |
| Source vs display retention | Original/resized artifacts have a simple page lifecycle | Source eviction also deleted a still-valid QPixmap frame | **Modern design:** a display frame survives source eviction; last painted and requested frames are protected until replacement paint. Magnifier rehydrates a missing source on demand. |
| Commit UI work | WinForms updates current controls in `showCurrentPage` | PageList scroll/work, all menu QAction writes and metadata staging ran synchronously before paint | **Modern design:** slider/status and semantic commit stay synchronous; invariant full-action sync is removed; PageList scroll/work and persistence are serial-guarded/coalesced side effects. |
| Replacement open | Old form state remains until new open completes | Provisional open cleared old runtime artifacts before success | **Nivis ownership fix:** stop old work but retain completed artifacts until replacement success; failed open restores a ready hit. |

`RasterBookRuntime.stage` remains a two-phase navigation contract. It installs the
new current/work order, updates eviction ranking, stops obsolete prefetch and
cancels a replaceable active job without starting a cold target.  A later
`request` for the exact staged object opens the execution gate.  A completion
arriving while staged may be retained but cannot change presentation state.
Already-cancelled A jobs are never re-adopted in an A -> B -> A reversal.

`NavigationAdmissionPolicy` now separates discrete, wheel, initial key press,
key repeat, slider scrub and internal refresh. Discrete navigation and the
leading wheel/key press have no timer. A same-direction wheel packet within
40 ms proves a burst; its trailing boundary is the input-event timestamp
interval plus 2 ms, clamped to 6-28 ms. Device timestamps are used instead of
GUI callback arrival time so a slow decode cannot misclassify queued rapid
input as a low-rate wheel. An initial key press is immediate; later auto-repeat
packets for that key stage regardless of a momentary worker-idle gap and the
physical key release dispatches the exact final target. The timer is only a
release boundary owned by this state; it never owns the requested page or work
order. ScrollEnd, key release, slider release, direction reversal and a ready
hit bypass the wait.

The raster viewport timer is also cancelled when a navigation request captures
the live viewport/DPR. It can no longer invalidate the just-built frame store
in the middle of a wheel burst. If no navigation occurs after a real resize,
the existing delayed layout refresh still runs.

Source hydration has a separate rollback boundary.  If a ready QPixmap no
longer owns a decoded source, the runtime temporarily withholds that one frame
while it loads the source, but keeps the frame as a ready rollback artifact.
A superseding navigation restores it before cancelling work, so A -> magnifier
-> B -> A cannot turn an already-ready page cold.  Magnifier requests also use
a current-only work order; next/previous are not decoded at full resolution
while the lens competes for interactive CPU and memory.  A late terminal
hydration callback may refresh the runtime-owned source, but cannot replace a
valid frame with an error or republish an already-committed ready hit.

### 16.3 Port provenance

No C# statement or WinForms implementation was copied literally in this
change.  The AGPL-derived processing principles and their translated owners
are:

| Fixed-revision source | Adopted principle | NivisViewer implementation |
|---|---|---|
| `GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` | Replace the unstarted order immediately around the latest current page | `RasterBookRuntime.stage`, `_adopt_request`, `_drive` |
| `ViewerForm.cs`, `NextPage` / `PreviousPage` ready-frontier checks | Do not start decode for every transit page of a cold burst | `NavigationAdmissionPolicy`, input-kind routing and `RasterBookRuntime.stage` |
| `ViewerForm.cs`, `SetNewResizedImage` / `showCurrentPage` | Publish only completed resized artifacts and reuse ready output directly | frame-store hit, `commit_display_ready_frame`, last-painted protection |

ZipPlaFork's dropped input, fixed index-side preference, uncancellable stale
job, all-book prefetch, pre-paint background work, WinForms/GDI canvas and
pre-completion logical-control update were deliberately not adopted.

### 16.4 Historical adaptive-admission A/B (superseded)

The following table preserves the former four-child A/B that selected the
adaptive gate. It is historical evidence, not the current production policy.
The benchmark no longer reconstructs that removed fallback; it now runs only
the production input-kind path. The same temporary 24-page fixture alternated
900 x 1,350 and
2,400 x 3,600 detailed JPEGs, with a 1,200 x 800 viewport and five-unit,
256 MiB cache.  Input is fake/offscreen `QWheelEvent`, never native input.

Work columns are `jobs / queued callbacks / file reads / decode attempts`.
Latency is final input to explicit offscreen paint.

| ZIP scenario | A work | B work | A paint ms | B paint ms |
|---|---:|---:|---:|---:|
| Ready hit | 1 / 1 / 0 / 0 | 1 / 1 / 0 / 0 | 1.842 | 2.029 |
| 10-page forward | 12 / 12 / 12 / 12 | 12 / 12 / 12 / 12 | 1.375 | 1.342 |
| Rapid wheel 12, 0 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 17.145 | 18.226 |
| Rapid wheel 12, 4 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 15.680 | 20.034 |
| Rapid wheel 12, 8 ms | 7 / 7 / 6 / 7 | **4 / 4 / 3 / 4** | 17.368 | 22.615 |
| Direction reversal | 4 / 4 / 4 / 4 | 4 / 4 / 3 / 4 | 84.443 | **53.056** |
| Two-page roundtrip | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 1.120 | 0.891 |
| Outside-cache return | 15 / 15 / 14 / 14 | 15 / 15 / 14 / 14 | 50.517 | 49.852 |
| Mixed direction changes | 8 / 8 / 8 / 8 | 8 / 8 / 8 / 8 | 1.486 | 1.216 |

| Folder scenario | A work | B work | A paint ms | B paint ms |
|---|---:|---:|---:|---:|
| Ready hit | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 1.311 | 1.319 |
| 10-page forward | 12 / 12 / 12 / 12 | 12 / 12 / 12 / 12 | 1.506 | 1.501 |
| Rapid wheel 12, 0 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 30.712 | **27.957** |
| Rapid wheel 12, 4 ms | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | 26.510 | 28.227 |
| Rapid wheel 12, 8 ms | 7 / 7 / 7 / 7 | **4 / 4 / 4 / 4** | 50.659 | **33.107** |
| Direction reversal | 4 / 4 / 4 / 4 | 4 / 4 / 4 / 4 | 85.742 | 84.163 |
| Two-page roundtrip | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.949 | 0.967 |
| Outside-cache return | 15 / 15 / 14 / 14 | 15 / 15 / 14 / 14 | 50.843 | 50.078 |
| Mixed direction changes | 8 / 8 / 8 / 8 | 8 / 8 / 8 / 8 | 1.851 | 1.778 |

The ready critical path creates zero jobs and decodes on both sides.  The one
ZIP ready-scenario job above is post-paint neighbor prefetch, outside
input-to-commit.  Forward-10 has nine ready hits and one miss.  The paced 8 ms
case removes 43% of ZIP and Folder job/callback/decode work in this large
mixed fixture.  ZIP pays 5.247 ms of final wait in this offscreen run, while
Folder improves by 17.552 ms because avoided native decode contention exceeds
the admission margin.  With a smaller quick fixture the work reduction was
69% (ZIP) and 71% (Folder).  The result is deliberately reported as a
source/backend-dependent trade, not a universal latency win.

| Aggregate | ZIP A | ZIP B | Folder A | Folder B |
|---|---:|---:|---:|---:|
| Jobs / callbacks | 53 / 53 | **50 / 50** | 52 / 52 | **49 / 49** |
| Reads / decode attempts (successes) | 50 / 51 (47) | **46 / 48 (46)** | 51 / 51 (51) | **48 / 48 (48)** |
| Stale / cancel | 3 / 5 | **1 / 2** | 4 / 5 | **2 / 2** |
| Cache hits / misses / evictions | 38 / 44 / 26 | 38 / 44 / 26 | 38 / 43 / 26 | 38 / 43 / 26 |
| Working-set delta MiB | +17.590 | +17.672 | +20.004 | +21.020 |
| Process-peak growth MiB | 34.914 | 35.000 | 35.297 | 36.434 |
| Shutdown ms | 2.197 | 2.041 | 1.875 | 1.787 |

All four children reported a completed coordinator, no remaining runtime job
and no residual benchmark process.  Timings use `perf_counter_ns`; the JSON
also records input-to-request, request-to-cache decision, request-to-decode,
decode-to-commit and commit-to-paint min/median/p95/max values.  Windows file
cache is hot, QPixmap/native allocations are not fully observable, explicit
offscreen render is not DWM presentation, and no real-device improvement is
claimed from these values alone.

### 16.5 Validation and remaining limits

Syntax/import checks passed for every changed Python file.  The runtime,
magnifier and ZIP/Folder integration group passed 59/59 in one independent
offscreen process.  Related files passed independently: ViewerWindow 23,
ViewerWidget 38, PresentationState 5, virtual PageList runtime 4, metadata
integration 19, BookSession 16, ImageSource 25 and PageModel 12.  The 57-test
Sprint 18 navigation file was covered by five independent functional groups
(core, slider, fullscreen chrome, canvas and settings), all passing.

One broad combined process aborted after 113 passes, and the whole Sprint 18
file also aborted after 40 tests, while a Pdfium service thread or a raster Qt
worker still existed during Python/Qt garbage collection.  The same nodes pass
when split into clean processes; therefore this is recorded as a suite-process
lifetime risk rather than hidden or attributed to an assertion failure.  No
test/benchmark process remained afterward.  The complete suite was not rerun
for this navigation-only change.

The benchmark uses `ZIP_STORED`, a hot OS file cache, explicit offscreen
`QWidget.render`, and no visible PageList.  It does not include native wheel
delivery, DWM/GPU presentation, disk-cold I/O or a real user's simultaneous
PageList/chrome activity.  Physical-device confirmation remains required.

### 16.6 Input-kind admission replacement (2026-08-20)

The production policy above was checked with the same quick temporary fixture
before and after the replacement. The earlier side is the immediately preceding
adaptive production path saved before editing; it is not the historical 6 ms
benchmark-only fallback. The current runner uses two fresh child processes
(ZIP and Folder) and its `--minimal` mode executes only cold single wheel,
ready hit, 12 packets at 8 ms and reversal. Synthetic wheel events carry device
timestamps; no native input or visible application was used.

| Source / scenario | Before request dispatch ms | After request dispatch ms | Before / after jobs-decodes | Before paint ms | After paint ms |
|---|---:|---:|---:|---:|---:|
| ZIP cold leading wheel | 5.875 | **0.200** | N/A / 1-1 before commit | N/A | 8.985 |
| ZIP ready hit | 0.295 | 0.390 | 0-0 / 0-0 before commit | 0.916 | 1.107 |
| ZIP rapid wheel, 12 x 8 ms | 14.904 | **10.596** | 4-4 / 4-4 total | 17.927 | **13.702** |
| ZIP reversal | 9.582 | **6.621** | 4-4 / 4-4 total | 17.140 | **14.399** |
| Folder cold leading wheel | 6.125 | **0.200** | N/A / 1-1 before commit | N/A | 13.752 |
| Folder ready hit | 0.347 | **0.256** | 0-0 / 0-0 before commit | 0.995 | **0.848** |
| Folder rapid wheel, 12 x 8 ms | 13.339 | **10.344** | 4-4 / 4-4 total | **18.738** | 20.648 |
| Folder reversal | 8.536 | **6.502** | 4-4 / 4-4 total | 19.286 | **19.232** |

Ready hit creates no critical-path job, decode or QPixmap upload on either
side; each scenario's one total job is neighbor prefetch released after paint.
The new policy removes about 5.7-5.9 ms of artificial admission from the first
cold wheel and 2.7-4.3 ms from final rapid/reversal dispatch while preserving
the preceding adaptive path's four-job rapid work bound. ZIP final paint also
improves in this run. Ready-hit critical work is unchanged; its sub-millisecond
movement is test-process noise. Folder rapid final paint is about 1.9 ms slower
in this sample even though dispatch is earlier, so no physical-device
improvement is inferred for that backend from one offscreen run.

The measured values use small 320 x 480 / 900 x 1,350 JPEGs, hot Windows file
cache, `perf_counter_ns`, explicit offscreen `QWidget.render`, five retained
units and a 64 MiB budget. Post-paint neighbor work is included in total jobs;
the cold single critical path itself is exactly one job/decode. These figures
select the implementation and guard against regression, but real wheel cadence,
disk-cold I/O and DWM presentation still require physical-device confirmation.

After the final input-routing and 64-bit timestamp fixes, syntax/import checks
passed and five independent offscreen pytest processes passed 17 + 2 + 6 + 44
+ 2 = 71 focused tests. They cover policy/core, production admission and
atomic old-frame behavior, ZIP/Folder ordered-runtime contracts, canvas/slider/
fullscreen routing, and book-switch lifetime. The production-only minimal
benchmark completed both fresh children, reported drained coordinators and no
remaining runtime task.

## 17. Large-raster preview/full tier replacement (2026-08-21)

This change addresses a physical-pixel path that becomes dominant when a
JPEG's short side reaches roughly 4,000-5,000 pixels.  That observed range is
**not a threshold in NivisViewer**: no branch compares either dimension with
4,000 or 5,000.  The audit did find a separate feature-gated cliff: requests
with the conditions below switched from decoder-sized preview to full-raster
work.  Pure standard fit was already decoder-scaled, so the offscreen evidence
does not by itself attribute the user's standard-fit slowdown to this
boundary.  A 5,000 x 7,500 32-bit surface alone is about 143 MiB, so decode,
orientation, adjustment, rotation, PIL-to-QImage materialization and a later
scale can multiply memory traffic even when the compressed ZIP entry is small.

### 17.1 Exact former production boundary

Before this replacement, decoder-sized JPEG was used only by the standard
`fit_window` / `fit_no_upscale` case with application rotation 0, no wide-page
split, no magnifier selection or active lens, and default
brightness/contrast/gamma.  The following conditions requested a full source:

- `fit_width`, `fit_height`, actual-size or manual zoom;
- a resampling mode other than `standard`;
- application rotation 90, 180 or 270 degrees;
- wide-page splitting;
- magnifier selection or an active magnifier;
- a non-default brightness, contrast or gamma adjustment; or
- failure/null output from the scaled decoder path.

The full Folder path decoded with Pillow, applied EXIF transpose, detached an
additional full-size copy, converted the whole raster to QImage, and then let
the render stage rotate and reduce it.  The ZIP generic path additionally
materialized the expanded entry before decode.  Rotation happened before the
final target scale, and adjustment could create more than one full PIL
intermediate.  Thus compressed byte size was not a reliable predictor of the
cost seen during page turns.

### 17.2 What ZipPlaFork does—and does not do

The comparison remains fixed to
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.  Its relevant source is licensed
AGPL-3.0-or-later; the retained license and notices documented in section 1
continue to apply.

The inspected `source/ZipPla/ViewerForm.cs` page worker,
`source/ZipPla/ImageLoader.cs` decode/filter helpers and
`source/ZipPla/GenerarClasses.cs` work-order code do **not** provide a
decoder-sized JPEG path.  ZipPlaFork normally obtains an entry stream, creates
a full `Bitmap`, performs orientation/filter/rotation work on full source
pixels, and only then creates the resized display artifact.  Its light
navigation therefore comes from simple ownership and work order, not from
avoiding the original raster.

The following boundary is intentional:

| Reference behavior | Decision in NivisViewer |
|---|---|
| One Viewer worker and one current-centered replaceable order | **Adopted / retained.** `RasterBookRuntime` has one active unit job and re-evaluates current before directional neighbors. |
| A completed resized artifact is a direct display hit | **Adopted / retained.** A ready frame bypasses worker, decode and QPixmap upload. |
| Decode a full GDI `Bitmap` as the normal source for every page | **Not adopted.** This would reproduce the large-raster memory traffic this change removes. |
| WinForms/GDI page publication and control timing | **Not adopted.** Nivis atomic complete-frame commit, old-frame retention and `ViewerPresentationState` remain stronger. |
| WinForms-era DPI assumptions | **Not adopted.** Qt physical targets, fractional DPR and stale-DPR rejection remain authoritative. |

No C# statement was copied literally for the scaled-decode work below.  The
AGPL-derived parts remain the previously recorded one-worker/current-first and
completed-artifact-hit processing principles.  The preview/full split is a
NivisViewer modern design.

### 17.3 New physical-pixel contract

The raster job still owns decode through display-ready frame generation, but
the source request is now based on the layout that will consume it:

| Boundary | New contract |
|---|---|
| Preview target | Fit mode, logical display unit, per-page spread/split slot, physical viewport, DPR and application rotation determine the exact final physical-pixel target. Rotation 90/270 maps that target back to decoder axes. Decoder headroom is uniformly 1.0: the retained JPEG source is the smallest decoder-native tier that is still at least as large as the final target, not an arbitrary algorithm-specific intermediate. |
| ZIP JPEG | The ordinary/two-axis selected entry is read once into one reserved `QByteArray`; a seekable `QBuffer` and `QImageReader` request the smallest native JPEG reduction tier sufficient for the physical target. Unknown one-axis prefetch is the deliberate exception: it first reads only the JPEG header to validate allocation. A decoder that ignores the tier and returns more than twice the required edge is contained before that oversized raster enters the preview store. |
| Folder JPEG | Pillow uses the same 1/2, 1/4 or 1/8 native `draft` tier rule. Orientation and optional adjustment operate on that sufficient source; the redundant post-`exif_transpose` full-size copy is removed. The configured renderer then owns the one exact final layout scale. |
| Transform order | Preview-eligible fit requests perform at most one decoder-native integer reduction, then application rotation/crop/adjustment and exactly one configured resize to the exact DPR-aware physical target. The former arbitrary decoder-sized resize followed by another render resize is removed. Full decode is reserved for actual-size, manual zoom, magnifier-source promotion and nearest-neighbor semantics, or a decoder/format fallback that requires original pixels. |
| Source cache | Preview and full are distinct tiers and may coexist for the same page. Normal fit selects the smallest sufficient preview; a full promotion no longer destroys the useful fit preview or an already completed QPixmap. |
| Magnifier | Original-pixel promotion uses a current-only order. It does not start full-resolution next/previous prefetch. Cancelling the lens synchronously adopts the normal preview key, stales/cancels an incompatible full job, and returns through the retained frame without clearing either cache tier. |
| Admission / eviction | Admission uses decoded-source bytes plus frame bytes, not compressed entry length. Folder estimates its retained native JPEG reduction tier rather than the smaller exact target. Lazy one-axis fit uses a bounded provisional estimate to enter the worker, then a worker-only JPEG header probe checks the complete display unit's missing retained sources plus layout frame bytes before allocating pixels; over-budget/probe-failed prefetch is suppressed for that work order. Current preview, visible spread partner and last-painted frame are protected; optional full sources are preferred eviction candidates when normal-fit work needs room. |
| Publication | Only a complete frame reaches the GUI. QPixmap creation, atomic commit, serial/epoch/layout/DPR stale fences, presentation projection and paint acknowledgement are unchanged. |

This is deliberately a Hybrid/New-design decision: it keeps ZipPlaFork's
compact current-first worker discipline, improves on its full-source pixel
cost, and keeps NivisViewer's high-DPI, spread/RTL, magnifier, atomic
presentation and virtual PageList UX boundaries.

### 17.4 Historical standard-fit production A/B

This run predates both the final independent resampling authority in section
17.8 and the Startup Ready Runway in section 20.  A and B ran in fresh
offscreen child processes with the same alternating
1,200 x 1,800 / 4,500 x 7,000 solid-JPEG fixture, 1,200 x 800 viewport, five
retained units and 256 MiB budget.  A is the immediately preceding production
implementation; B is the production implementation after this replacement.
Latency is final synthetic input to explicit offscreen paint.  Scenario totals
include post-paint neighbor prefetch; the `critical` column stops at the final
frame commit.

| Source / scenario | Input -> paint ms, A -> B | Critical jobs / decode attempts, A -> B | Scenario jobs / callbacks, A -> B |
|---|---:|---:|---:|
| ZIP cold wheel | 12.692 -> 13.132 | 1 / 1 -> 1 / 1 | 3 / 3 -> 3 / 3 |
| ZIP ready hit | 1.240 -> 1.361 | 0 / 0 -> 0 / 0 | 1 / 1 -> 1 / 1 |
| ZIP rapid wheel, 12 x 8 ms | 15.580 -> 15.707 | 2 / 2 -> 2 / 2 | 4 / 4 -> 4 / 4 |
| ZIP direction reversal | 17.591 -> 17.214 | 2 / 1 -> 2 / 2 | 4 / 4 -> 4 / 4 |
| Folder cold wheel | 18.903 -> 15.893 | 1 / 1 -> 1 / 1 | 3 / 3 -> 3 / 3 |
| Folder ready hit | 1.450 -> 1.282 | 0 / 0 -> 0 / 0 | 1 / 1 -> 1 / 1 |
| Folder rapid wheel, 12 x 8 ms | 25.273 -> 20.341 | 2 / 2 -> 2 / 2 | 4 / 4 -> 4 / 4 |
| Folder direction reversal | 22.404 -> 20.045 | 2 / 1 -> 2 / 2 | 4 / 4 -> 4 / 4 |

The standard ZIP path was decoder-scaled before this work and remains so:
large output is 480 x 746 and small output 497 x 746 on both sides.  B's
sub-millisecond ZIP movements are mixed/noise-sized and are not presented as
an improvement.  Folder A produced the same exact-size outputs.  Folder B uses
the native JPEG draft tier and therefore retains 563 x 875 for the large page
and 600 x 900 for the small page before final layout scaling.  It is faster in
all four sampled Folder scenarios, but its deliberately larger reusable source
also makes the aggregate memory result worse in this run.

| Aggregate | ZIP A | ZIP B | Folder A | Folder B |
|---|---:|---:|---:|---:|
| Jobs / queued callbacks | 12 / 12 | 12 / 12 | 12 / 12 | 12 / 12 |
| Reads / decode attempts (successful) | 11 / 11 (11) | 11 / 12 (11) | 11 / 11 (11) | 12 / 12 (12) |
| Stale results / cancel requests | 1 / 2 | 1 / 2 | 1 / 2 | 1 / 2 |
| Working-set delta MiB | 5.969 | 5.766 | 9.141 | 10.223 |
| Process-peak growth MiB | 13.266 | 14.488 | 20.141 | 24.051 |

The extra ZIP attempt is one cancelled attempt without another completed
entry read; the extra Folder attempt/read comes from scheduling around the
reversal.  Neither side duplicates a successful decode of the same active
target.  A ready hit still creates zero critical-path jobs and zero decodes;
in that measured predecessor, the one scenario-total job was neighbor prefetch
released only after paint.  That paint-gated scheduler is not the current
contract.

### 17.5 Historical former full-transform A/B

This comparison predates the final independent resampling authority in section
17.8.  The standard-fit fixture above could not demonstrate the then-current
main improvement because that path was already reduced.  A separate
isolated-worker A/B therefore used one 4,500 x 7,000 ZIP JPEG, rotation 90
degrees, brightness 1.2, the legacy `high_quality` mode and a 1,200 x 800
viewport.  A reconstructed the former full-source transform demand; B used the
intermediate layout-aware preview with 2.0 quality headroom.  Both generated
the same 1,200 x 771 display frame.

| Metric | A: former full transform | B: intermediate preview transform |
|---|---:|---:|
| Decoded source dimensions | 4,500 x 7,000 | 1,543 x 2,400 |
| Cold runtime completion ms | 507.020 | 95.235 |
| Decoded-source cache bytes | 94,500,000 | 11,116,800 |
| Total source + frame cache bytes | 98,200,800 | 14,817,600 |
| Working-set delta MiB | 97.273 | 15.664 |
| Sampled peak growth MiB | 423.066 | 54.781 |
| Jobs / decodes / reads / QPixmap creations | 1 / 1 / 1 / 1 | 1 / 1 / 1 / 1 |

The reduction came from doing less pixel work, not from skipping a job or
changing presentation semantics.  At that stage the preview was sized by the
2.0 high-quality headroom policy for the rotated output, while avoiding the
94.5 MB source and its full-sized transform intermediates in the normal fit
path.  The final production contract no longer uses that algorithm-specific
headroom; section 17.8 records the sufficient native JPEG tier plus one exact
configured final resize.  These values therefore remain historical evidence,
not a benchmark of the final filter authority.

### 17.6 Preview/full layout reuse A/B

This second comparison is a **current-runtime adapter comparison**, not a
checkout or executable snapshot of an old production binary.  Each side uses
a separate current `ZipRasterBookRuntime` process.  A calls
`cancel(clear_artifacts=True)` before each layout step to emulate the former
clear-and-redecode ownership.  B calls `invalidate_layout()` so frame artifacts
change while sufficient preview/full decoded sources remain owned by the book.
The sequence covers initial preview, smaller resize, full promotion, DPI/manual
zoom and magnifier-equivalent demand.

| Metric | A: clear source and frame | B: retain preview/full source tiers |
|---|---:|---:|
| Decode attempts / entry reads | 5 / 5 | 2 / 2 |
| Completed entry bytes | 2,473,465 | 989,386 |
| Sequence elapsed ms | 564.547 | 340.933 |
| Working-set delta MiB | 327.656 | 146.918 |

B decodes one sufficient preview and promotes once to full; later layout and
magnifier-equivalent requests reuse the matching tier.  The adapter nature of
A means these figures demonstrate the ownership/cache contract rather than an
exact historical end-user latency comparison.

### 17.7 Related validation and remaining limits

The focused runtime, image-source, Viewer integration, navigation,
presentation, magnifier, Folder/ZIP, layout and shutdown groups passed
**249 related tests** in independent offscreen processes.  The benchmark
children reported drained runtime/coordinator shutdown, and no test or
benchmark process remained afterward.  This was related-scope validation, not
a claim that every repository test ran in one process.

The fixtures use solid JPEG, a hot Windows file cache, synthetic input,
offscreen Qt and explicit `QWidget.render`; they do not exercise disk-cold I/O,
native wheel/key delivery, DWM/GPU presentation, or the exact compression and
pixel detail of the user's books.  Pillow native draft deliberately retains a
coarser decoder tier than the exact ZIP scaler, so the standard Folder memory
aggregate can rise even when transform latency falls.  QPixmap/native backing
allocations and transient decoder peaks are sampled rather than exhaustively
observable.  Actual-size, manual zoom, magnifier and nearest-neighbor mode still require
full pixels, and a protected current page may temporarily exceed the budget.
An unindexed `fit_width`/`fit_height` prefetch performs one extra header-only
entry/file access before decode so an extreme aspect cannot allocate beyond
the free prefetch budget.  The current page is intentionally not declined and
can still soft-overflow the retention budget when displaying an unusually long
page; physical-device checks must therefore include that case.

Accordingly, no physical-device improvement is inferred solely from these
offscreen timings.  The acceptance check remains repeated turns, reversal and
rapid wheel input on the user's 4,000-5,000-pixel and larger ZIP/Folder books,
including rotation, brightness/sharp or Lanczos rendering, magnifier enter/leave,
spread, DPR change and return to a previously completed normal-fit frame.

### 17.8 Final independent resampling authority

This subsection supersedes the older combined-mode and quality-headroom
wording elsewhere in section 17.  The production authority is now four
independent settings rather than one label that ambiguously controls both
reduction and enlargement:

| Surface | Reduction authority | Enlargement authority | Defaults |
|---|---|---|---|
| Normal Viewer | `viewer_downscale_algorithm`: `auto`, `fast`, `smooth`, `sharp`, `area`, `nearest` | `viewer_upscale_algorithm`: `auto`, `bilinear`, `bicubic`, `lanczos`, `nearest` | `auto` / `auto` |
| Magnifier | `magnifier_downscale_algorithm`: the same reduction set | `magnifier_upscale_algorithm`: the same enlargement set | `sharp` / `lanczos` |

`ResamplingPolicy` in `app/viewer_render.py` is the value contract.  Explicit
algorithms are part of `ViewerRenderKey` and `ZipRasterRenderSpec` equality, so
a cache hit can never silently reuse output produced by another filter.
`ViewerWindow` and `ViewerWidget` pass the normal pair to complete display-unit
rendering and the magnifier pair to lens rendering.  Equal-size output takes a
copy without a resampling pass.  A real size change uses one native Pillow
filter: `BOX`, `BILINEAR`, `BICUBIC`, `LANCZOS` or `NEAREST`; `auto` chooses
BOX for a reduction of at least one half, Lanczos for a moderate reduction and
bicubic for enlargement.  The compatibility-only legacy standard caller may
still use Qt's native `QImage.scaled` path.  No Python pixel loop, custom SIMD
resizer, new native library or GPU dependency was introduced: this remains a
Qt/Pillow-only design.

The final frame size is no longer clamped to a decoder result that is smaller
than the requested target.  The old sequence could accept such a source,
produce a target-underfilled frame and then let QPainter/device DPR enlarge it;
another path requested an arbitrary exact decoder size and subsequently ran
the selected resize filter again.  `jpeg_native_reduction_size`, the ZIP/Folder
JPEG readers, `RasterBookRuntime._contain_preview_source`, uniform decoder
headroom 1.0 and exact physical render targets now establish one rule: retain
the smallest native JPEG tier sufficient for the DPR-aware target, then run
exactly one deliberate final resize.  This keeps the selected filter
authoritative and prevents hidden double scaling without forcing normal fit to
retain the original raster.  A fresh decoder result is checked against the
same source-sufficiency postcondition used by the cache; if a backend returns
even one axis below the required size, that preview is not admitted and the
job promotes once to the full source.

The GUI creates one `QPixmap` from the exact display `QImage`, assigns the
request DPR and uses point-form painting for both normal frames and the
magnifier whenever the natural device-independent size matches the layout.
This removes the former destination-rectangle resample caused by fractional
DPR rounding.  The explicit exception is a `manual_zoom` artifact above
64 MiPixels: until tiled zoom rendering exists, it is proportionally bounded
and may be enlarged by QPainter to avoid a single multi-gigabyte allocation.
Ordinary fit and actual-size frames do not use that safety cap.

`ConfigManager._migrate_legacy_resampling_settings` performs a one-way
translation from `viewer_resampling_mode`, `magnifier_resampling_mode` and
`smooth_scaling`.  If both new keys for a surface already exist they win;
otherwise the legacy pair is translated once.  The legacy keys are then
removed during load/normalization/update and are never saved again.  Old API
setters expose only a compatibility view over the new settings, so there are
not two writable authorities.

The performance provenance remains ZipPlaFork fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later:

- `source/ZipPla/ViewerForm.cs`, `bmwLoadEachPage_DoWork`
  (`:3177-3550`) and its scaling selection (`:3405-3449`), plus
  `GetResizedSize` (`:5273`), informed the structural rule that one page job
  owns decode/filter/resize through a complete display artifact;
- `source/ZipPla/ViewerForm.cs`, `SetNewResizedImage` (`:5451-5468`), and
  the `PreFilteredImageArray` / `ResizedImageArray` fields informed retained
  source versus completed-output reuse; and
- `source/ZipPla/ImageLoader.cs`, `GetJpegOrientation` / `LoadRotateBitmap`
  (`:382-400`) and `GetFullBitmap(Stream)` (`:559-689`), together with
  `source/ZipPla/BitmapResizer.cs`, were evaluated for its decode/orientation/
  resize order.

The independent normal/magnifier reduction/enlargement policy, fractional-DPR
target, native JPEG tier selection, exact one-final-resize rule, immutable Qt
cache identity and one-way settings migration are NivisViewer modernizations,
not copied WinForms/GDI code.  ZipPlaFork's full-GDI-source policy and its
legacy resizer choices were deliberately not adopted.  The retained license
and copyright files at `licenses/ZipPlaFork/AGPL.txt` and
`licenses/ZipPlaFork/About.txt`, section 1 and `THIRD_PARTY_NOTICES.md` cover
the AGPL-derived structural principles.

### 17.9 User-facing resampling authority

The four production settings above were initially reachable from the
Viewer's View menu but were absent from `SettingsDialog`.  Consequently a user
looking in the persistent settings UI could not discover or change the
implemented algorithms.  `SettingsDialog._build_viewer_tab`,
`load_current_values` and `values` now expose the same four ConfigManager keys
under **画像の拡大縮小**, split into **通常表示** and **拡大鏡**, each with an
independent **縮小方式** and **拡大方式**.  Both the persistent dialog and the
existing Viewer radio menus use the shared label dictionaries in
`app/viewer_render.py`; unsupported or placeholder choices therefore cannot
drift into either UI.

| UI value | Downscale backend | Upscale backend |
|---|---|---|
| 自動 | BOX at 0.5 scale or below; otherwise Lanczos | Bicubic |
| 高速 | Pillow bilinear | — |
| 滑らか | Pillow bicubic | — |
| シャープ | Pillow Lanczos | — |
| 面積平均 | Pillow BOX | — |
| バイリニア | — | Pillow bilinear |
| バイキュービック | — | Pillow bicubic |
| Lanczos | — | Pillow Lanczos |
| 最近傍 | Pillow nearest | Pillow nearest |

Applying the dialog writes only the four modern keys.  `ConfigManager` emits
the normal settings to every open `ViewerWindow`; the window invalidates only
layout-dependent frame artifacts, keeps the runtime's decoded source store,
and immediately requests the current complete display unit with the new
algorithm identity.  Neighbor frames are regenerated by the existing
book-scoped warm-up order after the current frame.  Magnifier-only changes do
not invalidate normal frames: an active lens retains its old complete pixmap
until its new generation finishes, using the retained magnifier source.  The
frame identities continue to include purpose (normal/magnifier), both
algorithm directions, physical target, DPR, rotation and adjustments; the
decoded-source identity intentionally does not include resampling settings.

Representative offscreen fixtures cover fine black lines/text, photographic
gradients, screenshot-like one-pixel UI edges and pixel art.  They verify that
the supported explicit native filters produce distinct output families, while
also recording the intentional `auto` aliases (large reduction to BOX and
enlargement to bicubic).  Folder runtime regression varies the algorithms
between layout generations and confirms that the already sufficient decoded
source is reused rather than read or decoded again.

This UI completion adopts ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`'s independent
`StandardScalingAlgorithm` / `MagnifierScalingAlgorithm` and `ScaleDown` /
`ScaleUp` authority concept.  The relevant provenance remains
`source/ZipPla/ViewerForm.cs`: the two `ScalingAlgorithmPair` fields
(`:282-283`), `bmwLoadEachPage_DoWork` normal selection (`:3405-3449`),
magnifier selection (`:7803-7807`), configuration load/save
(`:5826-5829`, `:5958-5978`), `setStandardScalingAlgorithm`
(`:14574-14600`) and `setMagnifierScalingAlgorithm` (`:14772-14784`), under
AGPL-3.0-or-later.  NivisViewer does not copy ZipPlaFork's large legacy
Lanczos/Spline matrix: the modernized UI publishes only the Qt/Pillow-native
backends already used by production, preserves high-DPI physical targets and
adds live frame-only regeneration.

## 18. ZIP initial warm-up and automatic memory budget (2026-08-21)

This change addresses a different boundary from section 17. The current-page
preview was already fast and completed artifacts were retained correctly, but
an untouched newly opened ZIP did not populate beyond its tiny submitted
frontier. Increasing a nominal memory value therefore could not prepare the
same display-ready pages that became fast after manual navigation.

### 18.1 Production diagnosis before the replacement

The settings trace found two independent limits. The old checkout did not have
the described discrete Viewer-memory selector: prefetch presets supplied a
free-form MiB value, unknown values such as `auto` normalized to 256 MiB, and
normalization capped integers at 4,096 MiB. Supported integer values did reach
`ImageCache`, `BookSession` and an existing ZIP/Folder runtime, but
`BookSession` also copied the legacy `ImageCache.cache_size`—normally ten—into
the runtime unit ceiling. More importantly, `ViewerWindow._zip_runtime_request`
submitted only current plus one neighbor on each side. A byte budget cannot
start work that is absent from the order.

The following pre-change production probe used a temporary 24-page ZIP of
alternating 4,500 x 7,000 and 1,200 x 1,800 solid JPEGs, standard fit, a
1,200 x 746 physical Viewer, hidden PageList, offscreen Qt and an explicit
first paint. It then supplied no user input. `auto` here means the former token
that fell back to 256 MiB, not the resolver introduced below.

| Requested setting | Runtime budget | Immediately after paint | 1 second | 3 seconds | 5 seconds |
|---|---:|---|---|---|---|
| former `auto` | 256 MiB | ready `[0]`; 3.137 MiB | ready `[0, 1]`; 5.763 MiB | unchanged | unchanged |
| fixed 256 MiB | 256 MiB | ready `[0]`; 3.137 MiB | ready `[0, 1]`; 5.763 MiB | unchanged | unchanged |
| fixed 4 GiB | 4 GiB | ready `[0]`; 3.137 MiB | ready `[0, 1]`; 5.763 MiB | unchanged | unchanged |

At one second the 5.763 MiB combined total was 2.780 MiB of display frames and
2.983 MiB of decoded sources. No source-only page existed: the one neighbor
that was requested did become display-ready. The initial work order was only
`[[0], [1]]`; 4 GiB still had about 4,090 MiB free. The runtime was idle,
paint release was complete, Browser/PageList pause was false, and admission had
not stopped. The three jobs and three callbacks included the initial
spec/layout transition. Thus first-paint resumption and cost admission were
healthy for the submitted frontier.

The user hypotheses classify as follows:

| Hypothesis | Result |
|---|---|
| A. budget propagation | **Partly confirmed.** Ordinary legacy integers propagated, but there was no true `auto`, no 8–32 GiB contract and no memory-mode UI independent of prefetch counts. |
| B. wide retention but narrow initial population | **Confirmed.** The submitted work order ended after the immediate neighbor regardless of free bytes. |
| C. first-paint resume failure | **Rejected by this probe.** The neighbor started after paint and all pause/gate state returned to idle. |
| D. admission/cost or fixed-count limit | **Partly confirmed.** Admission was not overestimating this fixture, but the inherited ten-unit safety ceiling would later waste a large budget. |
| E. background decode merely cannot keep up | **Not the stopping cause.** The worker had no remaining submitted work at one second. |

### 18.2 Fixed ZipPlaFork reference and adaptation boundary

The source comparison remains fixed to
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later. The retained license text, copyright notices and provenance
in section 1 continue to apply.

At that revision, the memory selector in `source/ZipPla/SettingForm.cs` uses
index 0 for Minimum, indices 1 through 8 for `(128 MiB << index)`—256 MiB
through 32 GiB—and `ulong.MaxValue` for Automatic. `ViewerForm.GetUserMemoryUBound`
caps user memory by process/address-space and physical-memory constraints.
`ViewerForm.SetMemoryUBound` combines process working set and available physical
memory with an active coefficient of 0.7 and an inactive coefficient of 0.3.
`SetBackgroundMode` and `BackgroundMultiWorker.SetWorksOrder` give the one
Viewer worker an all-page order: current fixed numerical band, next numerical
band, previous numerical band, then the remaining pages. `ReduceUsingMemory` evicts original and
resized ownership together from the low-priority end only when memory pressure
requires it, while keeping the minimum visible neighborhood.

The following is the exact adaptation boundary:

| Fixed-revision source / method | Adopted principle | NivisViewer implementation |
|---|---|---|
| `SettingForm.cs`, memory selector; `ViewerForm.GetUserMemoryUBound` / `SetMemoryUBound` | A user memory mode must resolve to the same real budget that controls background population | `app/viewer_memory_policy.py`; `ConfigManager.viewer_memory_mode`; `ViewerWindow._load_prefetch_settings` |
| `ViewerForm.SetBackgroundMode`, `priorityLevel`; `GenerarClasses.BackgroundMultiWorker.SetWorksOrder` | Keep a book-wide current-centered replaceable order instead of a three-unit submitted frontier | `ViewerWindow._zip_runtime_request`; `RasterBookRuntime._adopt_request`, `_drive` |
| `ViewerForm.ReduceUsingMemory` | Let memory pressure, current priority and completed-artifact usefulness decide the stopping/eviction point | combined source/frame ledger, `set_cache_limits`, prefetch admission and work-order-aware stores |

The WinForms control shape, `ulong.MaxValue` sentinel, continuously variable
limit, GDI ownership and exact active/inactive formula were not copied.
NivisViewer instead uses a stable modern Windows snapshot and listed buckets.
No C# statement was translated literally in this change; the all-book
memory-driven work-order structure is recorded as AGPL-derived.

### 18.3 First Hybrid replacement contract (historical predecessor)

This subsection and its probe record the first all-book replacement.  Sections
19--20 supersede its post-paint release, materialized finite order and
book-length safety ceiling with a count-free lazy plan, the commit-released
Startup Ready Runway and seamless book-wide continuation.  The memory-mode
resolution and combined-byte principles below remain applicable.

`viewer_memory_mode` is now an independent saved string with `minimal`
(128 MiB), fixed 256/512 MiB and 1/2/4/8/16/32 GiB modes, plus `auto`. Fixed
modes resolve byte-exactly: 256 MiB is 268,435,456 bytes and 4 GiB is
4,294,967,296 bytes. A legacy prefetch memory value migrates once to the
nearest listed bucket. The Raster ZIP and Folder runtimes use the new byte
budget; the old custom image-unit counts remain only for PDF, RAR/7z and other
unmigrated sources. `disabled` still disables Raster background warm-up, and
the direction-priority checkbox still controls whether travel direction or
numeric next is preferred.

Automatic mode reads total and available physical memory plus process working
set once when a ViewerWindow first resolves that mode, or when the user
explicitly changes back to it. It subtracts non-cache working set and an OS
reserve of the larger of 2 GiB or 20% of total RAM (bounded by total RAM), then
takes the smaller total-memory and available-memory bound. The result is
rounded down to one of the listed 128 MiB through 32 GiB buckets. Existing
cache bytes are added back once because they are already reflected in
`available`, avoiding double charge in the normal case. The available bound is
nevertheless `max(0, cache + available - reserve)`: when available memory is
below the reserve, that shortfall also reduces an existing cache instead of
freezing it as permanently protected. Snapshot failure falls back to a stable
512 MiB. Book switches do not resample fluctuating available memory; a mode
change is the explicit recalculation boundary.

After the current complete frame paints, `ViewerWindow` now submits a finite
book-wide display-unit order: current, the immediate preferred-direction unit,
the immediate opposite unit, and all remaining units by distance with the
preferred side winning ties. The runtime still has exactly one active unit
job, generates preview source plus layout-specific display-ready frame, and
checks each background key against combined byte admission. A key that cannot
fit is skipped for that replaceable request rather than ending the whole
frontier, so later small pages can still populate the remaining memory. The old
legacy ten-page cap is replaced by total book pages as a safety ceiling,
leaving the byte budget authoritative. Enlarging the budget clears
capacity-only skips and drives the already paint-released frontier immediately,
without a timer or a retry loop for broken/start failures.

A new request replaces the unstarted order immediately. A running background
job is retained only if it is still the highest-priority missing unit;
otherwise it is cooperatively cancelled and any queued obsolete result is
rejected before QPixmap creation or cache mutation. NivisViewer's stronger
contracts remain outside this borrowed scheduling principle: epoch/request and
layout/DPR stale fences, preview/full tiers, complete-spread atomic commit,
old-frame retention, synchronous ready hit, matching-paint acknowledgement,
high-DPI targets, current-only full-source requests for magnifier, actual size,
manual zoom and pixel mode, and a separately budgeted virtual PageList.

### 18.4 Post-change production probe

The same 24-page offscreen production probe was rerun after the replacement.
On this machine, saved mode `auto` resolved once to 34,359,738,368 bytes
(32,768 MiB) from the following snapshot: 134,909,497,344 total physical
bytes, 78,190,223,360 available bytes, 73,560,064 bytes of process working set
and a 26,981,899,468-byte reserve. Fixed 256 MiB and 4 GiB resolved to exactly
268,435,456 and 4,294,967,296 bytes. All three byte values reached the active
runtime unchanged, and its finite unit ceiling was the 24-page book size.

| Mode | Immediately after first paint | 1 second | 3 seconds | 5 seconds |
|---|---|---|---|---|
| auto / 32 GiB resolved | ready/source `[0]`; 3,289,600 B | ready/source `[0..23]`; 70,181,312 B | unchanged | unchanged |
| fixed 256 MiB | ready/source `[0]`; 3,289,600 B | ready/source `[0..23]`; 70,181,312 B | unchanged | unchanged |
| fixed 4 GiB | ready/source `[0]`; 3,289,600 B | ready/source `[0..23]`; 70,181,312 B | unchanged | unchanged |

At the first paint, frame and source stores each held 1,644,800 bytes, the
full work order was `[0]` through `[23]`, and one job/callback/read/decode had
completed. At one second, all 24 pages had display-ready frames, with
34,984,416 frame bytes and 35,196,896 source bytes: 70,181,312 bytes
(66.93 MiB) combined. There were no source-only pages, admission declines or
evictions. Totals were 25 jobs, 25 callbacks, 24 entry reads, 24 decodes and 25
QPixmap creations; the extra frame/job is the same initial layout/spec
transition observed in the baseline. The runtime then remained idle and
unpaused through five seconds.

This fixture fits below even the 256 MiB budget, so equal final residency is
expected and does not mean the settings collapsed to one value. The important
before/after difference is that the untouched book stopped at two ready pages
and 5.763 MiB before the replacement, but reached all 24 ready pages and
66.93 MiB after it. The byte limits themselves remained distinct and exact.

Warm navigation through the first twenty pages produced 19 hits and zero
misses, jobs, reads or decodes. A ten-page forward then five-page reverse
sequence produced 15 hits and again zero jobs, reads or decodes, ending on page
5. While background work was active, a request for page 17 cancelled one old
background attempt and rejected one stale result; no obsolete frame committed,
page 17 was the only new presentation commit, and the replacement order began
`17, 18, 16, 19, 15`. Closing while page 18 background work was active took
125.0 ms and drained the worker, queued callback and archive lifetime.

A forced low-budget check used 3,289,601 bytes, approximately current plus one
byte. It stabilized at 3,077,120 combined bytes with only page 0 ready, one
admission decline and no retry loop. Current display therefore remains allowed
under pressure while optional warm-up stops. The fixture uses solid JPEGs, a
hot temporary ZIP, synthetic/offscreen paint and no native input or DWM/GPU
presentation. These results establish population, priority and memory
contracts; they do not by themselves claim the user's physical-device page
turn latency.

### 18.5 Admission hardening, store scaling, and remaining boundaries

The post-probe safety follow-up extends exact worker-side admission beyond the
one-axis JPEG case. A lazy PNG, WebP or other non-JPEG background unit is
allowed through the GUI boundary only with a non-mutating capacity reservation.
Before allocating any pixel raster, the one runtime worker probes every unknown
member of that display unit, accounts for all missing retained sources at their
logical raster sizes, derives the complete layout-frame cost, and compares the
sum with current free bytes plus artifacts strictly below the candidate in the
latest retention order. ZIP probing uses the cancellable
sequential entry device and `QImageReader` header path; Folder probing uses its
source header boundary. Probe failure or an over-budget result declines only
that background key. `_drive` continues with later keys, so one exceptional
image cannot prevent smaller pages from filling otherwise usable memory. A
new request or larger budget retries capacity skips; broken entries and worker
start failures remain suppressed. An image that becomes current is not denied
by this optional-prefetch rule and retains the existing protected-current soft
overflow contract.

The reservation phase removes nothing. Only a successful result that still
matches the active request re-plans from its actual QImage sizes. It completes
every QPixmap upload first, then reclaims the required lower-rank source/frame
artifacts immediately before store insertion. Cancellation, stale completion,
upload failure and exact admission decline therefore preserve the old ready
cache; a successful page turn can still slide a full cache window toward the
new current page. The bounded transient is the single active worker result and
its GUI upload outside the retained-cache ledger.

Both source and frame stores now maintain aggregate bytes incrementally on
insert, replacement and removal. Their largest-artifact query uses a bounded
lazy heap ledger, while active work-order rank maps and a lazily built
worst-first candidate order avoid rescanning and sorting the full store for
every item removed during one pressure event. Protection remains store-aware:
current/required sources and current/last-painted frames are excluded from
their respective candidate orders. A live budget reduction first cancels a
non-current job carrying the obsolete larger admission snapshot, applies
byte-ranked eviction, then re-drives the latest order under the new limit. A
live increase clears only capacity declines and resumes the paint-released
order. An in-flight job adopts the larger allowance and is cancelled only when
a newly affordable, higher-priority skipped unit must overtake it.
These are NivisViewer scaling and lifetime changes; no additional ZipPlaFork
C# statement was copied for them.

The remaining boundaries are explicit:

| Residual | Current behavior and risk |
|---|---|
| Lazy-size wide/spread topology | A decoded preview/full source survives size discovery and can be reused. If the newly known aspect changes single, spread-partner or wide-split topology, however, the old layout key is not a complete frame for the new unit. A display-ready frame must be generated again, without another source decode when the retained tier is sufficient. |
| Very large books | Resolved in section 19: topology is built once per layout revision and navigation replaces only a lazy current-centered cursor. Physical-device responsiveness around 10,000 pages remains unverified, but the former per-input full-order materialization is gone. |
| PageList memory ownership | `ViewerPageListRuntime` remains deliberately separate with an explicit fixed 32 MiB QImage budget. It is neither derived from nor live-resized by `viewer_memory_mode`, so the main source+frame authority cannot silently change thumbnail retention. |

These residuals do not change the 24-page results in section 18.4. They limit
the claim to the measured ordinary book and establish the next optimization
boundaries rather than asserting constant-time ready hits for every book size
or topology transition.

## 19. Memory-driven raster cache ownership (2026-08-21 worktree)

This section records the cache-subsystem replacement visible in the current
worktree. It supersedes the count/frontier descriptions in sections 18.3 and
18.5 where they conflict. The policy, lazy planner, admission owner and
count-free source/frame stores are connected to the ZIP/Folder production
runtime and `ViewerWindow`; physical-device behavior remains explicitly
unverified.

### 19.1 Fixed-revision provenance and adopted boundary

The source reference remains
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.  Its retained
license and copyright material remain at `licenses/ZipPlaFork/AGPL.txt` and
`licenses/ZipPlaFork/About.txt`; the notices in section 1 and
`THIRD_PARTY_NOTICES.md` apply to this structural port.

The following table records the actual fixed-revision methods, not only the
general idea of a memory-aware cache:

| ZipPlaFork source / method | Fixed-revision behavior | Adopted NivisViewer principle |
|---|---|---|
| `source/ZipPla/ViewerForm.cs`, `GetUserMemoryUBound` (`:5442-5449`) | Caps the requested Viewer memory by physical RAM and process address-space constraints. | Resolve one explicit Viewer hard ceiling from the saved mode and machine snapshot rather than deriving residency from a page count. |
| `ViewerForm.SetMemoryUBound` (`:5424-5439`) | Recalculates a usable bound from process working set, available physical memory and active/inactive coefficients of 0.7/0.3. | Keep separate active/inactive population targets and react to physical-memory pressure, while using stable modern policy state rather than copying the exact formula. |
| `ViewerForm.SetNewResizedImage` (`:5451-5468`) | Recomputes the bound, makes room with `ReduceUsingMemory`, publishes a completed `VirtualBitmapEx` only if it can be admitted, otherwise disposes and requeues it. | Perform non-mutating admission before decode where possible and commit replacement/eviction only for a successful, still-relevant completed artifact. |
| `ViewerForm.ReduceUsingMemory` (`:5471-5524`) | Walks the low-priority tail of the latest work order, disposes resized and retained original ownership together, and uses a small count only as a minimum-neighborhood guarantee. | Evict from the least useful retention rank under byte pressure; current, spread partner and the last painted frame remain protected.  Page count is not a maximum-residency contract. |
| `ViewerForm.SetBackgroundMode` (`:5558-5582`) and `priorityLevel` (`:5586-5605`) | Builds an all-page order around current/next/previous/remaining bands, installs it, reduces memory, and runs the Viewer worker with thread count one. | Represent the whole book as current-centered work, re-prioritize it on navigation and run one active display-unit job. |
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`) and completion selection (`:266-303`) | Replaces the permutation while work is active; at a completion boundary it skips already-started entries and selects the first unfinished item from the newest order. | Replace unstarted background work around the latest current unit, while keeping NivisViewer's cooperative cancellation and request/epoch stale fences. |

This remains a direct AGPL-derived **structural** port, not a line-for-line C#
translation.  NivisViewer does not adopt `ulong.MaxValue` as an Auto sentinel,
GDI `Bitmap` arrays, WinForms active-form checks, the exact 0.7/0.3 equation,
or a full-array sort on every page turn.

### 19.2 Resolved memory policy

`app/viewer_memory_policy.py` now separates the configured ceiling from the
normal background-population target:

- `ViewerMemoryResolution.hard_limit_bytes` is the byte-exact fixed mode or the
  stable Auto bucket.  Compatibility properties `bytes` and `mib` expose the
  same hard ceiling to callers not yet migrated to the explicit names.
- `active_soft_target_bytes` is seven eighths (87.5%) of the hard ceiling and
  leaves room for one decoder result, QImage/QPixmap upload and Qt/native
  allocations that are not fully visible to the retained-artifact ledger.
- `inactive_soft_target_bytes` is one half of the hard ceiling.  Changing
  active state selects a different soft target; it does not reinterpret the
  saved setting or change the hard ceiling.
- The OS reserve is the larger of 2 GiB or 20% of total physical RAM, bounded
  by total RAM.  Existing cache is added back once when available memory is
  evaluated, while non-cache process working set is charged separately.
- Auto still resolves to a stable 128 MiB through 32 GiB bucket.  A live
  pressure sample may lower the pressure ceiling immediately, in 16 MiB
  granularity, but does not make Auto's hard ceiling flap.  Recovery requires
  meaningful headroom and grows by at most the larger of 64 MiB or one eighth
  of the hard ceiling per observation.  Only an explicit setting
  `reconfigure` resolves a different Auto hard ceiling.
- `ResolvedViewerMemoryPolicy.debug_values()` exposes saved mode, hard limit,
  both soft targets, selected target, reserve, pressure ceiling, cache bytes
  and the physical-memory snapshot to tests/benchmarks.  It emits no
  production log.

The current `ViewerWindow` worktree owns one `ResolvedViewerMemoryPolicy`,
samples physical memory on a five-second timer and on active/inactive changes,
and exposes both the hard budget and selected soft target.  `minimal` and the
fixed modes therefore remain hard ceilings even under pressure; pressure
changes how far optional background population may proceed.  Required current
display work remains outside the optional-background soft gate and may
temporarily overflow it, subject to subsequent low-priority reduction.

### 19.3 Lazy book-wide planner and page-count removal

`app/raster_warmup_planner.py` separates immutable book topology from one
navigation plan.  `ViewerWindow._raster_book_topology` walks the PageModel
display-unit boundary once per book generation, source identity and topology
revision.  A page turn then creates a small `RasterWarmupPlan`; it does not
materialize or sort a new all-page list.

After the first complete frame is accepted by the presentation commit, the
plan releases a **Startup Ready Runway** of up to four complete display units
in the preferred/navigation direction plus one in the reverse direction, in
`F1, R1, F2, F3, F4` priority order.  It
then continues seamlessly through the same lazy book-wide walk without waiting
for physical paint.  Paint acknowledgement remains the displayed-frame
ownership/reclamation and deferred-UI boundary; it is no longer a scheduling
gate.  Section 20 records why the preceding one-neighbor/paint-gated contract
made the available byte budget appear ineffective and how this runway replaces
it.

The 4 + 1 values are a startup minimum target, not a page-count ceiling.  Each
candidate is still admitted against the combined decoded-source plus
layout-frame byte policy, so a hard/soft limit, an oversized unit, a book end
or an already-ready/terminal unit can yield fewer new artifacts.  The normal
iterator still represents every single/spread display unit in the book and
creates candidates only as the one worker asks for them.  Direction reversal
or any navigation creates a new current-centered plan and therefore replaces
the unstarted cursor; completed artifacts remain reusable.  Capacity skips,
the unprocessed hint and explicit waiting/running/soft-target/hard-limit/
complete/suspended stop reasons belong to `RasterWarmupPlanner`, not to a
timer, paint event or cache membership.

Neither source nor frame store accepts a maximum page/unit count.
`cache_unit_limit` and the `set_cache_limits(unit_limit=...)` compatibility
surface have also been removed from `RasterBookRuntime`; callers, tests and
current runtime benchmarks use byte limits only. `BookSession` constructs ZIP
and Folder runtimes from the combined byte budget without supplying a page
count. Thus the removal boundary is explicit:

| Count-like value | Current classification |
|---|---|
| Raster artifact count | Diagnostic only; no constructor/property/setter count limit remains. |
| Book topology length | Finite iteration and identity safety boundary; not a cache cap. |
| current/spread/next/previous neighborhood | Minimum protection/priority band; not a maximum. |
| `ImageCache.cache_size` and legacy image prefetch counts | Retained for PDF, RAR/7z, custom sources and the non-raster pipeline; not the ZIP/Folder runtime membership rule. |
| PageList thumbnail count/range | Separate virtualized PageList ownership, outside the main raster source/frame ledger. |

Consequently a small-page book may retain hundreds or thousands of completed
units until the byte target is reached, while a large-image book naturally
retains fewer.  Reaching 24, 32 or 64 units is not itself a reason for the
raster worker to become idle.

### 19.4 NivisViewer-specific modern cache contract

The memory/work-order principle is combined with NivisViewer behavior that is
not copied from ZipPlaFork:

- `_ZipRasterSourceStore` owns decoded QImages separately from
  `_ZipRasterFrameStore`'s layout/DPR/rotation-specific complete frames; their
  actual byte costs form one combined ledger at the runtime boundary.
- Normal fit warm-up stores a sufficient preview source and a display-ready
  frame.  Full decoded source is promoted for the current page only when
  magnifier, actual size, manual zoom, nearest-neighbor source semantics or another explicit
  full-resolution demand requires it.  A sufficient preview/full tier can
  survive a frame-layout invalidation and avoid another archive/file decode.
- Retention rank comes from the current lazy plan and direction.  Current
  display unit, complete spread partner and last-painted frame are protected;
  obsolete layout/DPR/rotation variants, unnecessary full sources and distant
  opposite-side artifacts are natural low-priority candidates.
- Admission calculates free bytes plus reclaimable strictly lower-rank
  artifacts without first deleting them.  Unknown-cost background pages are
  header-probed by the one worker.  A stale, cancelled, failed or oversized
  result leaves the existing ready cache intact; one skipped oversized page
  does not end the remaining book-wide walk.
- A successful relevant result is rechecked using actual QImage/frame costs.
  Every QPixmap upload must then succeed before lower-rank ownership is
  reclaimed and the new artifact is inserted. This preserves ready-hit reuse
  and avoids destroying the old frame for a speculative decode or failed GUI
  upload.
- Request/source epoch, layout and DPR validation, complete-spread atomic
  commit, old-frame retention through replacement paint, high-DPI targets,
  virtual PageList isolation and callback-drained shutdown remain authoritative
  NivisViewer contracts.

`app/raster_admission_policy.py` is the standalone soft/hard decision owner
(`ADMIT`, reclaim, exact probe, soft stop, hard refusal and oversized skip) used
by `RasterBookRuntime`. Known costs are decided before dispatch; unknown PNG/
WebP-like sizes are header-probed by the one worker and reported through the
same capacity/oversized diagnostics before pixel decode. The lazy planner
pauses at soft target without consuming the deferred unit, and a later target
increase resets that cursor and resumes immediately. Final 200-500-page
population figures are recorded below after the reproducible CLI run;
physical-device responsiveness remains validation work rather than an inferred
result.

### 19.5 Reproducible 300-page population result

`scripts/benchmark_raster_memory_population.py` drives the production ZIP or
Folder runtime directly in a fresh offscreen child process. It paints and
acknowledges the first complete frame, then samples at 1, 3 and 10 seconds.
Fixtures live only in a temporary directory; no application window or native
input is created. The default safe matrix uses 300 JPEG pages, a 400 x 600
physical-pixel target, small 240 x 360 images, large 2400 x 3600 images, and an
alternating mixed book. The 4 GiB/Auto cases remain bounded to at most
576,000,000 retained source+frame pixel bytes and do not reserve their hard
limit up front.

On the measured 128-GiB host, resolved hard/active-soft values were:

| Mode | Hard limit | Active soft target |
|---|---:|---:|
| 256 MiB | 268,435,456 B | 234,881,024 B |
| 4 GiB | 4,294,967,296 B | 3,758,096,384 B |
| Auto | 34,359,738,368 B (32 GiB bucket) | 30,064,771,072 B (28 GiB) |

Every first-paint sample had exactly one ready page, one running worker and
stop reason `running`. The following values are from the second complete run:

| Source/profile/mode | First paint / cache | Ready at 1s / 3s / 10s | 10s cache | Final state / capacity skips | Read / decode / duplicate | WS delta |
|---|---:|---:|---:|---|---:|---:|
| ZIP small 256 | 6.498 ms / 691,200 B | 155 / 300 / 300 | 207,360,000 B | complete / 0 | 300 / 300 / 0 | 108,257,280 B |
| ZIP large 256 | 14.600 ms / 1,920,000 B | 87 / 122 / 122 | 234,240,000 B | complete_with_skips / 178 | 122 / 122 / 0 | 121,462,784 B |
| ZIP mixed 256 | 7.377 ms / 691,200 B | 112 / 179 / 179 | 233,088,000 B | complete_with_skips / 121 | 179 / 179 / 0 | 129,900,544 B |
| Folder small 256 | 8.339 ms / 604,800 B | 154 / 300 / 300 | 181,440,000 B | complete / 0 | 300 / 300 / 0 | 186,617,856 B |
| Folder large 256 | 18.065 ms / 2,580,000 B | 81 / 89 / 89 | 229,620,000 B | complete_with_skips / 211 | 89 / 89 / 0 | 235,204,608 B |
| Folder mixed 256 | 8.619 ms / 604,800 B | 110 / 144 / 144 | 229,305,600 B | complete_with_skips / 156 | 144 / 144 / 0 | 239,611,904 B |
| ZIP mixed 4 GiB | 6.659 ms / 691,200 B | 112 / 300 / 300 | 391,680,000 B | complete / 0 | 300 / 300 / 0 | 217,829,376 B |
| ZIP mixed Auto | 6.458 ms / 691,200 B | 111 / 299 / 300 | 391,680,000 B | complete / 0 | 300 / 300 / 0 | 219,541,504 B |
| Folder mixed 4 GiB | 10.664 ms / 604,800 B | 109 / 296 / 300 | 477,720,000 B | complete / 0 | 300 / 300 / 0 | 500,289,536 B |
| Folder mixed Auto | 8.538 ms / 604,800 B | 109 / 297 / 300 | 477,720,000 B | complete / 0 | 300 / 300 / 0 | 500,363,264 B |

All cases finished with source-only count zero, no duplicate decode, no
eviction, no oversized skip, no stale/cancel result and no soft/hard overflow.
In the 256-MiB large/mixed cases, `complete_with_skips` means that the planner
visited the complete book and skipped units that could not be admitted below
the active soft target; `missing_ready_page_count` records those pages while
`unprocessed_unit_count` correctly reaches zero. One page did not halt later
smaller work. The 4-GiB and Auto mixed books populated all 300 pages, proving
that 24/32/64 pages are not hidden stop conditions.

Every child exited normally. Runtime callbacks drained, active job count and
unfinished task count reached zero, ZIP/folder sources closed, fixtures were
renameable before teardown, and temporary directories were removed. Shutdown
took 12.950--29.778 ms.

The pre-change ad-hoc baseline is not a valid numeric A/B for ready counts: it
used 160 x 240 small pages and populated the 256-MiB hard limit, whereas this
benchmark uses 240 x 360 pages and intentionally stops optional work at the
active soft target. Its `195 ready / 266,014,320 B` therefore must not be
compared with `122` or `179` as a regression. The valid contract comparison is
that both old and new 4-GiB/Auto runs can retain all 300 pages, while the new
path additionally has no count cap, an explicit soft/hard boundary, lazy
book-wide completion and exact skip/restart diagnostics.

### 19.6 Remaining validation boundary

The offscreen results demonstrate memory-driven population and resource
lifetime, not physical-device smoothness. Task-local decoder buffers, QPixmap
upload and Qt/native allocations are transient and cannot be charged exactly
to the retained ledger; process working set is therefore expected to differ
from `cache_used_bytes`. The five-second pressure sampler, active/inactive
shrink and incremental recovery are covered by policy/runtime tests, including
preservation of an unrelated in-flight decode during recovery. Real-device
checks must still cover long idle warm-up, a page turn during that warm-up,
direction reversal, magnifier/full-source promotion, window deactivation and
returning to an already warmed page.

## 20. Historical raster book-open critical path and request-scoped Startup Ready Runway (superseded)

This section records the request-scoped four-forward/one-reverse predecessor.
It superseded the one-neighbor/paint gate described by the earlier wording in
sections 18.3 and 19.3, but is itself superseded by the persistent book-scoped
planner and presentation-surface ownership in section 21.  Its controlled A/B
tables remain useful historical evidence for the removed paint-idle gate; its
planner creation/release lifecycle is no longer the production contract.  The
byte-driven stores, PresentationState and complete-frame atomic swap remain in
force.  Physical-device behavior is not inferred from either structural
change.

### 20.1 Fixed-revision ZipPlaFork book-open call sequence

The reference is still
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.  The ordinary ZIP path at that
revision is the following concrete sequence:

```text
ViewerForm.OpenFile(path, ...)
  -> cancel the preceding BackgroundMultiWorker work set
  -> save preceding history / update access time / catalog selection request
  -> getPackedImageLoader
     -> Program.GetPackedImageLoader
     -> PackedImageLoader(..., onMemory=None)
        -> File.OpenRead once and retain the stream in the loader
  -> PackedImageLoader.GetPackedImageEntries or GetPackedImageFullEntries
     -> getZipArchiveEntries
        -> new ZipArchive(retained stream, Read) once
        -> ZipArchive.Entries.ToArray -> image filtering
     -> getNameSortedEntries / logical-name sort
  -> choose currentPage and update the page control
  -> clearResizedImageArray
  -> BackgroundMultiWorker.RunWorkerAsyncWithInterrupt(all-page arguments, true)
     -> bmwLoadEachPage_RunWorkerStarting
        -> promote nextData/loader, allocate page arrays, SetBackgroundMode
        -> priorityLevel + BackgroundMultiWorker.SetWorksOrder
     -> BackgroundMultiWorker starts WorksOrder[0]
     -> bmwLoadEachPage_DoWork(the first page in that numerical priority band;
                               normally current, not a topology guarantee)
        -> PackedImageLoader.OpenImageStream / OpenInnerImageStream
        -> entry stream, decode, orientation/filter, resize, VirtualBitmapEx
     -> bmwLoadEachPage_EachRunWorkerCompleted
        -> SetNewResizedImage -> completed artifact publication
     -> BackgroundMultiWorker.privateRunWorkerCompletedEventHandler
        -> select the first unfinished item in the newest order immediately
```

The source locations are `source/ZipPla/ViewerForm.cs:2308-2771`
(`OpenFile`), `:2919-3003` (`bmwLoadEachPage_RunWorkerStarting`),
`:3177-3602` (`bmwLoadEachPage_DoWork`), `:5371-5415` (completion),
`:5451-5468` (`SetNewResizedImage`) and `:5558-5605`
(`SetBackgroundMode` / `priorityLevel`); `source/ZipPla/PackedImageLoader.cs`
`:283-330` (retained file stream), `:1197-1243` (`getZipArchiveEntries`) and
`:1781-1867` (entry-stream access); and
`source/ZipPla/GenerarClasses.cs:247-264,266-339`
(`SetWorksOrder` and latest-order completion selection).  The configured
Viewer worker count is one in `ViewerForm.Designer.cs:1628-1636`.  Revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` is the fixed source snapshot, not
the commit that introduced that Viewer setting: the revision itself changes
only `CatalogForm.ThumbViewerItem` thumbnail concurrency.  The audited Viewer
worker and methods were already present in base import/core commit
`5e942ef0006decf01c21d9dbe037dba50e763420`.

ZipPlaFork therefore does perform synchronous work before the first page job:
history/catalog calls, archive entry enumeration and sorting, slider/control
projection, old resized-array disposal and the worker-starting array setup are
not free.  Its important work-order property is narrower: once the current
display artifact is published, the same one-worker scheduler can select the
next ordered page at that completion boundary.  It has no Qt physical-paint
acknowledgement gate.  The ordinary loader retains one archive stream/reader;
copying the whole archive to memory is an optional mode, not the default path.

This replacement does **not** copy ZipPlaFork's pre-open old-frame clearing,
synchronous archive enumeration, WinForms control updates, GDI bitmap arrays,
weak request identity or wait-cancel ownership.  Those would regress
NivisViewer's asynchronous open, rollback frame, high-DPI and stale-result
contracts.

### 20.2 NivisViewer path before this worktree change

Archive open and entry enumeration were already off the GUI thread, but the
GUI handoff performed several non-current operations before it could request
the first frame:

```text
ViewerWindow.open_path
  -> _save_current_reading_position
     -> MetadataStore.update_progress -> MetadataStore.flush (synchronous)
  -> BookSession.open_book_async
     -> _BookOpenWorker.run
        -> source factory -> ZipImageSource.__init__
           -> zipfile.ZipFile retained for the book lifetime
        -> ZipImageSource.list_images
           -> infolist, extension/display-name filtering, natural sort
  -> BookSession._on_async_prepared                         [GUI]
     -> PageModel.set_prepared_source
     -> _replace_viewer_runtime -> old runtime shutdown/cache clearing
     -> _replace_page_list_runtime -> thumbnail runtime construction
     -> ImageCache.set_source -> duplicate source and full image-id ownership
  -> ViewerWindow._finish_opened_book                      [GUI]
     -> MetadataStore.record_book_opened
        -> history_changed -> HistoryModel.refresh
     -> _add_recent_path -> recent QAction projection
     -> _refresh_view -> topology/current runtime request
     -> book_changed -> Browser/book projection
  -> RasterBookRuntime current read/decode/frame job
  -> ViewerWindow._on_zip_runtime_frame_ready
     -> QPixmap-backed complete-frame atomic commit
  -> ViewerWidget.paintEvent / framePainted
     -> release the whole background warm-up order
```

`_BookOpenWorker.run`, `ZipImageSource.__init__` and
`ZipImageSource.list_images` were not the GUI-thread regression: they kept one
ZIP handle and produced the page index in the open worker.  The avoidable
critical-path costs began at `BookSession._on_async_prepared` and
`ViewerWindow._finish_opened_book`.  They included destruction of a potentially
large preceding source/frame cache, eager PageList ownership, an unused legacy
`ImageCache` page-list copy, synchronous SQLite/history and recent-action
projection, and Browser work made eligible before the current frame.  The
runtime then left its one Viewer lane idle between accepted current commit and
physical paint even when the next display unit was known and admissible.

The first topology/index construction remains required once per book/layout
revision, and the current page still cannot decode until asynchronous ZIP
listing and natural sorting complete.  This change does not hide those costs;
it removes unrelated ownership/projection work around them.

### 20.3 Historical request-scoped Hybrid sequence (superseded)

At this predecessor stage, the ZIP/Folder production handoff was:

```text
ViewerWindow.open_path
  -> save preceding progress without a synchronous metadata flush
  -> asynchronous source open, ZIP listing and PageModel construction
  -> BookSession installs the new RasterBookRuntime with explicit hard/soft bytes
     -> retire old Viewer/PageList work without clearing retained artifacts
     -> defer replacement PageList construction
     -> ImageCache.suspend_for_book_runtime (fully detached legacy owner)
  -> ViewerWindow stages book-open metadata/recent/book_changed projection
  -> submit only the current complete display unit
  -> worker read/decode/render -> validated complete RasterFrame
  -> atomic PresentationState/ViewerWidget commit
     -> slider/status/displayed state update together
     -> release_startup_runway(request_id)
        -> prioritize up to four forward + one reverse complete display units
        -> continue through the lazy book-wide order with the same one worker
  -> matching ViewerWidget paint acknowledgement
     -> release_prefetch(request_id): mark painted ownership / reclaim safely
     -> start the zero-delay presentation-side-effect timer
  -> after paintEvent returns
     -> construct/activate the virtual PageList runtime
     -> reclaim retired Viewer/PageList/source ownership
     -> record book-open metadata, recent actions and book_changed/Browser projection
     -> project page history/actions/PageList selection and persist committed progress
```

`ViewerWindow._on_zip_runtime_frame_ready` releases startup work only after the
request/book epoch, source identity, complete display-unit identity, layout/DPR
contract, accepted presentation token and positive committed frame serial have
all survived validation.  `RasterBookRuntime.release_startup_runway` is
idempotent per request and also requires the current complete frame to exist in
the frame store.  `RasterWarmupPlanner.release_startup_runway` makes up to four
preferred-direction and one reverse-direction **complete display units** the
priority prefix, ordered `F1, R1, F2, F3, F4`.  When those topology units exist
and pass byte admission, making all five ready before lower-ranked work is the
startup guarantee.  A two-page spread consumes one unit, not two slots; a split
wide page is never exposed half-complete.  When this prefix is ready, terminal
or safely declined by byte admission, `next_candidate` proceeds directly into
the ordinary lazy whole-book iterator.  It does not return to a paint-waiting
state.

The exact cause of the former “only one neighbor is ready” symptom was the
intermediate `release_after_commit(unit_limit=1)` contract: after one useful
missing display unit it explicitly restored `waiting_for_paint`, leaving the
single worker idle until a matching physical-paint acknowledgement opened the
remaining order.  The combined cache still had free bytes, so increasing its
budget could not create work hidden behind this scheduler admission gate.  The
new 4 + 1 runway removes that gate; it is a startup minimum target and priority
prefix, **not** a retention limit.  Every candidate is still admitted against
the combined decoded-source plus layout-frame hard/soft byte policy.  A small
budget, oversized artifact or a book boundary may therefore produce fewer than
five newly ready units, while a large budget can retain and warm far more than
five.

The runway is topology-based rather than screen-coordinate-based.  In single
or spread mode it follows complete `RasterDisplayUnit` entries; LTR/RTL and the
current navigation direction determine the preferred side; wide/split units
remain atomic; and a start/end boundary neither wraps nor duplicates units.
Every accepted navigation or direction reversal constructed a fresh
current-centered planner and request-scoped release.  Unstarted old-direction
work disappeared, an active job was adopted only when it was the exact first
missing unit of the replacement runway, and otherwise it was
cancelled/stale-rejected.  Completed old-direction artifacts remained reusable
under the byte budget.  Within one request, the runway continued into the lazy
book-wide iterator without a paint gap; navigation nevertheless discarded that
planner owner and its scan/admission state.  Section 21 replaces this remaining
reset boundary.  Physical paint still acknowledges ownership, releases the
preceding displayed-frame protection and gates PageList/metadata/history/
Browser projection and retired-resource cleanup; it does not decide whether
raster warm-up may continue.

This is intentionally Hybrid.  It adopts ZipPlaFork's useful completion-boundary
continuation but keeps NivisViewer's Qt publication boundary: QPixmap creation,
atomic old-frame replacement and physical-paint acknowledgement are unchanged.
PageList, SQLite/history/progress, recent-action rebuild, Browser projection
and large retired-cache destruction remain paint-gated and are run by
`ViewerWindow._flush_presentation_side_effects` on its zero-delay timer, outside
the `paintEvent` call stack.  Slider and status remain part of the accepted
atomic presentation commit; moving them after paint would reintroduce a split
displayed-page owner.

### 20.4 Ownership, memory-setting and lifecycle cleanup

| Boundary | Before | Current worktree contract |
|---|---|---|
| Main raster source owner | `RasterBookRuntime` plus an attached, suspended `ImageCache` with another full image-id list | `PageModel` owns page metadata and `RasterBookRuntime` owns ZIP/Folder decode/frame state; `ImageCache.suspend_for_book_runtime` cancels legacy work and clears its source and IDs. |
| Runtime memory authority | New runtimes were initially seeded through the legacy `ImageCache` budget, then corrected by Window policy | `BookSession.set_viewer_runtime_memory_limits` owns explicit hard/soft byte values used both for construction and live updates. |
| Persisted Raster memory setting | `viewer_memory_mode` coexisted with normalized/saved `viewer_cache_max_memory_mib` and prefetch presets still exposed a hidden memory field | `viewer_memory_mode` is the sole Raster memory authority. `viewer_cache_max_memory_mib` is accepted only for one-time migration when no explicit mode exists, is removed from normalized state and is never re-saved. A newer explicit mode wins. |
| Legacy format settings | Raster and legacy meanings were entangled | `cache_size`, image/PDF forward/backward units and preset/direction controls remain for PDF, RAR/7z, custom/non-raster paths; their continued existence is not a Raster cache cap. |
| PageList memory | Derived as one quarter of the main budget, clamped to 8--64 MiB | An explicit 32 MiB `ViewerPageListRuntime` QImage budget defines an independent virtual-thumbnail boundary; the runtime itself is not constructed until first paint. |
| Replacement cache destruction | Old Viewer/PageList runtime shutdown could clear a large ready cache during new-book GUI installation | `RasterBookRuntime.retire` and `ViewerPageListRuntime.retire` stop acceptance/cancel work but retain artifacts. `BookSession.release_retired_book_resources` performs final shutdown after the replacement paint. |
| Failed/no-paint/rapid switch | Cleanup relied primarily on normal runtime idle/paint flow | Failed replacement keeps the active presentation. The bounded no-paint fallback schedules the same post-paint projections. `close_book` releases deferred owners, and a raster A -> raster B -> non-raster C chain releases them before C because no later raster paint exists. Running callbacks retain their source through the existing retired-source drain maps. |

The concrete NivisViewer owners are `app/viewer_window.py`
(`open_path`, `_finish_opened_book`, `_save_current_reading_position`,
`_apply_book_open_projection`, `_flush_book_open_projection`,
`_on_zip_runtime_frame_ready`, `_on_zip_runtime_frame_painted` and
`_flush_presentation_side_effects`); `app/book_session.py`
(`_on_async_prepared`, `_replace_viewer_runtime`, `_replace_page_list_runtime`,
`ensure_page_list_runtime`, `set_viewer_runtime_memory_limits` and
`release_retired_book_resources`); `app/image_cache.py`
(`suspend_for_book_runtime`); `app/zip_raster_book_runtime.py`
(`RasterBookRuntime.release_startup_runway`, `_first_missing_startup_key`,
`release_prefetch`, `_adopt_request`, `retire` and `_drive`);
`app/raster_warmup_planner.py` (`RasterWarmupPlan.startup_runway_units`,
`RasterWarmupPlanner.release_startup_runway`, `next_candidate` and
`release_after_paint`); `app/viewer_page_list_runtime.py` (`retire`); and
`app/config_manager.py` (legacy memory-key migration and removal).

Deferring retirement intentionally permits the preceding completed frame/cache
to coexist with the new current decode for one replacement-paint interval.  It
prevents a large GUI-thread deallocation burst before current dispatch and
preserves rollback, but may cause a short transient working-set peak.  Close,
non-raster transition and queued-callback drain remain explicit terminal
boundaries rather than relying on object finalizers.

### 20.5 Historical adoption decision and AGPL provenance

| Fixed-revision source / method | Principle evaluated | NivisViewer result |
|---|---|---|
| `ViewerForm.cs`, `bmwLoadEachPage_RunWorkerStarting`, `SetBackgroundMode`, `priorityLevel`; `GenerarClasses.cs`, `SetWorksOrder` / completion selection | One Viewer lane receives current-centered work and chooses the next useful item at a completion boundary without a paint-idle gap. | **Superseded Hybrid predecessor:** `RasterBookRuntime.release_startup_runway` and a request-created `RasterWarmupPlanner` supplied a four-forward/one-reverse prefix. Section 21 retains the priority principle but replaces request-scoped planner ownership with one persistent book-scoped owner. |
| `PackedImageLoader.cs`, constructor, `getZipArchiveEntries`, `OpenImageStream` / `OpenInnerImageStream` | One book-scoped archive handle/index and entry-local page work. | **Already adopted/maintained:** `ZipImageSource.__init__`, `list_images`, entry-read methods and `ZipRasterBookRuntime` keep structured lifetime, cancellation and stale fences. No new archive copy is added. |
| `ViewerForm.cs`, `OpenFile`, `clearResizedImageArray` | Replacement preparation and old ownership disposal before new work. | **Not adopted:** old complete output is retained; `BookSession` retires work immediately but moves bulk cache destruction after successful replacement paint. |
| No ZipPlaFork counterpart | Async archive listing, PresentationState, PageList virtualization, post-paint side effects, migration-only settings and callback-drained rapid switching. | **NivisViewer/New design maintained:** these are modern Qt/UX/lifetime structures, not translations of WinForms code. |

The one-worker completion-order and replaceable current-centered order
principles in the first row are derived from the fixed AGPL-3.0-or-later
ZipPlaFork source and are recorded as a direct structural port.  The exact
4 + 1 startup shape, navigation-direction awareness, complete-display-unit
topology, combined Qt byte admission, epoch/request stale cancellation and
paint-independent promotion to a lazy iterator are NivisViewer
modernizations; ZipPlaFork does not contain that Qt contract.  No C# statement,
GDI type or WinForms control code was copied literally for this change.  The
retained license and copyright material at
`licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`, section 1
and `THIRD_PARTY_NOTICES.md` apply.

### 20.6 Historical controlled A/B that exposed the paint-idle cause

This A/B predates the final Startup Ready Runway and is retained as diagnosis,
not as a benchmark of the current 4 + 1 contract.  It compared only the then-new
one-neighbor scheduling boundary, not a checkout of the removed GUI ownership
path.  Both sides used the same generated large ZIP, fixed memory policy,
decoded/frame stores and one-worker runtime in fresh offscreen child processes:

- **A / paint gate:** accept the current frame, withhold
  the then-current `release_initial_warmup`, acknowledge paint, then call normal
  `release_prefetch`.
- **B / one-neighbor predecessor:** accept the same current frame, call the
  then-production `release_initial_warmup`, permit exactly one nearest missing
  unit through `release_after_commit(unit_limit=1)`, then
  acknowledge paint and release the remaining order normally.

The intended matrix is a 100--300-page large-image ZIP under fixed 256 MiB,
fixed 4 GiB and resolved Auto.  It records archive opens/listings, page count,
work submitted before current, current entry read/decode/job/callback counts,
open-to-current commit, commit-to-first-neighbor-ready, first-paint time,
ready/source/frame residency, retained bytes, working-set delta and shutdown
drain.  A controlled commit-to-paint delay may expose worker idle time, but
must be labelled synthetic; it is not physical presentation latency.  A direct
runtime runner also cannot measure deferred MetadataStore, QAction, PageList or
Browser work, so Window-level contract tests remain the evidence for those
boundaries.

The completed run used 200 2400 x 3600 JPEG pages in a 109,721,422-byte ZIP,
a 400 x 600 target and an explicit synthetic 20-ms accepted-commit-to-paint
boundary.  Values below are milliseconds; each A and B ran in a fresh process.

| Mode | A request->commit / commit->next / paint->3 ready | B request->commit / commit->next / paint->3 ready | Ready before paint / jobs before paint, A->B | Ready at 250/500/1000 ms, A->B |
|---|---:|---:|---:|---:|
| fixed 256 MiB | 14.376 / 31.266 / 18.599 | 14.482 / 8.183 / 10.795 | 1/1 -> 2/2 | 29/56/106 -> 31/55/97 |
| fixed 4 GiB | 15.948 / 31.561 / 21.161 | 16.780 / 10.840 / 10.606 | 1/1 -> 2/2 | 29/55/106 -> 28/52/101 |
| Auto | 14.703 / 32.153 / 21.610 | 15.518 / 10.280 / 10.469 | 1/1 -> 2/2 | 29/54/104 -> 28/53/107 |

First-paint latency was 35.013 -> 35.285 ms (256 MiB), 36.410 -> 37.413 ms
(4 GiB) and 35.673 -> 36.220 ms (Auto).  Those 0.272--1.003-ms differences
are within isolated-run noise; the controlled result does not show a material
first-frame regression for the one-neighbor predecessor.  B retained one
additional 1,920,000-byte complete
source+frame artifact before paint.  At one second B retained 186,240,000 /
193,920,000 / 205,440,000 bytes and its working-set deltas were 98,160,640 /
102,424,576 / 107,077,632 bytes for 256 MiB / 4 GiB / Auto respectively.  A's
corresponding values were 203,520,000 / 203,520,000 / 199,680,000 bytes and
106,905,600 / 107,311,104 / 105,340,928 bytes.  The ready-count variation after
250 ms is normal decoder-throughput noise rather than a policy regression.

Every case opened the archive and built the listing once, submitted zero jobs
before the initial request, produced zero duplicate successful decodes and
drained callbacks, closed the source and removed the temporary fixture at
shutdown.  The first-neighbor gap fell from 31--32 ms to 8--11 ms and the
post-paint three-ready gap from 18.6--21.6 ms to 10.5--10.8 ms.  The synthetic
20-ms boundary deliberately amplifies otherwise platform-dependent paint idle;
it is not claimed as physical presentation latency.  More importantly, this
run isolated the former scheduler cause: a useful job could execute at commit,
but `unit_limit=1` deliberately put the worker back behind the paint gate.  It
did not justify retaining that one-unit restriction.

A separate three-second commit-gate reach run verifies that memory mode, not a
hidden page/unit count, remains authoritative:

| Mode | Resolved hard / soft bytes | Ready pages / retained bytes | Jobs / reads / decodes / duplicates | Stop reason / WS delta |
|---|---:|---:|---:|---:|
| fixed 256 MiB | 268,435,456 / 234,881,024 | 122 / 234,240,000 | 122 / 122 / 122 / 0 | `complete_with_skips` / 122,003,456 B |
| fixed 4 GiB | 4,294,967,296 / 3,758,096,384 | 200 / 384,000,000 | 200 / 200 / 200 / 0 | `complete` / 197,177,344 B |
| Auto | 34,359,738,368 / 30,064,771,072 | 200 / 384,000,000 | 200 / 200 / 200 / 0 | `complete` / 197,832,704 B |

All three reach cases had one archive open, one listing, zero evictions and a
clean callback/source/temporary-file shutdown.  Offscreen evidence establishes
the paint-idle diagnosis and absence of a hidden count cap only.  The final
4-forward/1-reverse runway and seamless book-wide promotion supersede both A
and B; no new physical latency number is inferred from this historical table.
Native ZIP I/O, decoder, QPixmap upload, DWM/GPU presentation and physical
wheel feel still require the user's real-device check.

#### 20.6.1 Final request-scoped runway validation (historical)

The final implementation was rerun with a temporary 200-page ZIP containing
identical 2,400 x 3,600 detailed JPEG entries, a 400 x 600 viewport, 256 MiB
hard / 224 MiB soft raster policy, one worker and a deterministic 20 ms
commit-to-paint interval.  Each side ran in a fresh offscreen process.  This
first table isolates the scheduler on the final code: A withholds the commit
release until paint; B calls the production Startup Ready Runway.

| Final-code scheduler A/B | A: paint gate | B: Startup Ready Runway |
|---|---:|---:|
| Request -> first commit ms | 17.836 | 16.797 |
| Request -> first paint ms | 38.842 | 37.274 |
| Commit -> next ready ms | 34.371 | 13.728 |
| Paint -> three ready ms | 26.130 | 7.807 |
| Ready units at first paint / 50 / 100 / 250 ms | 1 / 4 / 8 / 19 | 2 / 6 / 10 / 21 |
| First-five forward hits at first paint / 50 ms | 0 / 3 | 1 / 5 |
| Cache bytes at first paint / 50 / 100 / 250 ms | 3,120,000 / 12,480,000 / 24,960,000 / 59,280,000 | 6,240,000 / 18,720,000 / 31,200,000 / 65,520,000 |
| Duplicate successful decodes | 0 | 0 |

The first-commit difference is isolated-process noise because B is released
only after that commit.  The useful result is the eliminated scheduler idle:
the next and third complete units arrive about 20.6 and 18.3 ms earlier in this
controlled boundary.  B performs exactly the additional reads/decodes needed
for its two extra ready units; job, callback and QPixmap counts remain one per
completed unit.

A separate 100 ms pre-paint reach probe compares the task-start production
(`release_after_commit(unit_limit=1)`) with the final production.  The old
runtime stopped at two ready units (current plus one neighbor), so only one of
the first five forward turns was a hit.  The final runtime reached eight ready
units, making all first five turns hits, and had already submitted the ninth
single-worker job.  The corresponding exact combined-cache values were
3,840,000 versus 24,960,000 bytes.  Both sides reported zero duplicate decode,
drained callbacks, closed sources and unlocked/removed temporary fixtures.

This holistic predecessor/final comparison also contains the resampling
correctness change: the 2,400 x 3,600 JPEG source retained for a 400 x 600
frame is now the sufficient native 600 x 900 tier (2,160,000 pixel bytes), not
the former arbitrary 400 x 600 decoder output (960,000 bytes).  The final
QPixmap remains exactly 400 x 600 (960,000 bytes).  In the 20 ms runs that
changed task-start predecessor versus final population from 2 / 7 / 11 / 28
ready units at first paint / 50 / 100 / 250 ms to 2 / 6 / 10 / 21 and raised
the sampled working-set delta at 250 ms from 31,473,664 to 71,028,736 bytes.
That throughput/memory cost buys a non-undersized reusable source and the
configured one-final-resize quality contract; it is not attributed to the
runway scheduler and still requires physical quality/latency evaluation.

### 20.7 Remaining risk and physical-device boundary

- ZIP central-directory enumeration, display-name decoding and natural sort
  still precede the first current request, although they run in the book-open
  worker.  If open latency remains proportional to entry count, that index path
  is the next measured boundary.
- Up to four preferred-direction and one reverse complete display units can now
  consume CPU/memory bandwidth before the current paint, followed by seamless
  book-wide warm-up.  The combined hard/soft byte policy remains the actual
  retention/admission ceiling; a real-device first-paint or immediate-input
  regression should tune the startup runway/admission policy, not restore a
  paint-dependent scheduler gate.
- PageList construction and retired-cache disposal move after paint, not out of
  the process.  They may affect the immediately following interaction and need
  a physical rapid-open/page-turn check.
- Replacement retirement may transiently retain two book caches.  Rapid raster
  replacement, transition to PDF/RAR/7z, hidden/no-paint fallback and close
  must be checked for bounded memory and zero late-state mutation.
- The persistent ZIP handle and one active decode still depend on Python/Qt
  backend behavior.  Matching ZipPlaFork's work order does not by itself prove
  matching native decoder or renderer latency.

### 20.8 Final validation and shutdown observations

Final syntax/import checks and `git diff --check` passed.  The focused runway,
source-tier, resampling, configuration-migration, Folder/ZIP runtime and Viewer
integration group passed 205 tests.  The complete repository suite was then
split across fresh pytest processes and passed **1,516 tests** across all 89
test files; lifecycle-sensitive partitions were rerun one file per process.
This process boundary is intentional: two earlier broad multi-file processes
aborted during Python/Qt teardown while old Pdfium or Windows shell-preview
workers from preceding tests still existed, although every affected file
passed in a fresh process.  That remains a suite-process lifetime risk rather
than evidence of a raster assertion failure.

Two older integration tests had treated complete-frame commit as if post-paint
metadata/PageList projection had already run.  Their bounded waits now observe
the public reading-progress or PageList epoch/row result, preserving the
production `commit -> paint -> noncritical projection` boundary.  The run also
found and fixed an unrelated real shutdown race: `FileOperationPanel` scheduled
an idle close without a QObject context, so the callable could reach a deleted
`WA_DeleteOnClose` wrapper.  The callback is now context-bound; 76 related file
operation tests passed.

All task-owned pytest and benchmark PIDs were identified by executable,
command line and parent PID, terminated where an aborted process had remained,
and rechecked.  No pytest, benchmark or compile process from this work remains;
unrelated pre-existing Python processes were not touched.  Named benchmark and
stranded pytest temporary directories created by this validation were removed.

## 21. Presentation-surface ownership and persistent book-scoped continuous warm-up (2026-08-21 worktree)

This section supersedes the request-scoped Startup Ready Runway lifecycle in
section 20 while retaining its useful evidence and byte-driven cache contract.
The production change has two independent but user-visible goals:

1. the idle prompt must never regain the canvas after a valid complete frame
   has committed; and
2. one raster-book planner must continue across page requests instead of
   recreating a four-forward/one-reverse startup phase on every navigation.

The fixed ZipPlaFork reference remains
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, licensed
AGPL-3.0-or-later.  As recorded in section 1, this hash is a fixed source
snapshot.  The commit itself changes only the `CatalogForm.ThumbViewerItem`
thumbnail semaphore from processor-count concurrency to one.  It did not
introduce `ViewerForm`'s `bmwLoadEachPage.ThreadCount = 1`; the audited Viewer
implementation is inherited from `5e942ef0006decf01c21d9dbe037dba50e763420`.

### 21.1 Superseded production defects

The former empty-state behavior had no single presentation owner.
`ViewerWidget.paintEvent` inferred the idle prompt solely from an empty local
`_images` list.  During an initial asynchronous open, that list is legitimately
empty before the first complete frame; during replacement it may also be
temporarily detached while the old presentation is still the rollback owner.
The Widget could therefore draw `画像を開いてください` without knowing whether
the application was idle, loading, retaining a replacement frame or reporting
an open error.  A viewport notification also called
`ViewerPresentationState.supersede_pending` before the 120-ms resize debounce
had a replacement request ready.  On a cold initial open this could reject the
only in-flight first frame and leave the locally inferred idle surface visible
until the delayed layout request completed.  A zero-image async source result
could likewise reach the successful installation path without any frame that
could end that interval.

The former warm-up path removed the paint gate inside one request, but still
created a new `RasterWarmupPlanner` in `RasterBookRuntime._adopt_request` for
every navigation.  It reset request release flags, failed/admission suppression
and the lazy iterator, then adopted an active background job only when it was
the exact first missing unit of the replacement 4 + 1 runway.  Consequently a
ready hit or a short direction change could cancel useful work, restart the
same priority prefix and lose the previous book scan/admission ledger even
though completed source/frame artifacts remained cached.  Section 20's 4 + 1
prefix was not a cache limit, but its *owner lifetime* was still too short.

### 21.2 Exact fixed-revision ZipPlaFork work and paint sequence

The reference sequence was re-audited from the fixed Git object rather than
inferred from method names:

```text
ViewerForm.OpenFile
  -> choose currentPage and update the trackbar
  -> clearResizedImageArray
  -> BackgroundMultiWorker.RunWorkerAsyncWithInterrupt(arguments, waitCancel=true)
     -> cancel/drain the preceding work set before installing the replacement
     -> bmwLoadEachPage_RunWorkerStarting
        -> promote nextData and its PackedImageLoader
        -> dispose the preceding loader only after old work has drained
        -> allocate PreFilteredImageArray / OriginalImageInfoArray
        -> allocate ResizedImageArray / ResizedSizeArray for the whole book
        -> SetBackgroundMode
           -> priorityLevel over every page
           -> BackgroundMultiWorker.SetWorksOrder(all-page permutation)
           -> ThreadCount = 1
     -> bmwLoadEachPage_DoWork(work number)
        -> OpenImageStream / decode / pre-filter source retention
        -> target resize / post-filter / complete VirtualBitmapEx
        -> exception becomes a terminal error VirtualBitmapEx
     -> bmwLoadEachPage_EachRunWorkerCompleted                 [UI completion]
        -> if currentPage changed: SetBackgroundMode for latest current
        -> SetNewResizedImage: memory admission and completed artifact publish
        -> if page is in the current visible band: showCurrentPage(false)
           -> Invalidate only; no synchronous paint and no scheduler release
     -> BackgroundMultiWorker.privateRunWorkerCompletedEventHandler
        -> if order changed, restart its cursor at the newest permutation
        -> skip every WorksStarted item
        -> start the first unfinished item immediately
     -> later WM_PAINT / pbView_Paint / pbView_PaintToCanvas
```

The decisive ordering is that `EachRunWorkerCompleted` runs before
`privateRunWorkerCompletedEventHandler` chooses the next page.  A current change
therefore replaces the unstarted order at the completion boundary; the sole
already-running page is not preempted.  Its result remains useful book cache
data, then the next job comes from the latest current-centered permutation.
`showCurrentPage` only calls `Invalidate`, so physical paint is not a worker
gate.

For single-page binding (`MaxPageCountInWindow = 1`), `priorityLevel` produces
current, numerical next, numerical previous, then the remaining book.  For a
two-page binding it produces the current numerical two-page band, the next
numerical two-page band, the previous numerical two-page band, then the rest.
Within a band the normal tie-break is distance from current; while the pointer
is over the trackbar it is distance from the pointer-mapped value.  This is a
fixed page-number band, not the actual wide/cover-aware display-unit topology.
`SetBackgroundMode` also runs before the replacement `BindingMode` is assigned
later in `bmwLoadEachPage_RunWorkerStarting`, so initial ordering can observe
the preceding binding value.  These upstream quirks are not ported.

`SetNewResizedImage` is completed-bitmap cache publication, not a displayed
frame transaction.  It admits a `VirtualBitmapEx` into `ResizedImageArray`, or
disposes and `ReworkOrder`s it.  `ReduceUsingMemory` evicts from the far end of
the latest priority order and disposes the same page's resized artifact and
pre-filtered source.  Completed pages survive navigation until that memory
eviction, reload or book replacement.  Natural next-page input refuses to move
beyond an unfinished display frontier, while direct/trackbar movement can
advance logical current before pixels.  Incomplete spread slots are not drawn,
but their completion can still request a loading paint; `pbView_Paint` applies
a half-gray mask to the retained canvas.  Status is also projected during
paint.  These logical/display and loading semantics are deliberately **not**
ported.

### 21.3 Single owner for EMPTY, LOADING, DISPLAYED and ERROR

`ViewerPresentationState` now owns a monotonic `PresentationSurface` containing
`mode`, `revision` and an optional message.  The possible modes are `EMPTY`,
`LOADING`, `DISPLAYED` and `ERROR`.  `ViewerWindow._project_presentation_surface`
is the sole bridge to `ViewerWidget.apply_presentation_surface`; the Widget
rejects an older revision and no longer derives the mode from image-list
emptiness.  `ViewerWidget.paintEvent` uses the idle prompt only for explicit
`EMPTY`, uses a loading message for `LOADING`, preserves committed pixels for
`DISPLAYED`, and uses the presentation-owned failure text for `ERROR`.

The state transitions are:

| Sequence | Presentation surface and canvas contract |
|---|---|
| Initial idle -> open | `EMPTY -> LOADING`; no `画像を開いてください` inference while the book/source/current frame is pending. |
| First complete frame | `LOADING -> DISPLAYED` in the same accepted presentation commit that updates displayed page, slider and status. Paint and post-paint callbacks do not demote it. |
| Delayed open/resize callback after commit | The Window projects the current state, and the Widget refuses a lower surface revision. A callback from the old interval cannot apply `EMPTY` or clear the frame. |
| Replacement open with an old committed frame | `DISPLAYED` remains `DISPLAYED` while replacement source/current is pending. The old complete frame stays the rollback owner. |
| Replacement failure | With an old committed frame, remain `DISPLAYED`; do not clear or show an empty/error placeholder over it. |
| Initial open failure | With no committed frame, transition to explicit `ERROR`; a cancelled initial open may return to `EMPTY`. |
| Zero readable images | `BookSession` closes the provisional source and emits `ImageSourceError(code="no_images")`; it is not installed as a successful empty book. |
| Explicit close/clear | Only `clear_book`/`close` transitions to `EMPTY`, after which Widget clearing and the idle prompt are valid. |

The viewport path now fences the old physical layout/DPR immediately.  When no
frame has committed, the surface remains explicit `LOADING` and the replacement
layout request is dispatched on the next event turn, rather than after the
normal 120-ms resize debounce.  With a committed frame, that old frame remains
`DISPLAYED` while the replacement layout is debounced.  A viewport event during
a provisional replacement open is recorded but cannot reactivate or request
the still-installed old book; success or failure consumes the live viewport
when it next requests the authoritative frame.  Loading and replacement state
therefore remain presentation concepts, not side effects of
`ViewerWidget.clear()` or an empty `_images` collection.

The concrete ownership is in `app/viewer_presentation_state.py`
(`PresentationSurfaceMode`, `PresentationSurface`,
`ViewerPresentationState.request_frame`, `commit_frame`,
`begin_replacement_open`, `supersede_pending`, `fail_replacement_open`,
`fail_pending`, `clear_book` and `close`); `app/viewer_widget.py`
(`apply_presentation_surface` and `paintEvent`); `app/viewer_window.py`
(`open_path`, `_on_async_book_open_failed`, `_on_zip_runtime_frame_ready`,
`_project_presentation_surface`, `_on_viewport_changed` and close handling);
and `app/book_session.py` (synchronous/asynchronous zero-image rejection).

### 21.4 Persistent book-scoped work-order owner

The request-scoped startup phase is replaced by one planner with the runtime's
book lifetime:

```text
first display request
  -> RasterBookRuntime._adopt_request
     -> create RasterWarmupPlanner once
     -> submit current complete display unit only
first accepted complete commit
  -> release_continuous_warmup(request_id)
     -> RasterWarmupPlanner.release_after_first_commit once
     -> urgent prefix F1, R1, F2, F3, F4
     -> same iterator continues through the remaining book
navigation / ready hit / direction reversal
  -> _adopt_request
     -> recenter the existing planner; do not replace its owner
     -> preserve release state, visited identities and capacity skips
     -> retain ready source/frame artifacts under the byte budget
     -> if current is ready: immediate cache-hit publication, no decode job
     -> if current is cold: current is first in the replacement unstarted order
     -> if the sole job has already started and its book/source/render identity
        is artifact-compatible with the new topology, let it finish and retain
        its artifact even outside the new urgent band
     -> replace/cancel only an unstarted queued job for the latest current
provisional replacement open
  -> suspend the old runtime without destroying planner/store/scan ownership
  -> failed replacement recenters and resumes that same book planner
  -> successful replacement retires it at the book-lifetime boundary
old-request completion already queued on the GUI thread
  -> accept it as cache data when source epoch/identity/render spec/topology match
  -> never use its old request serial to mutate presentation state
paint acknowledgement
  -> transfer displayed ownership / reclaim safe bytes / release UI side effects
  -> never release, pause or recreate raster scheduling
```

`RasterWarmupPlan.priority_band_identities` and
`iter_continuous_units` express 4 + 1 as a scheduling band, not a phase or
retention count.  The same topology handles single, spread, LTR/RTL, wide split
and explicit terminal one-page spread units.  After the priority band, the
iterator immediately emits the remaining book order.  On recenter the Python
iterator is reconstructed around the latest current, but the planner object,
released state, visited-identity ledger, capacity skips, ready frame store and
decoded source store survive.  This is an order replacement over unstarted
work, not a return to startup.

Ready-ahead is continuously maintained as urgency.  A unit entering the new
priority band can discard a previous soft-target capacity skip and is evaluated
with protected admission rank; distant work remains soft-target work.  Cache
retention and eviction still use the full direction/distance rank and combined
source+layout-frame byte budget.  No new page-count cache limit is introduced.
Layout/DPR/render-signature change keeps the book-scoped planner and decoded
sources but resets physical-frame capacity observations before rebuilding
layout-dependent frames.

`RasterBookRuntime._result_is_artifact_compatible` deliberately separates
artifact usefulness from presentation serial.  A completed result from the
preceding navigation may enter the source/frame stores only if the runtime is
accepting, source epoch and source identity match, render spec matches and the
unit still exists in the current topology.  `frameReady` remains tied to the
latest current key/request, so an artifact-compatible old result cannot commit
an obsolete page.  Book switch, source epoch change, incompatible layout,
cancel, retire and shutdown remain hard rejection/drain boundaries.

The concrete NivisViewer implementation is `app/raster_warmup_planner.py`
(`RasterWarmupPlan.priority_band_identities`, `iter_continuous_units`,
`RasterWarmupPlanner.recenter`, `release_after_first_commit`,
`discard_capacity_skip` and `next_candidate`) and
`app/zip_raster_book_runtime.py` (`RasterBookRuntime._adopt_request`,
`_active_job_is_artifact_compatible`, `suspend`, `release_continuous_warmup`, `release_prefetch`,
`_drive`, `_admission_rank`, `_on_job_completed` and
`_result_is_artifact_compatible`).  `release_startup_runway` remains only a
compatibility adapter; it is no longer a separate production phase.

### 21.5 Adopted, retained and rejected structures

| Decision | Structure | Result |
|---|---|---|
| **Adopt from ZipPlaFork** | One Viewer lane continues at each completion boundary without waiting for paint. | The released book-scoped planner calls `_drive` from every terminal job result and continues to the next useful candidate. |
| **Adopt from ZipPlaFork** | Replace the unstarted all-book order around latest current while allowing the sole started compatible job to finish. | `recenter` preserves planner/store state; `_active_job_is_artifact_compatible` adopts any started same-book/source/render-spec job that still belongs to the current topology, not only an urgent-band job. |
| **Adopt from ZipPlaFork** | Completed page artifacts survive navigation and are evicted by current-centered memory policy. | Compatible old-request results may populate the byte-budgeted source/frame stores without presentation publication. |
| **Hybrid modernization** | Current/next/previous bands. | NivisViewer uses direction-aware complete display units and a 4-forward/1-reverse urgent band before the remaining book, rather than ZipPlaFork's numerical next-before-previous page bands. |
| **Keep NivisViewer** | Atomic frame commit and old-frame rollback. | Surface mode, displayed page, slider and status change only at accepted complete-frame commit; spread/split never publishes a partial unit. |
| **Keep NivisViewer** | Epoch/request/layout/DPR fences, input coalescing and structured shutdown. | Artifact compatibility is separate from latest-request presentation authority; book/source replacement still drains callbacks safely. |
| **Reject upstream literal behavior** | Clear old resized arrays before open, advance current/status before pixels, refuse natural input beyond the ready frontier, half-gray the old canvas, and rely on wait-cancel/`Guid.Empty`. | Replacement frame is retained; requests remain coalesced; loading/error/empty are explicit; generation/serial fences remain mandatory. |
| **Reject upstream literal memory rule** | `MaxPageCountInWindow * 4` minimum bitmap admission and coupled source/display eviction. | Combined source/frame bytes remain the only capacity authority, with independent source/frame reuse. |
| **Reject unrelated fixed-commit change** | Catalog thumbnail semaphore set to one by `07955f5`. | Virtual PageList keeps its independent worker/runtime and budget; the Catalog commit is provenance context, not a Viewer algorithm port. |

### 21.6 Direct structural-port and license map

| Fixed-revision source / class / method | Derived processing principle | NivisViewer counterpart |
|---|---|---|
| `source/ZipPla/ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage` | One active Viewer page worker. | `app/zip_raster_book_runtime.py`, `RasterBookRuntime._submit`, `_drive` and active-job ownership. |
| `source/ZipPla/ViewerForm.cs:2919-3007`, `bmwLoadEachPage_RunWorkerStarting`; `:5558-5605`, `SetBackgroundMode` / `priorityLevel` | Book-wide page arrays and a current-centered replaceable work order. | `app/raster_warmup_planner.py`, persistent `RasterWarmupPlanner`, priority band and continuous all-book iterator; runtime `_adopt_request`. |
| `source/ZipPla/ViewerForm.cs:3177-3602`, `bmwLoadEachPage_DoWork` | One page job yields a terminal display-ready artifact or error. | Existing `_ZipRasterUnitJob.run` / runtime decode-render path; Nivis extends the job boundary to an atomic complete display unit. |
| `source/ZipPla/ViewerForm.cs:5371-5415`, `bmwLoadEachPage_EachRunWorkerCompleted`; `source/ZipPla/GenerarClasses.cs:247-264,266-339`, `SetWorksOrder` / completion selection | Current change is observed before the completion handler chooses the next unfinished job; one old active result remains reusable. | `RasterWarmupPlanner.recenter`, `RasterBookRuntime._active_job_is_artifact_compatible`, `_on_job_completed`, `_result_is_artifact_compatible` and `_drive`. |
| `source/ZipPla/ViewerForm.cs:5451-5524`, `SetNewResizedImage` / `ReduceUsingMemory` | Publish completed artifact only and remove lower-priority retained work under memory pressure. | `_ZipRasterFrameStore`, `_ZipRasterSourceStore`, `_admission_rank`, combined-budget enforcement and atomic cache insertion after successful QPixmap creation. |
| `source/ZipPla/ViewerForm.cs:6438-6487,6768-6879,6990-7280`, `showCurrentPage` / paint path | Paint is separate from worker continuation; upstream loading/display semantics were also evaluated. | Only the non-gating separation is adopted. `ViewerPresentationState` and `PresentationSurface` replace the upstream logical/display and half-gray behavior. |
| `source/ZipPla/PackedImageLoader.cs:283-330,1197-1243,1781-1867`, loader/index/entry stream | Book-scoped archive/index lifetime and entry-local read. | Existing `ZipImageSource` and `ZipRasterBookRuntime`; unchanged by this planner/surface replacement. |

These principles remain a direct structural port from the fixed
AGPL-3.0-or-later snapshot.  No C# statement, WinForms control, GDI bitmap loop
or `BackgroundWorker` implementation is copied literally in this section's
Python changes.  Original copyright and license material remains at
`licenses/ZipPlaFork/About.txt` and `licenses/ZipPlaFork/AGPL.txt`; section 1
and `THIRD_PARTY_NOTICES.md` remain applicable.

### 21.7 Historical persistent-planner baseline (superseded by section 22)

The implementation adds contract coverage for presentation-surface ownership,
zero-image open failure, old-layout first-frame rejection with an immediate
cold replacement request, normal resize-debounce restoration,
persistent planner identity/recenter state, continuous first-commit release,
complete spread units, active-job adoption, artifact-compatible old results and
book switch/close/cancel fences.

This table is the pre-adoption baseline retained to show why persistent planner
ownership alone was insufficient.  Its `cancel 8` result is not the current
production contract; section 22 records the focused started-job adoption A/B.

| Scenario / metric | Request-scoped section-20 predecessor | Persistent book-scoped planner | Result |
|---|---:|---:|---|
| Empty prompt during initial loading | Widget inferred `EMPTY` from no local images | Explicit `LOADING`; stale surface projection rejected | Window/Widget tests observe no demotion after complete commit. |
| Initial failure / failed or cancelled replacement | No explicit surface owner | `ERROR` / retained `DISPLAYED` | Old pixmap, page state and the same planner survive replacement rollback. |
| First-frame commit / offscreen draw | 16 ms / <1 ms | 16 ms / <1 ms | Same fixture; draw resolution is below the timer resolution. The controlled 20-ms paint-ack delay is synthetic. |
| First 10 turns, 8-ms cadence, single | 10 hit / 0 miss | 10 hit / 0 miss | Both are ready-hit fast paths; current reduces scheduling churn rather than improving this already-warm hit rate. |
| First 10 turns, zero-delay stress, single | 3 hit / 7 miss | 3 hit / 7 miss | No hit-rate improvement when the worker is given no population time. Total elapsed is 94 -> 94 ms; the final per-turn sample is 0 -> 16 ms at the coarse timer quantum, so this is not claimed as an improvement. |
| Spread / LTR / RTL / reversal | historical contracts | complete-unit order and latest-frame contract retained | 11 selected offscreen contract tests pass; this is structural validation, not a numeric A/B. |
| Ready-ahead at commit / paint / 50 / 100 / 250 ms | 0 / 2 / 5 / 11 / 30 | 0 / 3 / 7 / 13 / 30 | Current begins the continuous next work one completion earlier; both reach 30 by 250 ms. |
| Planner objects across initial + ten turns | 11 | 1 | Release remains one book-scoped event; navigation recenters the same owner. |
| 8-ms navigation churn, four-run median | jobs 25, cancel 9, stale 8 | jobs 24, cancel 8, stale 0 | Ranges: jobs 23--27 -> 23--24; cancel 8--10 -> 8--9; stale 7--9 -> always 0. |
| Duplicate successful decode | 0 | 0 | Compatible immutable artifacts are retained without duplicate decode or stale presentation publication. |
| 250-ms combined cache, representative run | 59,520,000 B | 59,520,000 B | No page-count retention limit or memory increase in the matched sample. |
| Shutdown / callback drain | clean | clean | Both child processes drained, closed the ZIP source and left no benchmark/pytest process. |

Production metrics already expose `ready_ahead_unit_count`,
`priority_band_target_units`, `continuous_warmup_releases`,
`warmup_planner_creations`, `warmup_planner_recenters` and
`compatible_old_results` alongside cache hit/miss, jobs, cancel, stale and
eviction counts.  They are diagnostic evidence only; byte budget, visual
correctness and real-device first-ten-turn behavior remain the acceptance
criteria.  Real application launch, native input and external GUI automation
remain outside this offscreen validation boundary.

The reproducible runner is `scripts/benchmark_persistent_raster_warmup.py`.
The matched fixture is one 69,606,482-byte `ZIP_STORED` archive containing 100
1,600 x 2,400 JPEG pages (SHA-256
`5875cf121405248842df98cc11e28c0c1cdc27d9206cd8af4191b26a7efd1b76`), a
400 x 600 target, 512 MiB hard / 448 MiB soft limits and a synthetic 20-ms
commit-to-paint acknowledgement.  `HEAD` was read-only `git archive` output at
`199ab05b8460160a64c2d20655f36984440cbb60`; no checkout or worktree mutation
was used.  Four independent 8-ms cadence runs had elapsed medians 195.5 -> 187
ms, but offscreen decode scheduling is not physical wheel latency.  The
zero-delay result is retained explicitly because it bounds the claim: this
change removes planner/stale churn and forms ready-ahead sooner, but it does not
make an already-cold final target decode faster than the decoder itself.

Final syntax/import checks and `git diff --check` passed.  The focused
presentation, BookSession, Widget/Window, planner, ZIP runtime, Folder runtime
and raster-Viewer integration group passed **130 tests** in one offscreen
process.  A separate 11-test selection covering the complete planner module,
LTR/RTL slot order, direction reversal/compatible-result adoption, complete
spread rotation and magnifier-source retention also passed.  The full
repository suite was not rerun for this change; the selected scope follows the
task's requirement to validate major ownership/work-order contracts rather
than grow or repeatedly execute unrelated boundary tests.

## 22. Fixed-revision input frontier and artifact-compatible started-job adoption (2026-08-21 worktree)

Section 21 established one persistent book-scoped planner, but its first
implementation could still cancel the sole already-started background job when
navigation moved that job outside the new urgent band.  This section records
the narrower final work-order correction: navigation replaces only work that
has not started; a started job for the same book/source/render specification is
allowed to finish and its compatible artifact is retained.  The requested page
still changes immediately while the displayed complete frame remains atomic.
The startup runway, input timers and cache/admission policy are unchanged.

### 22.1 Exact open, work-state and next-work sequence at the fixed snapshot

The following sequence was re-audited directly at ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:

```text
ViewerForm.OpenFile
  -> create/retain PackedImageLoader and enumerate/sort its image entries
  -> select currentPage and project the trackbar
  -> clearResizedImageArray
  -> BackgroundMultiWorker.RunWorkerAsyncWithInterrupt(arguments,
                                                         waitCancel=true)
     -> if the preceding work set is busy:
          CancelAsync marks that set cancelled and queues the new arguments
          the already-running DoWork is not preempted
          its eventual completion is forced to Cancelled and is not published
     -> bmwLoadEachPage_RunWorkerStarting
        -> promote nextData and the new book-scoped PackedImageLoader
        -> dispose the preceding loader only after the preceding work drains
        -> allocate whole-book source/info/resized/size arrays
        -> SetBackgroundMode
           -> priorityLevel for every numerical page
           -> SetWorksOrder(the resulting all-page permutation)
     -> BackgroundMultiWorker.RunWorkerAsync
        -> allocate Works, WorksStarted=false and ResultArgs=null per work
        -> choose WorksOrder[0] and mark that item WorksStarted=true
        -> run at most one Viewer item because ThreadCount=1
     -> bmwLoadEachPage_DoWork(WorkNumber)
        -> open only that entry when its retained source is absent
        -> decode, orientation/pre-filter and source retention
        -> resize/post-filter and return one complete VirtualBitmapEx
        -> return a terminal error bitmap for a per-entry failure
     -> bmwLoadEachPage_EachRunWorkerCompleted                 [UI callback]
        -> if currentPage changed, SetBackgroundMode(latest current)
        -> SetNewResizedImage: admit/publish the completed bitmap as cache data
        -> if it intersects the latest visible numerical band:
             showCurrentPage(false) -> Invalidate only
     -> BackgroundMultiWorker.privateRunWorkerCompletedEventHandler
        -> store ResultArgs[completed WorkNumber]
        -> if SetWorksOrder changed the order, reset the next-order cursor
        -> skip every WorksStarted item in the newest WorksOrder
        -> mark/start the first unfinished item immediately
     -> later WM_PAINT / pbView_Paint / pbView_PaintToCanvas
```

The relevant upstream methods are `source/ZipPla/ViewerForm.cs:2308-2771`
(`OpenFile`), `:2919-3007` (`bmwLoadEachPage_RunWorkerStarting`),
`:3177-3602` (`bmwLoadEachPage_DoWork`), `:5371-5415`
(`bmwLoadEachPage_EachRunWorkerCompleted`), `:5451-5524`
(`SetNewResizedImage` / `ReduceUsingMemory`) and `:5558-5605`
(`SetBackgroundMode` / `priorityLevel`), plus
`source/ZipPla/GenerarClasses.cs:247-339` (`SetWorksOrder`, `ReworkOrder` and
completion/next-work selection).  `ViewerForm.Designer.cs:1628-1636` fixes the
Viewer worker count at one.

`WorksStarted`, `ResultArgs` and resident bitmap state are distinct.  Waiting
means `WorksStarted=false`; running means `WorksStarted=true` and
`ResultArgs=null`; scheduler-completed means both started and a non-null result.
`ReworkOrder` makes an item waiting again by clearing `WorksStarted` even when
an older `ResultArgs` object still exists.  Conversely, a scheduler-completed
item need not still own a resident bitmap after memory eviction.  Navigation
does not cancel the one running work item.  It only replaces the unstarted
permutation; book/reload replacement uses the separate drain/cancel boundary.

`EachRunWorkerCompleted` executes before the internal completion handler chooses
the next work item.  It can therefore rebuild the order around the latest
`currentPage`, publish the just-finished bitmap, and then let the scheduler pick
the first unfinished item from that newest order.  `showCurrentPage` merely
invalidates the control.  The next decode normally starts before the queued
Windows paint, so paint is not a scheduling gate.

### 22.2 Natural WheelDown and forced one-page input are different models

The default natural wheel call sequence is:

```text
ExtendedKeys.WheelDown -> Command.NextPage
  -> getMouseGestureSettingTemplate -> moveToNextPage
  -> NextPage -> movePageNatural(1)
```

This path is in `ViewerForm.cs:15852`, `:13049-13050`, `:12403-12417`,
`:1903-1948` and `:9279-9360`.  It has no pending final target.  In single mode,
an unfinished current page rejects WheelDown without changing current, work
order or cancellation state.  A ready current page may advance once into a cold
destination; further WheelDown is rejected until that new current completes.
In ordinary two-page spread mode both pages of the current numerical band must
be ready before the move, after which it may likewise enter one cold next band.
Wide/cover branches can reduce that move to one-page behavior.  Thus ten rapid
natural WheelDown inputs immediately after a cold open accept zero moves while
page 0 is unfinished, or at most one cold step if page 0 becomes ready during
the burst.  More steps require intervening completion frontiers.  Inputs beyond
the frontier are dropped, not coalesced.

The default `MoveForwardOnePage` command is different:

```text
Down -> Command.MoveForwardOnePage
  -> movePageToMinimulForward
  -> moveDividedPage -> isMovablePage -> movePage
```

The path is `ViewerForm.cs:15858`, `:13053-13054`, `:9536-9589`,
`:9630-9694` and `:9759-9772`.  With `MaxDivision=1`, ten rapid commands can
advance logical `currentPage` from `c` to `c+10` while the sole job for `c`
continues.  They do not enqueue ten decodes.  At the next worker completion,
`bmwLoadEachPage_EachRunWorkerCompleted` observes the latest `currentPage`,
calls `SetBackgroundMode`, admits the completed old bitmap if memory permits,
and the internal completion handler starts the first unfinished item around
`c+10`.  Intermediate pages are decoded only if a worker completion happens
between input events.  This is **completion-boundary coalescing to the latest
logical current**, not a pending-request/final-target state machine.

For an ordinary middle-of-book current, the numerical order is:

| Binding | Fixed `priorityLevel` page band |
|---|---|
| single, `M=1` | `c`, `c+1`, `c-1`, then the remaining pages by distance |
| spread, `M=2` | `c,c+1`, then `c+2,c+3`, then `c-2,c-1`, then the rest |

At book start this becomes `0,1,2,...` in single mode.  In ordinary spread it
also begins `0,1,2,3,...`, and natural navigation normally waits for the page-0
band partner before accepting the next spread.  These are fixed number bands,
not wide/cover-aware complete display units.  `SetBackgroundMode` is called in
`bmwLoadEachPage_RunWorkerStarting` before the replacement `BindingMode` is
assigned later in that method, so the initial `M` can even reflect the prior
mode.  NivisViewer does not adopt either mismatch.

The one-input state model is therefore:

| Input/state | ZipPlaFork decode / retain / drop behavior |
|---|---|
| Natural input while current band is unfinished | Reject input; do not move current, create work or cancel; the running job continues. |
| Natural input from ready current to ready destination | Move current and paint retained bitmap immediately; background job continues and the newest order is selected at its completion. |
| Natural input from ready current to cold destination | Accept one move, keep the running job, draw the loading treatment; reject subsequent natural inputs until completion. |
| Forced one-page or trackbar move beyond the frontier | Advance logical current; do not create one job per input; retain the running result and choose latest current at the completion boundary. |
| Reversal to a resident page | Change current and repaint the resident bitmap; the sole old-direction job still finishes and may enter cache before the order recenters. |

### 22.3 Bitmap completion and memory admission

`SetNewResizedImage` is cache publication, not a displayed-frame commit.  Its
dynamic upper bound comes from `SetMemoryUBound`; active and inactive modes use
different fractions of current process/available memory.  Before admitting a
new `VirtualBitmapEx`, `ReduceUsingMemory` evicts from the tail of the latest
work order and disposes both that page's resized bitmap and pre-filtered source.
The new bitmap is admitted when it fits, or while the resident count is below
`MaxPageCountInWindow * 4` (four single pages or eight spread pages).  Otherwise
it is disposed and `ReworkOrder` makes that page schedulable again.  When the
required numerical neighborhood is resident but no further bitmap can be
admitted, the worker can pause at `ThreadCount=0`; later navigation calls
`SetBackgroundModeIfPausing` to resume it.

These rules explain why an old running job is not inherently stale in
ZipPlaFork: if its bitmap passes admission it remains useful book cache even
when `currentPage` changed during decode.  They also explain what is not being
ported: the fixed `M * 4` minimum, coupled source/display eviction and logical
current/loading canvas semantics are WinForms-era policies, not requirements of
the completion-boundary work-order principle.

### 22.4 NivisViewer Hybrid implementation

NivisViewer now adopts the useful upstream boundary without adopting its input
loss or split presentation state:

```text
latest navigation input
  -> accept final requested unit and serial immediately
  -> keep displayed page, slider, status and old complete frame unchanged
  -> RasterWarmupPlanner.recenter(latest complete-unit topology)
  -> active job handling
     -> exact current key: adopt latest request as current
     -> already started + same source epoch/identity/render spec + unit still
        in current topology: let it finish and adopt it as cache-producing work
     -> not started: remove/replace it before setting a cancellation flag
     -> worker already finished but GUI callback is still queued:
        release the physical one-worker slot immediately
        record its key as pending completion so it cannot be decoded twice
  -> compatible completion
     -> store source/frame artifact under the combined byte budget
     -> publish only when it is the latest current key/request
  -> final current completion
     -> atomic ViewerPresentationState/frame commit
```

The implementation is in `app/zip_raster_book_runtime.py`:
`RasterBookRuntime._adopt_request`,
`_active_job_is_artifact_compatible`, `_take_unstarted_job`,
`_release_finished_active_slot`, `_has_pending_completion`,
`_result_is_artifact_compatible`, `_on_job_completed` and `_drive`, with
diagnostic `work_order_changes`, `running_job_adoptions`,
`queued_job_replacements` and `finished_job_slot_releases`.  A same-book
compatible started job is no longer restricted to the 4-forward/1-reverse
urgent band.  `tryTake` is attempted before cancellation, closing the Qt
queued-to-running race without poisoning a job that just started.  A finished
QRunnable is detached from `_active_job` even if its queued QImage/QPixmap
publication callback has not run; `_pending_completion_keys` is the duplicate
decode fence until that callback is consumed.
Incompatible source epoch, source identity, render specification, topology,
book switch, retire, shutdown and close remain hard rejection boundaries.

This preserves the NivisViewer advantages: every input can update the final
requested target, the old complete frame remains displayed during the miss,
stale serials cannot mutate presentation, single/spread/wide/RTL use one actual
display-unit topology, and paint acknowledgement owns only displayed-resource
reclamation and deferred UI effects.  It does not release, pause or advance the
raster worker.  Across the initial request plus ten navigation requests, the
same planner reports **one creation and ten recenters**.

Only the artifact-compatible started-job policy and its metrics changed in
this focused implementation, together with the worker-finished/GUI-pending
state split needed to make that policy continue immediately.  The continuous
runway (`F1, R1, F2, F3, F4`),
input admission/coalescing timers, combined byte budget, retention rank,
eviction and source/frame cache formats are unchanged, making the A/B attribution
narrow.

### 22.5 Focused offscreen A/B

The before/after comparison uses the same fake/offscreen raster fixture and
changes only the focused work-ownership boundary above: started-job retention,
safe queued replacement and worker-finished/GUI-pending separation.  The
figures are supporting evidence, not a claim about native wheel feel or
physical paint latency.

The final rerun used one unchanged 100-page, 1600 x 2400 JPEG, ZIP_STORED
fixture (`69,606,482` bytes, SHA-256
`5875cf121405248842df98cc11e28c0c1cdc27d9206cd8af4191b26a7efd1b76`), a
400 x 600 physical target, 20-ms fake paint acknowledgement and the same
512/448-MiB hard/soft budgets.  Each cell is `before -> after`.  No runway,
timer, radius or budget value differs between the two sides.

| Scenario | first frame ms | ready hits / 10 | jobs | successful artifacts | cancel | running adoption | ready ahead | final ms | elapsed ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| single LTR, 8 ms | 16 -> 16 | 10 -> 10 | 24 -> 24 | 15 -> 23 | 9 -> 0 | 1 -> 9 | 4 -> 12 | 0 -> 0 | 204 -> 203 |
| single RTL, 8 ms | 15 -> 16 | 10 -> 10 | 24 -> 24 | 15 -> 23 | 9 -> 0 | 1 -> 10 | 4 -> 12 | 0 -> 0 | 203 -> 203 |
| spread LTR, 8 ms | 15 -> 16 | 10 -> 10 | 16 -> 15 | 29 -> 29 | 1 -> 0 | 9 -> 10 | 3 -> 3 | 0 -> 0 | 203 -> 203 |
| spread RTL, 8 ms | 15 -> 15 | 10 -> 10 | 16 -> 16 | 30 -> 30 | 1 -> 0 | 9 -> 10 | 4 -> 4 | 0 -> 0 | 203 -> 203 |
| single LTR, no added delay | 16 -> 0* | 3 -> 2 | 12 -> 12 | 11 -> 11 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 0 -> 15 | 94 -> 94 |
| single RTL, no added delay | 0* -> 16 | 2 -> 3 | 12 -> 12 | 11 -> 11 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 0 -> 0 | 93 -> 94 |
| spread LTR, no added delay | 16 -> 16 | 2 -> 2 | 12 -> 12 | 22 -> 22 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 16 -> 16 | 141 -> 141 |
| spread RTL, no added delay | 15 -> 15 | 2 -> 1 | 12 -> 12 | 22 -> 22 | 0 -> 0 | 10 -> 10 | 0 -> 0 | 15 -> 15 | 140 -> 140 |

All eight runs kept one planner, ten recenters, zero duplicate successful
decode, zero stale presentation result and clean shutdown.  `0*` is the
millisecond timer's resolution floor, not a zero-cost first frame.  The
no-added-delay rows wait for each frame before issuing the next request; they
are a cache-readiness stress bound, not a simultaneous input burst.  Their
one-hit scheduling jitter is therefore not used as an improvement claim.

The actual production burst contract is covered separately with page 1 held
inside its decode while ten wheel targets advance from page 0 to page 10:

| State after first-frame commit | Previous Nivis policy | Hybrid policy |
|---|---|---|
| input 1 | requested 1, displayed 0; running page 1 is exact current | same |
| input 2 | requested 2; set cancellation on running page 1 | requested 2; adopt page 1 as useful book work |
| inputs 3-10 | requested ends at 10; cancelled page 1 still occupies the sole worker | requested ends at 10; displayed remains 0; page 1 remains useful and is adopted nine times total |
| worker exits before GUI callback | page 1 result is discarded; final job waits for the queued callback to clear `_active_job` | page 1 key enters the pending-completion ledger and page 10 starts immediately without waiting for QPixmap publication |
| terminal artifacts / presentation | pages 0 and 10; one cancel; displayed sequence 0 -> 10 | pages 0, 1 and 10; zero cancel/stale/duplicate; displayed sequence 0 -> 10 only |

The decoded page order is `0, 1, 10` on the Hybrid path; page 1 is retained
instead of being thrown away, and no intermediate frame is committed.  This is
the direct answer to the open-runway problem: useful work and the latest order
are independently owned.  Adoption does not manufacture decoded pixels at an
actual 0-ms cadence, but it prevents navigation from converting an occupied
worker into discarded work.  Physical ZIP entry I/O, decoder throughput,
QPixmap upload, DWM/GPU presentation and native-input behavior remain for the
real-device check.

### 22.6 Adoption decision and AGPL provenance

| Decision | Fixed-revision source principle | NivisViewer result |
|---|---|---|
| **Adopt structurally** | `bmwLoadEachPage_EachRunWorkerCompleted` observes latest `currentPage`; `SetBackgroundMode` replaces the unstarted order; `BackgroundMultiWorker` lets the sole started work finish and selects next work at completion. | `RasterBookRuntime._adopt_request` keeps a started artifact-compatible same-book job and replaces an unstarted job; `_on_job_completed` stores the artifact, while only latest-current authority can publish. |
| **Adopt structurally** | `SetNewResizedImage` makes a completed old-current bitmap reusable cache data; worker continuation does not wait for paint. | Compatible old-request results can enter the frame/source stores; `_drive` continues independently of paint acknowledgement. |
| **Modernize the boundary** | The WinForms completion callback publishes the bitmap and then its internal handler immediately chooses the next work before paint. | A finished QRunnable releases the scheduler slot before its queued GUI callback; a pending-key ledger prevents duplicate decode, so QPixmap upload and callback order do not gate a different final target. |
| **Keep NivisViewer** | No upstream equivalent for requested/displayed serial separation or atomic complete-unit commit. | Final requested input is accepted, displayed/slider/status remain on the prior complete frame, and spread/wide commit only as one complete unit. |
| **Do not adopt** | Natural WheelDown drops input at the unfinished frontier; forced movement advances logical current/status before pixels; loading uses a half-gray canvas. | Input coalescing retains the final target and PresentationState prevents partial/stale display publication. |
| **Do not adopt** | Fixed numerical `M=1/2` bands, coupled source/display eviction and the `M * 4` minimum bitmap rule. | Actual display-unit topology and the existing combined byte-driven source/frame stores remain authoritative. |

The structural source is
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork) at fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, principally
`source/ZipPla/ViewerForm.cs` methods `bmwLoadEachPage_RunWorkerStarting`,
`bmwLoadEachPage_DoWork`, `bmwLoadEachPage_EachRunWorkerCompleted`,
`SetNewResizedImage`, `SetBackgroundMode`, `NextPage`, `movePageNatural` and
`movePageToMinimulForward`, plus `source/ZipPla/GenerarClasses.cs`
`SetWorksOrder`, `ReworkOrder` and the internal completion handler.  The source
is AGPL-3.0-or-later.  Original copyright and license materials remain in
`licenses/ZipPlaFork/About.txt` and `licenses/ZipPlaFork/AGPL.txt`, with the
notice in `THIRD_PARTY_NOTICES.md` and section 1 still applying.

As in section 21, the fixed hash is a source snapshot, **not** the commit that
introduced the one-worker Viewer setting.  Commit `07955f5` only changes the
Catalog thumbnail semaphore; the audited Viewer worker was already present in
`5e942ef0006decf01c21d9dbe037dba50e763420`.  The NivisViewer implementation is
a Python/Qt structural translation of the completion-boundary ownership rule;
it does not copy WinForms controls, GDI bitmap arrays or `BackgroundWorker`
statements literally.

## 23. Magnifier artifact and manual viewport transform

This section fixes the interactive magnifier and manual-zoom pan boundary. It
does not alter the book runtime, navigation work order, decoded-source store or
normal display-frame cache described above.

### 23.1 Fixed-revision Magnifier flow

At revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, ZipPlaFork separates
construction of a magnified page canvas from movement over that canvas:

```text
MagnifierPhase1
  -> validate current resized/pre-filtered pages and enter MagnifierOpening
  -> MagnifierPhase2 waits for Magnifier_ZoomedPage/current-page readiness

bwMagnifierMaker_DoWork
  -> read retained PreFilteredImageArray pages
  -> choose MagnifierScalingAlgorithm.ScaleUp or ScaleDown
  -> resize each required page once into MagnifierCanvas
  -> publish Magnifier_ZoomedPage in RunWorkerCompleted

pbView_MouseMove while ViewerMode == Magnifier
  -> retain latest cursor position
  -> pbPaintInvalidate only

paint
  -> GetMagnifierRectangle computes the latest source rectangle
  -> draw the relevant region from Magnifier_ZoomedPage.Bitmaps/Offsets
```

`TouchListener_Pan` accumulates a position delta consumed by
`GetMagnifierRectangle`; `MagnifierAutoScroll` likewise changes that position.
Neither path reconstructs `Magnifier_ZoomedPage` for each movement. A page,
zoom/layout or source change is the artifact-generation boundary; cursor/touch
movement is a viewport boundary. The prepared canvas may be queued while the
opening preview is visible, and `MagnifierPhase2` enters the final mode only
when the matching current-page canvas is ready.

The principal source locations are `source/ZipPla/ViewerForm.cs`:
`bwMagnifierMaker_DoWork` / `RunWorkerCompleted` (`:7720-8018`),
`MagnifierPhase1` / `MagnifierPhase2` (`:8051-8088`),
`GetMagnifierRectangle` (`:8137-8370`), `MagnifierAutoScroll`
(`:8384-8460`), `pbView_MouseMove` (`:10578-10623`),
`TouchListener_Pan` (`:1449-1530`) and the Magnifier paint branch
(`:7026-7058`).

### 23.2 Previous NivisViewer critical path

Before this change, every active-lens `ViewerWidget.mouseMoveEvent` called both
`_update_magnifier_selection` and `_request_magnifier_render`. The cursor crop
was part of `ViewerRenderKey`, so each distinct position incremented a request
generation, attempted to cancel the preceding task and scheduled a new
`ViewerRenderTask`. That worker cropped a `QImage`, converted through Pillow,
resampled to the viewport, returned another `QImage`, and the GUI callback ran
`QPixmap.fromImage`. Repaint also emitted the normal page-paint notification,
allowing unrelated after-paint scheduling to run during pointer tracking.

The old/new one-move sequences are:

| Boundary | Before | After |
|---|---|---|
| Cursor event | compute crop and create crop-specific generation/key | store latest normalized source rectangle |
| Worker | cancel/replace or run crop + resample | none |
| GUI upload | new `QPixmap.fromImage` for a completed crop | none |
| Cache | insert position-specific result | retain the page/zoom artifact |
| Paint | draw full viewport crop pixmap and emit page paint callbacks | draw the latest artifact source rectangle; no page-commit callback |

### 23.3 Hybrid artifact design

NivisViewer adopts ZipPlaFork's preparation/movement split as a modern Qt
Hybrid. `ViewerWidget._request_magnifier_render` creates one independent
artifact identity from source `QImage.cacheKey`, physical artifact target,
DPR, rotation, split range and the selected magnifier down/up algorithms. The
cursor crop and navigation request serial are deliberately absent. A small
two-entry LRU retains the current/recent artifact; it is capped at 160 MiB and
each artifact at 32 million physical pixels (at most about 128 MiB for a
32-bit pixmap). Pathological images that reach the cap use QPainter's final
source-rectangle mapping instead of allocating an unbounded canvas. A tiled
renderer was not added because ordinary manga pages are covered by this
bounded whole-artifact design.

The current preview source can produce an immediate provisional artifact.
`magnifierSourceResolutionRequested` / `magnifierPdfResolutionRequested` is
emitted at most once for that source identity; cursor motion does not re-enter
promotion. When the runtime attaches the current page's full source,
`resume_magnifier_after_source_render` remaps the normalized viewport and
builds one replacement artifact. The old preview remains visible until that
artifact is complete. Source/page/render-generation and artifact-key checks
reject late callbacks after page change, book switch, close or lens cancel.
The normal ready-frame cache and its resampling authority are unchanged.

One newly committed display frame sets a paint-ack flag. `contentPainted` and
`framePainted` are emitted only by its first completed paint; later magnifier
or pan repaints are pure viewport operations. This keeps PageList, history,
progress, prefetch and Browser projection out of the pointer-move call path.

### 23.4 Manual zoom and pan ownership

`ViewerWidget.ViewTransform` is now the single viewport-state owner for zoom
factor, pan offset, cursor zoom anchor, physical viewport size, physical
display-unit bounds, rotation, DPR and transform generation. Left-button
movement becomes pan only after the existing pointer-controller drag
threshold. A sub-threshold release still follows the configured canvas click
navigation. Open-hand/closed-hand cursors expose the two states.

Pan changes only `ViewTransform.pan_offset` and schedules a QWidget repaint;
it neither invalidates a layout artifact nor creates a worker, decoder,
resampler or QPixmap. Overscroll is clamped independently on each axis and an
axis smaller than the viewport remains centered. Page change and return to a
fit mode reset pan. Resize/DPR recomputes physical bounds and clamps the
existing pan. Rotation currently retains the established reset behavior; a
future source-coordinate focus model would be needed to preserve a meaningful
focus through 90-degree axis exchange.

Ctrl+wheel retains the existing zoom gesture but now treats the cursor as its
anchor: the normalized display-unit point under the cursor remains there after
the scale change, subject to edge clamping. Arrow keys pan while the manual or
actual-size image can move; Shift uses a larger viewport-relative step. At an
edge, the pan operation returns false and the existing page-navigation handler
can receive the key. NivisViewer has no production touch-pan gesture owner at
this boundary, so no synthetic touch subsystem was added; future touch input
should call the same clamped `ViewTransform` operation.

### 23.5 Focused offscreen contract

The focused fake-mouse/offscreen checks exercise 100 cursor moves after the
artifact is ready, preview/full-source promotion, PDF promotion, manual zoom at
200%, drag threshold/clamping, cursor-anchored zoom, keyboard pan, click
navigation coexistence, direct ZIP/Folder presentation and paint
acknowledgement. For the 100-move lens case the post-artifact counts are:

| Operation | Before (structural count) | After |
|---|---:|---:|
| crop/resample requests | 100 | 0 |
| worker jobs | up to 100 position jobs | 0 |
| `QPixmap.fromImage` | up to 100 completions | 0 |
| source promotion requests | could be re-entered by movement | 0 (one at lens start only when needed) |
| page-commit paint callbacks | up to one per serviced repaint | 0 |

The initial lens open or a source/zoom/algorithm change still performs exactly
one necessary artifact job and one QPixmap upload. Pan tests keep render
generation, render-cache keys and task count unchanged. These counts establish
the production boundary but are not a claim about native mouse latency, GPU
upload or DWM presentation; those remain real-device checks.

### 23.6 Provenance

The structural source is [`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork)
at fixed revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, principally
`source/ZipPla/ViewerForm.cs` methods and fields listed in section 23.1. It is
AGPL-3.0-or-later. License and copyright notices remain in
`licenses/ZipPlaFork/About.txt`, `licenses/ZipPlaFork/AGPL.txt` and
`THIRD_PARTY_NOTICES.md`. NivisViewer copies the prepared-canvas/view-position
separation structurally, not the WinForms/GDI control or bitmap statements;
the bounded Qt artifact cache, DPR-aware identity, preview/full promotion,
stale fences and `ViewTransform` are NivisViewer-specific implementations.

## 24. Browser filename rating and file detail (2026-08-22)

### 24.1 Fixed-revision ZipPlaFork contract

The rating source was re-audited at fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`. `source/ZipPla/ZipPlaInfo.cs`
class `ZipPlaInfo` constructs `fileNameRegex`, parses the first valid block in
its constructor, exposes `Rating`, serializes known values with
`GetInfoString`, and places the canonical block before the final extension for
files or after the whole name for directories in
`GetPathOfCurrentInfo(bool isDir)`. The grammar is a case-insensitive, optional
single-leading-space block `{zpi$...}`. Known semicolon-separated parameters
are serialized in `c`, `b`, `r`, `t`, `d` order. Rating is `r=1` through
`r=5`; no token means unrated, and zero is not a separately persisted state.
Changing only `Rating` preserves parsed cover, binding, tags and legacy
direction. Invalid blocks such as `r=0`, `r=6` or unknown parameters do not
match the whole metadata grammar and remain ordinary filename text.

`source/ZipPla/CatalogForm.cs` draws five stars at the thumbnail's upper-left
after the thumbnail layer: completed stars are Gold and remaining stars are
LightSlateGray over a black background. It also draws before thumbnail loading
has completed. Horizontal position maps linearly and clamps to 1..5. Hover is
preview-only; left click changes the pointed item even when other rows are
selected, middle click clears it, and context-menu commands apply none/1..5 to
all selected items, including directories. Rating sorting leaves unrated
entries behind rated entries in both directions.

The same `CatalogForm.cs` registers `SortMode.RatingInAsc` and
`SortMode.RatingInDesc` as ordinary `cbSortBy` entries beside name, created,
accessed, modified and size modes. `GetSortArray` calls
`ZipPlaInfo.GetOnlyRating`; ascending replaces its `-1` unrated sentinel with
`int.MaxValue`, while descending keeps `-1`, so unrated rows sort last in both
directions. `dgvFileList_ColumnHeaderMouseClick` toggles the Rating column
between descending and ascending by selecting the matching `cbSortBy` mode,
and `setNonVolatilitySort` mirrors that mode to the Rating header glyph.

### 24.2 NivisViewer Hybrid implementation

`app/zippla_filename_metadata.py` is a direct Python structural translation of
the parser/serializer contract above. `app/rating_rename_service.py` performs
the file-rating same-directory rename: it checks collision, never rewrites
payload bytes, verifies nanosecond mtime after rename, and restores only if the
backend changed it. Directory ratings use the existing
`FileOperationCoordinator` rename authority described in 24.3.1. Browser
scanning parses the filename without opening the item and separates physical
path from the metadata-free display name.

`BrowserItemModel` owns the parsed value, preview value and rating sort. Its
rating-rename relocation changes the path identity in place, migrates the
existing thumbnail `QImage`, preview/error/signature, cut state and cached
dimensions, and then re-sorts without a directory rescan. Therefore a direct
rating change performs no image read/decode and no QPixmap construction.
Selection/current/scroll identities are remapped after a rating sort move.
Browser navigation snapshots already derive from the model's visible order,
so rating-sorted Folder Viewer next/previous retains that same topology.
NivisViewer keeps its existing split controls rather than copying ZipPlaFork's
combined enum labels: the upper `browser_sort_key_combo` contains one
`レート` key and the adjacent existing order combo supplies ascending or
descending. `ConfigManager` accepts and persists `rating` through the same
normalization contract as other keys; unknown future/legacy values still fall
back safely to `name`. Rating changes invoke the model's stable in-memory sort
and restore path-based selection/current/scroll state without scanning the
filesystem.

`BrowserItemDelegate` provides a DPR-aware upper-left overlay, five-way hit
test and hover preview while preserving IconMode virtualization. A direct
left/middle click is consumed before Qt changes the multiselection, so it
changes that one Browser item only; this includes a child folder. The context
menu is the explicit batch authority for files and folders. Affected live
Viewers use the existing confirmation and close contract before either rename
path. NivisViewer has no filesystem watcher in this Browser, so there is no
duplicate self-event to suppress; file results update the model synchronously,
and folder coordinator results update it on completion without a rescan.

The status bar's permanent right-hand `browser_file_detail_label` contains
only file size and logical image dimensions. Dimensions reuse the Browser
model cache or a one-thread Pillow header probe (no pixel `load`); EXIF
orientations 5..8 swap logical axes. A selection generation plus normalized
path identity rejects late results. Archive/PDF/folder selections do not probe
cover dimensions, and multi-selection retains the existing left-hand status
while clearing this single-image detail.

### 24.3 Provenance

The translated grammar, star interaction and sort semantics derive from
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork), revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, principally
`source/ZipPla/ZipPlaInfo.cs` (`ZipPlaInfo` constructor, `Rating`,
`GetInfoString`, `GetPathOfCurrentInfo`) and `source/ZipPla/CatalogForm.cs`
(catalog rating paint/hit/rename/context/sort handlers). That source is
AGPL-3.0-or-later. Original notices and license text remain in
`licenses/ZipPlaFork/About.txt`, `licenses/ZipPlaFork/AGPL.txt`, and
`THIRD_PARTY_NOTICES.md`. Qt delegate painting, asynchronous header probing,
model cache relocation, stale fences and status-bar placement are
NivisViewer-specific modernizations.

#### 24.3.1 Full-rectangle folder fallback and directory rating metadata

This comparison is fixed to `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`. The relevant upstream source is:

- `source/ZipPla/CatalogForm.cs`: `setColors`,
  `tvCatalog_ThumbnailPaint`, `drawFileImage`, `drawFileIconImage`,
  `SetRating`, `SetSelectedArchiveRating`, and nested
  `ThumbViewer.getThumbnailRectangle`/`ThumbViewer.DrawItem`;
- `source/ZipPla/ZipPlaInfo.cs`: constructor parsing, `Rating`,
  `GetInfoString`, and `GetPathOfCurrentInfo(bool isDir)`.

These upstream files and the behavior translated below are
AGPL-3.0-or-later-derived. ZipPlaFork copyright/license notices and the license
text remain in `licenses/ZipPlaFork/About.txt`,
`licenses/ZipPlaFork/AGPL.txt`, and `THIRD_PARTY_NOTICES.md`.

##### 24.3.1.1 Exact fallback geometry and color

`ThumbViewer.getThumbnailRectangle` computes one `thumbWidthCurrent` by
`thumbHeight` rectangle. `ThumbViewer.DrawItem` creates a canvas of exactly
that size, fills its entire area with `BackColorBrush`, then draws a real or
dummy image unscaled into the same rectangle. `CatalogForm.setColors` calls
`tvCatalog.SetBackAndForeColor(Color.Black)`, so the operational Catalog
default is solid black. For the not-yet/error branch,
`tvCatalog_ThumbnailPaint` passes that same `e.ThumbnailRectangle` to
`drawFileImage`; the fixed-revision method immediately returns, so it does not
add a smaller card or large icon. The optional lower-left associated icon is
drawn separately by `drawFileIconImage` after a completed thumbnail.

`BrowserItemDelegate` now derives one physical-pixel-snapped
`thumbnail_content_rect` from `BrowserGridMetrics.thumbnail_frame_rect` and
uses it both as the maximum target for a successfully loaded thumbnail and as
the placeholder canvas. It preserves the existing real-image geometry: four
logical pixels inside the frame in fit mode and one in center-crop mode, with
edges snapped to physical pixels. Thus the placeholder has no independently
computed edge and cannot bleed outside the real-image target. There is no
placeholder-specific inset, rounded edge, internal outline, shadow, or back
plate. The normal thumbnail-frame outline remains common to placeholder and
real-thumbnail items. Automatic/default resolves to `#000000`;
a custom `#RRGGBB` value is persisted as
`browser_folder_fallback_background`, and restore-default serializes `auto`.
An open Browser applies either value through delegate configuration and a
viewport repaint only. Thumbnail provider generation, requests, decoded image
caches, and directory scan generation are not changed.

NivisViewer retains its large folder icon as a usability modernization. The
icon target is aspect-fitted within at most 50% of each shared content-rectangle
dimension and centered from that rectangle at 100%, 125%, 150%, and 200%
device scaling. Broken/unreadable archive
states with no valid image use the same canvas and icon layout authority; the
existing error badge remains separate. The former translucent lower-left plate
was NivisViewer's own `_paint_type_icon` operation: a 105-alpha black brush
painted a rounded rectangle expanded two logical pixels around the badge.
That operation alone is removed. The associated image is still drawn at the
same lower-left target and its source alpha remains authoritative.

##### 24.3.1.2 Exact directory rating grammar

`CatalogForm.SetRating` and `SetSelectedArchiveRating` construct
`ZipPlaInfo`, assign `Rating`, and call
`GetPathOfCurrentInfo(name.Last() == Path.DirectorySeparatorChar)`. Folder
rating is therefore part of the fixed-revision Catalog behavior, including
multi-selection. `ZipPlaInfo.Rating` accepts null or integers 1 through 5.
The case-insensitive metadata block consumes at most one leading space and
semicolon-separated recognized parameters; serialization follows prototype
order `c`, `b`, `r`, `t`, `d`.

The important directory rule is the `isDir` branch of
`GetPathOfCurrentInfo`: it removes metadata from `Path.GetFileName(path)` and
uses no suffix. The file branch instead separates
`Path.GetFileNameWithoutExtension` and `Path.GetExtension`. Consequently a
directory dot is ordinary name content, not an extension. NivisViewer's
existing `ZipPlaFilenameMetadata` parser/serializer is reused with
`is_directory=True`; no folder-rating database exists. Representative results
are:

- `Folder` -> `Folder {zpi$r=3}`;
- `Folder.Name` -> `Folder.Name {zpi$r=3}`;
- `Folder {zpi$r=3}` -> clear -> `Folder`;
- `Folder {zpi$t=foo}` -> `Folder {zpi$r=3;t=foo}`;
- `Folder {zpi$r=3;t=foo}` -> clear -> `Folder {zpi$t=foo}`.

Changing a rating thus canonicalizes known parameters without losing cover,
binding, tags, or direction; clearing removes only `r` and removes the block
only if no other known parameter remains.

##### 24.3.1.3 NivisViewer rename and relocation mapping

Folder rating does not call the file-only `RatingRenameService` and does not
introduce another `os.rename` path. `BrowserWindow.set_rating_for_paths`
serializes the destination name, then submits sequential
`FileOperationKind.RENAME` requests through the existing
`FileOperationCoordinator`/`FileOperationService`. This retains existing
Windows filename validation and collision policy. Coordinator completion
relocates the full `MetadataStore` tree, including child metadata and Browser
bookmarks; Browser completion relocates `BrowserNavigationHistory`, including
timeline paths, selected paths, and recent-directory entries.

Successful destinations are then applied to the existing
`BrowserItemModel.apply_rating_renames` authority. That method relocates
path-keyed thumbnail/dimension state, reruns rating filter/sort, and publishes
one model reset. The captured Browser selection, current item, viewport anchor,
and scroll values are remapped through the same replacement list and restored
with the existing minimal-movement policy. No directory rescan or thumbnail
decode/request is initiated by the rating update.

Before a rename, `FileOperationService` records the source timestamp. After a
successful rename it restores the original `st_mtime_ns` when the platform
changed it, using a no-follow `os.utime`; an inability to restore is logged as
a best-effort warning because the filesystem rename has already succeeded.
The operation never opens or rewrites descendants, so child bytes and child
timestamps are not modified.

### 24.4 Browser location breadcrumb and timeline projection (2026-08-22)

#### 24.4.1 Fixed-revision ZipPlaFork structure

The location UI was audited at fixed ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`. In
`source/ZipPla/ZipPlaAddressBar.cs`, `ZipPlaAddressBar.makeButtonList` validates
the current text, splits ordinary paths on the Windows directory separator,
special-cases the UNC prefix, and creates one `DirectoryButton` plus one
separator button for every component. Each directory button retains its full
path. `directoryButton_Click` replaces the text with that path, rebuilds the
buttons and raises `TextChangedByButton`; the current component instead raises
`CurrentLocationButtonClick`. `separatorButton_Click` derives the path to the
left of the separator and opens the items returned by
`getDirectorySeekingMenuItemArray2`. That method enumerates child directories,
marks the currently selected child and recursively supplies deeper dropdowns.
The original control also attaches one `FileSystemWatcher` per usable
separator and implements drag/drop on components and dropdown items.

`source/ZipPla/CatalogForm.cs` constructs `zabLocation` over editable
`cbLocation`, wires `TextChangedByButton` to Catalog navigation, and persists
the combo's `locationHistory`. Back/forward use one `undoBuffer` timeline and
`undoBufferIndex`; `gotToUnderBuffer` changes the index and restores
`UndoBufferClass.Location`, `SelectedFileName`, `ThumbnailPosition` and
`FileListPosition` while also carrying Catalog profile/color state.
`btnGoToBack_Click` and `btnGoToForward_Click` move one entry. Right mouse-down
on either button calls `showUndoBufferList`, which builds the full reverse
timeline, marks the current entry and jumps through `gotToUnderBuffer`.
`readme_original.txt` records the modern location bar, back/forward commands,
right-click timeline popup, removal of missing location-history paths, UNC
fixes and high-DPI navigation-button corrections.

#### 24.4.2 NivisViewer Hybrid implementation

NivisViewer retains its existing `BrowserNavigationHistory` as the only
navigation timeline. `BrowserLocation` already owned the filesystem location,
selected absolute path and vertical/horizontal grid scroll values, and
Browser scan commit already restored them after row topology became available.
The history now exposes immutable entries, current index, direct `go_to` and a
deduplicated recent projection; it does not gain sort, search or rating state.
Failed asynchronous direct jumps restore the previous timeline index.

`BrowserLocationBreadcrumb` replaces the always-visible text field in normal
mode. Every Qt segment stores its absolute path; an ancestor click uses the
ordinary `BrowserWindow.navigate_to` path, while clicking the current segment
is a no-op. Each separator requests visible child directories from a
latest-request background worker and immediately shows a loading popup, so
`os.scandir`, attribute access and natural sorting do not block the GUI
thread. Hidden/system visibility uses the same `BrowserVisibilityPolicy` as
the main scan. The worker is generation-fenced and close-cancelled; it does
not add a watcher or persistent filesystem handle.

Deep paths keep the current component and nearest ancestors, folding older
components into an ellipsis menu. Component text is middle-elided from Qt
font metrics and remains device-independent at high DPI. `Path.parts` retains
drive roots, UNC share roots, Japanese components and absolute path identity.
Archive-internal breadcrumbs and breadcrumb drag/drop are deliberately not
introduced in this unit.

Ctrl+L or blank-area/double click switches the same toolbar location slot to
the existing editable `BrowserAddressBar`. Enter uses the established
directory/image/archive path handling; Escape and focus loss discard edits
and restore the committed breadcrumb. Back/forward QAction shortcuts and
extra mouse buttons remain unchanged. Their QToolButtons now expose
directional right-click menus projected from the same timeline. A separate
recent-location button deduplicates that timeline by normalized path; choosing
one performs a normal new visit rather than mutating a second history store.
Missing locations use the existing asynchronous scan failure/rollback UX.

Directory navigation still follows one production path:

```text
breadcrumb / text / back-forward / timeline / recent location
  -> BrowserWindow.navigate_to
  -> BrowserDirectoryScanner with current visibility + sort policy
  -> BrowserItemModel source snapshot
  -> search AND rating predicates
  -> stable visible order
  -> selected path + grid scroll restoration
  -> immutable Browser-to-Viewer folder snapshot
```

Thus breadcrumb and history navigation do not introduce alternate sort,
filter, selection or Viewer topology authorities. Thumbnail completion is not
a restoration gate.

#### 24.4.3 Provenance

The breadcrumb component/path and separator-dropdown concepts derive from
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork), fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, specifically
`source/ZipPla/ZipPlaAddressBar.cs` (`ZipPlaAddressBar`, `makeButtonList`,
`DirectoryButton`, `directoryButton_Click`, `separatorButton_Click`,
`getDirectorySeekingMenuItemArray2`, `TextChangedByButton`,
`CurrentLocationButtonClick`) and `source/ZipPla/CatalogForm.cs`
(`zabLocation`, `cbLocation`, `locationHistory`, `btnGoToBack`,
`btnGoToForward`, `undoBuffer`, `gotToUnderBuffer`, `showUndoBufferList`). The
release-history statements cited above derive from `readme_original.txt`.
That source is AGPL-3.0-or-later; notices and license text remain in
`licenses/ZipPlaFork/About.txt`, `licenses/ZipPlaFork/AGPL.txt` and
`THIRD_PARTY_NOTICES.md`. Qt layout elision, the cancellable worker, immutable
history projections, structured scan rollback and integration with
NivisViewer's model/filter/Viewer snapshot are NivisViewer-specific
modernizations. WinForms controls, per-separator watchers, GDI painting,
Catalog profile switching and breadcrumb drag/drop were not copied.

#### 24.4.4 Windows popup lifetime correction and toolbar placement

The first Qt breadcrumb implementation opened a disabled one-row `QMenu`
immediately and then called `clear()` on that visible popup when asynchronous
directory enumeration completed. On Windows this mutated the native popup
while it owned popup focus and transiently reduced it to a zero-action frame.
Rapid arrow clicks also closed and replaced parent-owned menus without a
same-request admission guard. The resulting focus/regeometry window was the
empty framed popup observed on hardware; directory enumeration itself was
already outside the GUI thread.

The corrected path performs no popup creation while the worker is running.
Repeated clicks for the same component coalesce into one generation-fenced
request. Completion re-resolves the separator button from the current
breadcrumb (so resize cannot leave a stale anchor), constructs the complete
list before showing it, clamps the final non-empty popup to the anchor's
current screen, and shows it non-modally exactly once. Escape, outside click,
Browser resize, location commit and shutdown release the single owned popup
and restore Browser focus. A changed path cancels the pending generation. The
rating quick-filter context menu likewise remains an owned non-modal popup.

The controls retain their existing authorities but now use exactly two top
rows. The first is the existing File/View/Bookmarks/History menu row with the
five-star rating quick filter as its vertically centred top-right corner
widget. The single toolbar row underneath contains back/forward/up/refresh,
the expanding native-combo-chrome breadcrumb/location control, sort
key/direction, folders-first, density and a compact native-combo-chrome
Unicode search edit. Search may shrink to 140 logical pixels and the complete
field including its native dropdown subcontrol never exceeds about 220; the
location control receives remaining width. The layout does not wrap at narrow
widths and uses Qt size policies rather than physical-pixel positions.

#### 24.4.5 Bounded location/history projection and retention

Directory children, recent locations and back/forward timeline jumps now use
the same `BrowserLocationListPopup`: an owned `QFrame(Qt.Popup)` containing a
uniform `QListWidget`, not a native action-per-row `QMenu`. Its height is
derived from the active font and capped at 14 visible rows independently of
the number retained. Overflow uses the list's vertical scrollbar and normal
Qt wheel, arrow, Page Up/Page Down, Home/End and Enter handling; Escape and
outside click close the popup. Geometry is clamped to the available geometry
of the anchor's screen and flips above the anchor when necessary. Navigation,
resize and shutdown also close it. No nested `exec()` loop or synchronous
path validation is introduced.

`BrowserNavigationHistory` remains the sole navigation authority but now
owns two explicitly different projections. Its session timeline continues to
hold the current index and back/forward branches. A bounded normalized-path
MRU supplies the recent-location popup. Successful normal visits and restored
timeline visits move that location to the MRU front; a duplicate path is
removed first. `browser_location_history_limit` controls only this MRU with a
custom range of 1 through 1000 (default 50). Changing it trims the MRU tail
immediately without changing timeline entries or the current timeline index.
Popup height remains 14 rows even when hundreds of locations are retained.

The recent popup does not synchronously call `exists()`/`stat()` across its
entries, which avoids blocking the GUI thread on UNC and unavailable network
paths. A selected location travels through the existing asynchronous Browser
scan. If that scan reports the location missing, only that MRU entry is
removed and the existing non-modal failure status is shown; the active folder
and back/forward timeline remain intact.

This layout and scrolling behavior is derived from
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork), fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:
`source/ZipPla/CatalogForm.Designer.cs` supplies the menu-row plus combined
location/sort/filter-row structure, while `readme_original.txt` records the
modern location bar, removal of missing location-history paths, and mouse
wheel/Page Up/Page Down scrolling when location folder lists exceed the
screen. That source is AGPL-3.0-or-later; its notices remain in
`licenses/ZipPlaFork/About.txt`, `licenses/ZipPlaFork/AGPL.txt` and
`THIRD_PARTY_NOTICES.md`. The finite Qt popup, split timeline/MRU lifetime,
asynchronous click-time validation and DPI-derived geometry are
NivisViewer-specific modernizations.

#### 24.4.6 Compact rating state and separate search-query history

The menu-row rating control now uses only its five-star visual state. The
always-visible `レート`, `以上`, `のみ` and `未評価` labels were removed.
Inactive state uses outline stars, threshold/equality modes fill through the
selected star. Exact mode, unrated state and threshold remain available in the
dynamic tooltip and the existing non-modal context menu. There is no separate
mode label, `未` marker or clear button: clicking the active threshold again
clears it, while the context menu retains an explicit clear action. This
changes presentation only; `BrowserFilterState` remains the sole rating/search
predicate authority.

Location history and file/book metadata history are explicitly not combined.
The recent-location projection continues to receive only successful Browser
directory scan targets. Opening an image or archive path through the location
editor navigates to and records its parent directory while the selected file
remains restoration state. Individual image/archive paths therefore do not
appear in the location MRU. The existing first-activation select-all behavior
of `BrowserAddressBar`, Ctrl+L, Enter and Escape behavior is unchanged.

The search edit is the content widget of a native `QComboBox` shell, backed by
the same bounded 14-row popup presentation used for directory/history lists.
Qt style draws the frame, dropdown subcontrol and arrow; no Unicode arrow or
adjacent `QToolButton` participates. Its data authority is a separate
`BrowserSearchHistory`, because query strings are
neither filesystem locations nor navigation timeline entries. It records one
trimmed non-empty query on Enter, explicit dropdown opening or history
selection; focus loss and the 100 ms incremental filter states are never
recorded. Duplicate detection uses Unicode `casefold()` while preserving the
most recently entered spelling. Selecting a query immediately feeds the
existing in-memory search predicate without a directory scan. The popup also
exposes a compact clear-history command and retains wheel, scrollbar, arrow,
Page Up/Page Down, Home/End, Enter, Escape, outside-click and screen-clamped
behavior without `exec()`.

`browser_search_history` is persisted as an MRU, while the active search text
remains session-only and starts empty on every Browser construction.
`browser_search_history_limit` accepts 0 through 1000 (default 50); zero clears
and disables query history. The location MRU independently accepts 1 through
1000. Both settings use custom `QSpinBox` input and trim immediately, but only
the location MRU belongs to `BrowserNavigationHistory`; its back/forward
timeline is never truncated by either setting.

#### 24.4.7 ZipPla-style operation-density simplification

At fixed revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`,
`source/ZipPla/CatalogForm.Designer.cs` keeps `cbLocation` (234 by 20),
`cbSortBy` (135 by 20) and `cbFilter` (182 by 20) compact on the same Catalog
row; the rating affordance is the text `★★★★★`, without an adjacent clear
button. `source/ZipPla/CatalogForm.cs` registers the current filter from
`cbFilter_DropDown` with `setCurrentFilterToHistory(bringToTop: false)`, while
back/forward right-click calls `showUndoBufferList`. The fixed revision's
`source/ZipPla/ZipPlaAddressBar.cs` switches one address-bar region between
breadcrumb buttons and its editor on focus instead of placing a separate
recent-history button beside it. These files and behaviors are
AGPL-3.0-or-later-derived comparison material; notices remain in
`THIRD_PARTY_NOTICES.md`.

NivisViewer now follows that operation density without copying the WinForms
widgets. One expanding native `QComboBox` shell owns the breadcrumb/editor
stack and its style-provided dropdown subcontrol. Clicking the current segment or empty
breadcrumb area enters path editing; ancestor segments navigate and separator
chevrons enumerate children. The native final dropdown subcontrol alone projects the
directory-only MRU. Back/forward right-click continues to project the separate
timeline, so the two histories are not merged. The removed standalone
`履歴 ▾` widget has no compatibility alias or hidden replacement.

The sort controls and the integrated search/history field remain on the same
toolbar row. Search has a 190 device-independent-pixel preferred outer width,
a 150-pixel responsive minimum and a 230-pixel maximum. The location area
receives all remaining stretch space. Folders-first and display-density
controls are no longer duplicated on this high-frequency row; their existing
Settings authority remains live and persistent.
The toolbar and menu/rating row remain the only two top UI rows. History
limits, persistence, bounded non-modal popups, Unicode matching, stable
filter/sort authority and Browser-to-Viewer visible-order snapshots are
unchanged.

#### 24.4.8 Native dropdown chrome, stable rating toggle, and folder canvas

The fixed revision uses real WinForms `ComboBox` controls for `cbLocation`,
`cbSortBy` and owner-drawn `cbFilter` in
`source/ZipPla/CatalogForm.Designer.cs`; their dropdown arrows are native
control chrome, not text-glyph buttons. NivisViewer now uses a small
`QComboBox` shell for both custom fields. Its edit-field subcontrol hosts the
existing breadcrumb/editor stack or search `QLineEdit`, while `showPopup()`
projects the existing bounded directory/query MRU. The current Qt style owns
border, hover, pressed, focus, disabled and high-DPI arrow painting, matching
the existing sort selectors without a bitmap asset or literal arrow glyph.

At the same revision, `CatalogForm.cs`
`ratingFilterToolStripMenuItem_MouseMove` derives `ratingReferenceValue` from
the painted five-star rectangle and `ratingFilterToolStripMenuItem_Click`
dispatches the selected rating expression. NivisViewer retains its simpler
`>=` left-click contract, but now captures the pressed star and applies that
stable reference on release. A small pointer movement across a fractional-DPI
star boundary therefore cannot turn an intended same-star clear into a new
threshold. The active `>= X` star toggles off on the same left click; every
mode remains clearable from the context menu, and middle click is an
additional label-free clear gesture. Search and sort state remain unchanged.

ZipPlaFork's `CatalogForm.cs` `ThumbViewer` paint path distinguishes
`LoadResult.NotYet`/error from completed thumbnails and uses
`drawFileIconImage` for an optional small associated icon. Its large
`drawFileImage` fallback is disabled by an early return at this revision.
`ThumbViewer.DrawItem`, however, first fills the complete rectangle returned
by `getThumbnailRectangle` with `BackColorBrush`, and `setColors` supplies
black through `tvCatalog.SetBackAndForeColor(Color.Black)`. NivisViewer uses
that black canvas as its automatic folder-fallback reference. The existing
centered folder icon is a deliberate Qt addition; it is painted directly on
the same square-cornered, physical-pixel-snapped maximum image target used by
the active fit or center-crop mode, with no placeholder-specific inset,
rounded card, internal border, back plate or shadow. Pending/loading folders may show the
stable fallback; broken/unreadable archives use it when their thumbnail has no
valid image; a completed preview takes the existing image branch. This is
delegate-only paint: it adds no scan, read, decode, thumbnail request,
per-folder pixmap or cache-key state.

The ZipPlaFork comparison above is against repository
`himamon/ZipPlaFork`, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, files
`source/ZipPla/CatalogForm.Designer.cs` and
`source/ZipPla/CatalogForm.cs`, and is AGPL-3.0-or-later-derived. No source
method was directly translated for the Qt shell or folder canvas; preserved
notices remain in the locations listed in section 24.3.

#### 24.4.9 Search width is based on the editable content rectangle

At the fixed revision, `CatalogForm.Designer.cs` gives `cbLocation` a
left/right anchor and an initial 234 by 20 size, while right-anchored
`cbSortBy` and `cbFilter` are 135 by 20 and 182 by 20 respectively;
`cbFilter.MinimumSize` is 153 pixels. Those numbers describe one native
WinForms ComboBox. They are not directly usable as the outer size of
NivisViewer's Qt shell, which also contains a native dropdown subcontrol and a
clearable `QLineEdit`.

The former Qt shell inherited its size hint from one empty ComboBox item and
therefore collapsed to 30 logical pixels. Its child editor still requested
140 pixels and was clipped beyond the shell. Although that child reported 118
pixels after its 22-pixel clear-button allocation, the native shell exposed
only a 5-pixel edit-field rectangle, so the effective text area was negligible.
The shell now
publishes an explicit preferred size and lays out its child from
`QStyle.SC_ComboBoxEditField`, so the active platform style remains the
authority for frame and arrow geometry. The first correction used a wide
360/240/460 preferred/minimum/maximum shell to prove that clipping was gone.
After real-device density feedback, the final allocation is 190/150/230.
At 1x offscreen Windows style from an 800 through 3840-pixel Browser, the
normal result is a 190-pixel outer shell, a 165-pixel edit field and 143
pixels after the clear-button allocation. Removing the two low-frequency
toolbar controls lets the location field retain the released space instead of
forcing the search shell below its preferred width.

This is a NivisViewer responsive-layout correction informed by ZipPlaFork's
fixed-control-versus-stretch allocation, not a translation of WinForms layout
code. Search history, Unicode/IME editing, debounce, clear action, filtering,
sorting and Browser-to-Viewer order authority are unchanged. The comparison
source remains `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, file
`source/ZipPla/CatalogForm.Designer.cs`, under AGPL-3.0-or-later.

#### 24.4.10 Minimal viewport movement and low-frequency settings ownership

ZipPlaFork's fixed-revision `CatalogForm.cs`
`cbSortBy_SelectedValueChanged` (`:14745-14805`) keeps the selected data item
stable while replacing `ThumbViewer.ShowIndexToDataIndex`, then calls
`ScrollBarToIndexWithMinimalMove` (`:30725-30748`). That method returns without
scrolling when the selected thumbnail is fully visible and moves only the
nearer boundary when it is outside. Other refresh paths preserve
`ThumbViewer.ScrollBarPercentage` (for example `:20746-20748` and
`:25626-25628`), and Browser history persists the thumbnail percentage at
`:7311`/`:7512`. `readme_original.txt` records the same contracts: display
refresh position preservation (`:473`), back/forward scrollbar restoration
(`:474`), exact minimal list movement (`:519`) and minimal selected-thumbnail
movement (`:583`).

NivisViewer previously captured the first visible stable path, but every
model reset restored it with `QListView.PositionAtTop`. This discarded its
pixel offset, and the next sort captured the already-shifted viewport. It also
treated a reset-generated `currentIndex` as meaningful even with no actual
selection. The replacement distinguishes two cases. With selected indexes it
restores path identity and uses `EnsureVisible` only when the current selected
cell is not fully contained. With no selection it restores the first visible
stable path to its captured x/y viewport offset; if that path disappeared it
uses the nearest surviving row, then the previous clamped scroll values.
Programmatic restoration suppresses thumbnail scheduling, so sort key,
direction and folders-first changes retain source/model thumbnails without a
filesystem scan, cache generation or decode request. Geometry-changing
Settings such as thumbnail size reuse the same anchor contract and then
request only the thumbnails needed by the new render specification.

The Browser Settings tab was already the persistent authority for
`browser_folders_first`, `browser_display_density` and `thumbnail_size`.
NivisViewer therefore removes only the duplicate folders-first checkbox and
density ComboBox from the toolbar; Settings changes continue to project into
the open Browser through `ConfigManager.changed`. This is a Hybrid adoption of
ZipPlaFork's minimal-movement behavior with Qt stable paths and per-pixel
scrollbars, not a translation of the WinForms `ThumbViewer` implementation.

For unavailable thumbnails, fixed-revision `CatalogForm.cs`
`tvCatalog_ThumbnailPaint` (`:24715-24805`) distinguishes
`LoadResult.NotYet`, errors and completed artifacts. `drawFileIconImage`
(`:6478-6499`) places an optional small associated icon at bottom-left after a
real thumbnail. Its large `drawFileImage` fallback (`:6546-6588`) returns
immediately at this revision because the old loader state could otherwise
overlay icons on completed thumbnails. The underlying `ThumbViewer.DrawItem`
canvas is nevertheless the exact thumbnail rectangle and is filled black by
the Catalog color setup. NivisViewer keeps its clearer pending/loading/no-
preview versus completed-preview state boundary and now uses the real
thumbnail's shared content rectangle, black in automatic mode, plus its
centered large icon. Broken/unreadable archives enter the same shared
placeholder branch. No extra internal border is added; the normal
thumbnail-frame outline is shared with real thumbnails. This remains
delegate-only paint and adds no filesystem access, decode, thumbnail request
or cache entry.

Filter changes also preserve selected identities rather than proxy row
numbers. If a selected path becomes invisible, restoration clears that path
without manufacturing a replacement selection; the captured visible anchor
may still preserve viewport offset. A real selection made while filtered is
captured by the next transition and remains authoritative when the filter is
cleared. This corrects NivisViewer's generic list-view restoration rather than
adding a rating-filter-specific selection system.

All source references in this subsection are from repository
`himamon/ZipPlaFork`, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, files
`source/ZipPla/CatalogForm.cs`, `source/ZipPla/CatalogForm.Designer.cs` and
`readme_original.txt`, under AGPL-3.0-or-later. The original notices remain in
the locations listed in section 24.3.

### 24.5 Independent rating quick filter and plain search

The fixed revision's upper Catalog UI has three separate authorities.
`source/ZipPla/CatalogForm.Designer.cs` places
`menuStripForTagFilter` and its Gold `ratingFilterToolStripMenuItem`
(`★★★★★`) on the upper row, while `cbSortBy` and `cbFilter` occupy the row
below. In `source/ZipPla/CatalogForm.cs`,
`ratingFilterToolStripMenuItem_Click`, its mouse-down helpers and
`ratingFilterToolStripMenuItem_MouseMove` map the pointed star to
`ratingReferenceValue` 1..5 and produce `r>=X` or `r=X`; modifier variants
append AND/OR forms to the generic filter. `readme_original.txt` documents
quoted text, wildcard matching, `+` OR, `-` NOT, `+-` OR-NOT, and rating
comparisons such as `r=3` and `r<2`.

NivisViewer adopts the separation, not the complete query grammar. The
expanding session-only plain filename/display-name search occupies the right
side of the existing sort row; the fixed five-star quick filter occupies the
right side of the File/View/Bookmarks/History menu row. Left-clicking star X
selects `rating >= X` and clicking the same condition again clears it. The
context menu supplies `>= X`, `= X`, unrated and clear. Hover paints the
prospective threshold without modifying filename metadata. Search uses
stripped Unicode `casefold()` substring matching and a 100 ms UI debounce;
Escape or the line-edit clear affordance clears it. Search text and rating
filter are deliberately not persisted, so a later application start cannot
unexpectedly hide files. Existing sort key/order persistence is unchanged.

`BrowserItemModel` is the sole owner of the in-memory visible list. Its
pipeline is the current scanned source snapshot, internal/visibility policy,
search predicate AND rating predicate, stable Browser sort, then the visible
ordered rows. The visibility settings that require filesystem attributes
(hidden/system/unsupported) remain inputs to the scanner and are represented
in the same final snapshot identity; typing never starts another scan. A
future parser can replace `BrowserSearchPredicate` behind the
`BrowserItemPredicate` boundary without adding a second list authority.

Rating renames update the source item, relocate its existing thumbnail and
other path-keyed caches, rerun the predicates and sort, then restore the
nearest surviving selection/current row and scroll anchor. No thumbnail
decode or directory scan is requested merely to reevaluate the filter. Folder
Viewer open snapshots are built from this exact final visible order and stamp
search/rating mode into `filter_identity`; main navigation and virtual
PageList therefore retain the same filtered/sorted topology for that book
session.

This UI/predicate structure derives from
[`himamon/ZipPlaFork`](https://github.com/himamon/ZipPlaFork), fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, specifically
`source/ZipPla/CatalogForm.Designer.cs` (`menuStripForTagFilter`,
`ratingFilterToolStripMenuItem`, `cbSortBy`, `cbFilter`),
`source/ZipPla/CatalogForm.cs` (rating filter click/move and `r>=X`/`r=X`
generation), and `readme_original.txt` (comparison, wildcard, quoted-search,
OR/NOT grammar). It is AGPL-3.0-or-later; preserved license and copyright
notices remain in the locations listed in section 24.3. The compact Qt
control, composable predicate boundary, Unicode plain-search policy, debounce,
session-only state and immutable Browser-to-Viewer snapshot are
NivisViewer-specific modernizations.

### 24.6 Registered-tag quick buttons in the menu-bar corner

The fixed ZipPlaFork source builds one `ToolStripMenuItem` per registered tag
in `CatalogForm.setTagFilteringMenuItems` and places the tag items beside the
rating item in `menuStripForTagFilter`. A normal click applies an AND include;
Shift applies an AND exclude; Ctrl applies an OR include; Ctrl+Shift applies an
OR exclude. Middle-click selects the direct include/exclude variants, and the
overflow button retains every registered tag when the available width is too
small (`CatalogForm.cs:21967-22055,22057-22105,22256-22315`).

NivisViewer keeps the existing `タグ` menu as the complete editor/filter
entry point and adds `BrowserTagQuickFilterStrip` immediately to its left.
Registered tags follow management order from left to right. Left-click toggles
an AND include while preserving the current
search, rating and exclusion predicates. Ctrl-click toggles an include and
selects the existing OR mode; Shift-click toggles an exclusion; Ctrl+Shift
keeps the OR mode while toggling an exclusion. The quick buttons never rewrite
filename search text, and the full TagFilterDialog remains authoritative for
explicit AND/OR and mixed include/exclude editing.

The saved `browser_tag_grouped` option (default off) hides the separate strip
and puts all registered filter checkboxes at the top of the existing `タグ`
popup, followed by selected-item tags, detailed filtering, tag management and
`選択の解除`. That last action is enabled only while include/exclude tag
filters exist; it clears those filter selections while retaining filename
search, rating and file selection. Exclusion is shown with a leading `−` in
the grouped popup. The separate mode keeps the compact strip and overflow
button, while both modes rebuild immediately after registry edits and filter
changes.

The strip measures the actual menu-bar corner space before Settings and the
existing tag/rating controls. It shows as many registered buttons as fit,
keeps the management order and moves later entries to a compact overflow menu.
If even that overflow affordance cannot fit, the
strip collapses while the existing `タグ` menu remains available, so no
registered tag becomes inaccessible. Registry edits rebuild the button order,
colors and overflow contents immediately. Offscreen tests cover real button
clicks, search/rating/exclusion preservation, OR toggles, registry changes,
overflow reachability and resize geometry.

This is a Qt-specific responsive adaptation rather than a WinForms control
port. The source remains `himamon/ZipPlaFork`, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, under AGPL-3.0-or-later; the
existing license and copyright notices remain in the locations listed in
section 24.3.

## 25. Immediate post-open ZIP navigation and speculative-work preemption

2026-08-23に、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の次のauthorityを再確認した。

- `source/ZipPla/PackedImageLoader.cs`, class `PackedImageLoader`, fields
  `stream`, `zipArchive`, `zipArchiveEntries`; constructor
  `PackedImageLoader(..., PackedImageLoaderOnMemoryMode)`; methods
  `getZipArchiveEntries`, `OpenImageStream`, `OpenInnerImageStream`, `Dispose`。
  defaultの`None`は`File.OpenRead(path)`を保持するfile-backed pathである。
  whole-file `MemoryStream` copyは`OnMemory`だけであり、`Releasable`も最初は
  file-backedである。ZIPは保持した`ZipArchive(stream, Read)`とentry配列から
  選択entryだけを`entry.Open()`し、page処理後にentry streamをdisposeする。
  loader dispose時にarchiveと元streamをまとめて閉じる。
- `source/ZipPla/ViewerForm.cs`, class `ViewerForm`, open/reload process
  `RunWorkerAsyncWithInterrupt` / `bmwLoadEachPage_RunWorkerStarting`、page job
  `bmwLoadEachPage_DoWork`、completion
  `bmwLoadEachPage_EachRunWorkerCompleted`、scheduler
  `SetBackgroundMode` / `SetBackgroundModeIfPausing` / `priorityLevel`、memory
  `SetNewResizedImage` / `ReduceUsingMemory`。open/reloadはpage数のwork arrayを
  作り、開始時にloader/entry arrayとsource/display配列を切り替える。
  `bmwLoadEachPage_DoWork`は支配的な`loader.OpenImageStream`が排他的なので
  multi-threadが有効でないと明記し、entry open、decode/filter、resizeを1 page
  jobで行う。`SetBackgroundMode`は全pageをcurrentまたはtrackbar pointerからの
  距離で並べ直し、`priorityLevel`でvisible current、直後、直前、遠方の順にし、
  `SetWorksOrder`後の`ThreadCount`を1にする。completionでcurrent変更を検出する
  たびにorderを再計算する。
- `ReduceUsingMemory`はそのsame work orderの遠方端からartifactを捨て、
  `SetNewResizedImage`は新artifactを入れる前に容量を作る。current近傍をorder先頭、
  挿入中indexをprotectedにするため、遠方より小さい実用近傍が先に保持される。

NivisViewerの`ZipImageSource`もbook lifetimeの`zipfile.ZipFile`を1個保持し、
`list_images()`はcentral directory entry metadataだけを列挙する。page readは
`ZipExtFile`からそのentryだけを最大1 MiB chunkで`QByteArray`へ読み、cancelを
chunk境界で確認する。open critical pathにarchive payload全体のcopy、全entry
image decode、または並列archive readerはない。したがってphysical ZIP total
bytes自体はpage decode critical pathではなく、entry countに応じたcentral
directory列挙だけがbook open側で増える。

実際の残存競合は`RasterBookRuntime`のstarted-work adoption policyだった。
first complete frame commitがcontinuous warmupをreleaseすると、plannerは
4 forward + 1 reverseのinteraction runwayを先頭にしたall-book orderから
1 jobをsole laneへ投入する。navigationは毎inputでplannerをrecenterしていたが、
active jobがnew planとartifact-compatibleなら、new currentがcoldでも常にその
started jobをadoptした。このため、例えばpage 1 warmup中にrapid inputのfinal
targetがpage 10になると、page 10はpage 1のread/decode/resize終了まで開始できない。

修正後の`RasterBookRuntime._adopt_request`は同じscheduler/cache authorityを使い、
started jobを次の場合だけ保持する。

1. active job自身がnew currentである（neighbor prefetchの有益なadoption）。
2. new current frameが既にframe cacheにある、またはcompleted artifactがGUI publish
   待ちであり、background jobがpresentation latencyを増やさない。

それ以外のcold currentでは、compatibleであってもunrelated warmupをcooperative
cancelする。workerは次のsource/decode cancellation boundaryでsole laneを空け、
final staged requestが先にdispatchされる。ready hitではbackgroundを不要に捨てず、
currentそのもののstarted prefetchも継続する。all-book warmup、4+1 runway、
continuous recenter、source/frame分離cache、memory admissionとsingle laneは維持した。
これはZipPlaForkのpersistent file-backed loader、single lane、current-centered order、
近傍retentionを採用しつつ、replaceable inputとcooperative cancellationを加えたHybrid
processであり、C#実装の逐語翻訳ではない。

`scripts/benchmark_viewer_navigation.py` schema 3はproduction
`ZipRasterBookRuntime`をoffscreenで開き、first-visible直後のrapid wheel finalを
warm-idle controlより前に測る。benchmark-only source wrapperがentry read byte/call、
decode start/complete/abandonを記録し、input時active key、cache hit/miss、runtime
metric deltaも出す。`--archive-padding-mib`はimage count/dimensionsを変えず、sourceが
無視するstored non-image bytesだけを加える。`--zip-compression`はimage entryの
deflated/storedを切り替え、`--pages`とpaddingの組合せで近いtotal sizeのpage-count
比較もできる。production timestamp loggingや別schedulerは追加していない。

同一SHA-256 fixture（13 page、4096 x 6500、1920 x 1053 viewport、final page 10、
offscreen）の変更直前/直後runでは、first-visibleからrequestまで0.048/0.046 ms、
request-to-paintは44.111 msから40.036 msへ4.075 ms（9.2%）短縮した。別の最終runの
warm-idle cache-hit controlは1.786 msだった。timingは短いoffscreen runなので
実機体感の代替ではないが、blocking fixture testではstarted page 1がcold page 10
より前に保持されないことを決定的に確認した。一方、started page 1自身へ移動する
testではcancel 0でadoptionを維持した。

同じ13 page/geometryで64 MiBのignored stored paddingを加えた67,122,561-byte ZIPは、
open-to-first-paint 42.632 ms、immediate final 49.927 msで、target commit前にpadding
readは0だった。paddingなし13,525-byte ZIPの別runは38.918/42.526 msであり、差は
run間変動を含むがcritical intervalのentry bytesはpage 1とtargetの834,793 bytesに
限定された。stored-image ZIP（5,427,613 bytes）は37.825/34.476 msだった。
同じ4096 x 6500で約67.2 MBへ揃えた65-page fixtureはopen 57.829 ms、
immediate 48.079 msだった（13-page側は42.632/49.927 ms）。
これらはtotal ZIP payloadをcritical pathでcopy/readしていないことを支持し、
page countはentry metadata列挙量、compressed/storedは選択entry read/decode costへ
影響するという境界を示す。

このsectionのZipPlaFork由来process provenanceはrepository
`himamon/ZipPlaFork`、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、上記
`ViewerForm.cs` / `PackedImageLoader.cs` methods、AGPL-3.0-or-laterである。
license本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 26. Rapid navigation: ready-frame progression and final-target priority

2026-08-23に固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/ViewerForm.cs`を、decode orderだけでなくvisual presentationまで
再確認した。

- `ViewerForm.NextPage`（1773--1817）は必要な`resizedImageArray` elementが
  `null`またはunknownのとき自然送りを進めず、readyな範囲だけを進む。
  `moveToNextPage`（11635--11643）はこの`movePageNatural(1)` pathを使う。
- `movePageNatural`（8735--8813）はlegitimateな各advanceで`currentPage`を更新し、
  `showCurrentPage`を呼ぶ。したがってready resized artifactを横切るnatural inputは
  intermediate current pageをvisual pathへ渡す。
- `showCurrentPage(bool)`（6100--6145）はloading中を含めて
  `pbPaintInvalidate()`する。background completion
  `bmwLoadEachPage_EachRunWorkerCompleted`（5103--5138）もcompleted workがcurrentへ
  関係すると`showCurrentPage(false)`を再度呼ぶ。
- work側は`SetBackgroundMode` / `priorityLevel`（5278--5323）でcurrent、next、
  previous、distantの順へsingle workerを並べ直す。つまりZipPlaForkはready resized
  pagesではcurrent/presentationを段階的に進め、cold boundaryではnatural advanceを
  止め、そのcurrent近傍をwork priorityへ反映する。

これはNivisViewerのreplaceable rapid inputと同じcontractではない。NivisViewerは
`NavigationAdmissionPolicy`でfirst wheel packetをimmediateにし、同方向の次packetが
40 ms以内ならcold requestをreplaceable `STAGE`にする。trailing boundaryはpacket
cadence + 2 msを6--28 msへclampする。ready frameはこのdecisionを観測してもstageせず、
existing `RasterBookRuntime.request` cache-hit pathから同期的に
`PresentationState` / `ViewerWidget.commit_display_ready_frame`へcommitされる。cold transit
だけが置換され、boundaryで最新targetをdispatchする。

調査前の想定と異なり、ready intermediateはobsolete-request guardによりsemantic commit
を拒否されてはいなかった。実際のsecond causeはQt paint schedulingだった。tightなnative
wheel message列の中でcache hit 1、2を順にatomic commitすると、それぞれ`update()`は
queueされるがevent loopがpaintを処理する前にwidgetのcurrent imageが次へ置換される。
その直後のfinal cold requestがstageされるため、記録上は`[1, 2]`をcommitしていても
canvasへ到達したintermediateは`[]`となり、old pageがfinal completionまで残り得た。
obsolete result rejection、request identity、atomic commit自体は正しかった。

修正はschedulerとpresentationのauthorityを分離したまま行った。
`ViewerWindow._dispatch_pending_zip_runtime_request`はまず従来どおりfinal cold requestを
production runtimeへadmitする。その成功後だけ、同じbookの
`PresentationState.displayed`がdistinct final requestより前にlegitimately commit済みで、
widgetのatomic frame identityとも一致し、paint acknowledgementがまだpendingの場合に、
`ViewerWidget.paint_pending_committed_frame`でそのlatest ready frameを1回同期paintする。
このmethodは既存imagesを既存`paintEvent`へ通すだけで、decode、archive read、upload、
request、timer、frame queue、placeholderを作らない。final work submissionが常に先なので
intermediate presentationはfinal decode schedulingを遅らせない。表示可能なready frameが
なければold legitimate frameを維持し、fake progressionやcold transit decodeを行わない。

fresh offscreen integration fixtureの変更前/変更後sequenceは次のとおりだった。ここで
`commit`はsemantic atomic commit、`paint`は実際の`paintEvent`到達、`decode`はsource
decode startを表す。

- ready: input `1,2,3`、ready `{1,2}`、final 3 cold。変更前はintermediate
  `commit [1,2] / paint []`、変更後は`commit [1,2] / paint [2]`、その後final
  `commit/paint 3`。final 3はintermediate repaintより先にruntimeへadmitされる。
- cold: input `1,2,3`、ready `{}`。変更前/後ともtransit commitなし、page 2 decode
  なし、final 3 decode start。old page 0だけを維持しfake frameを出さない。
- mixed: input `1,2,3,4`、ready `{1,3}`。変更前はintermediate
  `commit [1,3] / paint []`、変更後は`commit [1,3] / paint [3]`。cold page 2は
  decodeせず、final 4をdecode/commit/paintする。

同一SHA-256のmaintained 13-page large-image fixture（4096 x 6500、1920 x 1053、
final 10、全transit cold）では、変更前の別runでrequest-to-paint 42.526 ms、変更後runで
36.993 ms、handler 2.378 ms、warm cache-hit control 1.745 msだった。変更後のpresented
sequenceはfinal `[10]`だけで、decode eventはcancelされたspeculative page 1の後にfinal
10がstart/completeした。ready transitがないcontrolなので差は短いoffscreen run間変動を
含み、改善量の主張には使わないが、ready-frame repaint追加によるcold final latency回帰が
ないことを確認した。focused ready/mixed fixtureはfinal request admissionをrepaintより前に
固定しており、presentationの追加がsingle decode laneのpriorityを変更しないことを
決定的に確認する。stale/obsolete frameは既存serial/book/source/unit guardsで拒否される。

このsectionのZipPlaFork由来behavior/process provenanceはrepository
`himamon/ZipPlaFork`、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、
`source/ZipPla/ViewerForm.cs`、class `ViewerForm`、methods/processes
`NextPage`、`moveToNextPage`、`movePageNatural`、`showCurrentPage`、
`bmwLoadEachPage_EachRunWorkerCompleted`、`SetBackgroundMode`、`priorityLevel`、
AGPL-3.0-or-laterである。license本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 27. Display-ready neighborhood value under combined cache pressure

2026-08-23に、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`をarchive lifetimeからpaintまで
一体のsystemとして再確認した。

### 27.1 ZipPlaFork integrated model

`source/ZipPla/PackedImageLoader.cs`、class `PackedImageLoader`では、fields
`stream`、`zipArchive`、`zipArchiveEntries`がbook lifetimeのarchive ownerである。
constructor `PackedImageLoader(..., PackedImageLoaderOnMemoryMode)`（267--313）は
default `None`で`File.OpenRead(path)`を保持し、`OnMemory`だけがwhole archiveを
`MemoryStream`へcopyする。`Releasable`はfile streamから始まり、明示`Release`で
memoryへ移す。`getZipArchiveEntries`（1143--1179）は保持した
`ZipArchive(stream, Read)`とentry arrayを作る。`OpenImageStream` /
`OpenInnerImageStream`（1693--1774）は選択entryだけを`entry.Open()`し、
`GetImageAndDisposeOriginal`でentry streamを必ずdisposeする。loader `Dispose`
（2473--2524）がarchiveとowner streamを閉じる。したがってdefault ZIP pathで
要求外payloadはinteractive decode critical pathへ入らない。

`source/ZipPla/ViewerForm.cs`、class `ViewerForm`では、book open時にpage-lengthの
`PreFilteredImageArray`、`OriginalImageInfoArray`、`ResizedSizeArray`、
`ResizedImageArray`と`BackgroundMultiWorker bmwLoadEachPage`を構成する。
`bmwLoadEachPage_DoWork`（2980以降）はsingle laneで選択entryをextract/decodeし、
pixel format conversionとpre-filterを`PreFilteredImageArray`へ置き、
`GetResizedSize`とscaling/filter processを経た`VirtualBitmapEx`を返す。
`bmwLoadEachPage_EachRunWorkerCompleted`（5103--5145）が
`SetNewResizedImage`を通じて`ResizedImageArray`へ入れた時点で、entry extraction、
decode、pre-filter、display-size resize/transformが完了しており、main canvasから
直ちに描けるdisplay-ready artifactになる。

`SetBackgroundMode` / `priorityLevel`（5271--5323）は全page workをcurrent window、
next window、previous window、distantのbandに分け、同bandではcurrentまたはtrackbar
pointerからの距離でsortし、`SetWorksOrder`後のworker countを1にする。completion時に
`currentPage`が変わっていれば同じorderを再構築する。このload orderは
`ReduceUsingMemory`のeviction orderでもある。

`SetNewResizedImage` / `ReduceUsingMemory`（5178--5247）はmemory upper boundを使うが、
`existingImageCount < MaxPageCountInWindow * 4`ならupper boundを越えてもnew resized
imageを受け入れ、reductionもそのcount floorより下へは落とさない。single-page modeなら
4 page images、two-page modeなら8 page imagesのfloorであり、commentどおりnavigation
判定とtrackbarのためのcurrent/near useful setである。これはgeneric LRUの個数上限では
なく、display-ready `ResizedImageArray`のminimum useful neighborhood contractである。
evictionは`WorksOrder`の末尾、すなわちcurrent-relative priorityが最低のimageから行い、
同じpageの`PreFilteredImageArray` sourceも一緒にdisposeして`ReworkOrder`へ戻す。

`NextPage`（1773--1817）はrequired resized size/artifactが未知ならadvanceを止める。
`movePageNatural`（8735--8813）はreadyなadvanceごとに`currentPage`を動かして
`showCurrentPage`を呼ぶ。`showCurrentPage`（6100--6145）はloading状態を含め
`pbPaintInvalidate`し、worker completionもnew artifactがcurrent windowへ入れば
`showCurrentPage(false)`する。従ってZipPlaForkのsystemは
`persistent entry access -> current-centered single worker -> display-ready resized array
-> count floor/current-relative eviction -> ready-only movement/invalidate`として連動する。

### 27.2 NivisViewer difference and root cause

NivisViewerは既にpersistent `ZipImageSource`、single `RasterBookRuntime` lane、
current-centered `RasterWarmupPlanner`、4 reading-direction + 1 reverseのurgent prefix、
book-wide continuation、direct-size JPEG preview decode、source/frame store分離、
combined byte admission、atomic `PresentationState` commitを持つ。warmupはfirst complete
commit直後に始まり、urgent prefixを終えるまではbroad remainderへ進まない。したがって
原因はstartup page countやwork iteratorの早過ぎるbook-wide expansionではなかった。

差はcombined cache value orderだった。変更前は同じlocalityのdecoded `QImage` sourceが
layout-ready `QPixmap` frameより高くrankされ、current/displayed/near sourceも保護された。
byte budgetがsource + frame pairを全urgent unit分保持できない場合、paint independentな
QPixmapを増やす前にadmissionが止まるか、pressure reductionでframeをsourceより先に
捨てた。このため「source-readyだが直ちにpaint不能」なworkへmemory valueを払い、
ready-intermediate presentationが利用できるframe runwayを小さくしていた。

同一120 x 180 PNG、100 x 100 display、single mode、current page 2のfocused fixtureでは、
decoded sourceがdisplay frameの2倍超だった。one source + four frame相当のbyte budgetで、
変更前はready `{2}`だけだった。同一条件の変更後観測ではready `{1,2,3}`となり、
より広いtwo-source + four-frame相当budgetでは少なくともcurrent-centered
`{1,2,3,4}`がreadyになり、decoded sourceは2以下、combined budget内だった。

### 27.3 Ported principle and modern adaptation

`app/zip_raster_book_runtime.py`の既存source/frame storesと唯一の
`RasterAdmissionPolicy`を維持したまま、次を変更した。

1. active layout内ではimmediately paintable frameをrehydratable sourceより高価値にする。
   frame同士は従来どおりcurrent-relative distance/direction、layout scope、current/displayed
   ownershipで順位付けする。
2. background frame admissionはpending frame rankを境界にし、そのunit自身が再利用する
   sourceを除き、既存navigation sourceをreclaim candidateにできる。successful QPixmap
   uploadまではreclaimをcommitしないので、cancel/stale/error resultは既存artifactを壊さない。
3. budgetが十分ならsource/frame separationとpreview reuseは従来どおり両方保持する。
   pressure時だけsourceを先に落とす。QPixmapはsourceから独立しているためpage turnは
   cache hitのままで、magnifier/manual source demandは既存
   `require_cached_current_source` hydration pathで必要な1 unitだけ再取得する。

これは`MaxPageCountInWindow * 4`を定数として移植していない。既存のcurrent-relative
topology/orderを使い、ready frameのbyte cost、resolved memory budget、page geometry、DPI、
layoutによって形成可能なrunwayが自動的に変わる。first page、first 10 input、固定startup
list、prefetch radius増加、parallel worker、second cache/presentation authorityはない。
new currentは毎navigationでplanとretention valueをrecenterし、cold rapid transitは従来どおり
decodeせず、final cold targetがunrelated warmupをcancelしてsole laneを先に得る。

### 27.4 Offscreen evidence

maintained benchmark schema 4へtest-only neighborhood snapshotと`artifactReady` sequenceを追加した。
13 page、4096 x 6500、1920 x 1053、final 10、512 MiBの3 runではfinal
request-to-paintが33.29--46.12 ms（median 43.775 ms）、以前の同fixture別runは
36.993 msだった。run variationが大きく速度差は主張しない。全runでfirst-visible時は
page 0だけready、rapid cold pages 2--9をdecodeせず、started page 1をcancelしてfinal 10を
先にdecodeした。warm-idle sequenceは`10,11,9,12`、ready setは`{0,9,10,11,12}`、
source-onlyは0だった。final-target-first contractは維持されている。

65 page、同画像geometry、16 MiB ignored archive padding（16,844,907-byte ZIP）、
128 MiB budget、final 32ではopen 44.204 ms、immediate final 50.795 ms、warm hit
2.194 msだった。first-visible `{0}`、target paint `{0,32}`から、current-centered
`32,33,31,34,35,36,30,...`の順でready artifactが形成され、idle時はpage 12--52の
41 display frames、source-only 0、116,528,680 bytes / 134,217,728-byte budgetだった。
single slow page-1 inputの別runはhandler 0.248 ms、commit 46.543 ms、その後
`1,2,0,3,...,12`のcurrent-relative orderで13 pagesがready、warm hit 2.019 msだった。
physical paddingは選択entry以外のinteractive readを増やさない。

このsectionのZipPlaFork由来principle/process provenanceはrepository
`himamon/ZipPlaFork`、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、AGPL-3.0-or-later、
`source/ZipPla/PackedImageLoader.cs` class `PackedImageLoader` constructor、
`getZipArchiveEntries`、`OpenImageStream`、`OpenInnerImageStream`、`Dispose`、および
`source/ZipPla/ViewerForm.cs` class `ViewerForm` fields `PreFilteredImageArray` /
`ResizedImageArray`、methods/processes `bmwLoadEachPage_DoWork`、
`bmwLoadEachPage_EachRunWorkerCompleted`、`SetNewResizedImage`、
`ReduceUsingMemory`、`SetBackgroundMode`、`priorityLevel`、`NextPage`、
`movePageNatural`、`showCurrentPage`である。license本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 28. Viewer mouse XButton book/file navigation（2026-08-23）

### 28.1 ZipPlaFork固定revisionの確認

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/ViewerForm.cs`では、`ViewerForm` constructorが`KeyboardShortcut`
の`UseX1Button` / `UseX2Button`を有効にし、既定shortcut table
`ViewerFormConfig.DefaultKeyboardShortcutCommands`がXButton1を`Command.OpenPrevious`、
XButton2を`Command.OpenNext`へ割り当てる。これらは`NextPage` / `PreviousPage`とは別commandで、
`getMouseGestureSettingTemplate`から`OpenPrevious` / `OpenNext`へdispatchされる。

`OpenNext` / `OpenPrevious`は`OpenNextOrPrevious(next, loop:false)`を呼ぶ。同methodは
`getCurrentParent` / `currentArchiveFilePath`から現在位置を得て、parentのfilesとfoldersを
列挙し、`requestedSortModeDetails`対応の`OpenNextOrPrevious_Comparer`でname、rating、time、
size、type等を比較・sortする。方向側のcandidateを順に`PackedImageLoader.HasAnyEntry`でprobeし、
最初に開けるpathを既存`OpenFile`へ渡す。通常のXButton操作はloopしない。candidateが無い場合は
`BookFolderIsNotFound`のinformation dialogを表示する。XButtonは`KeyboardShortcut` input ownerに
よってViewer image control上でcommandとして処理され、page moveやmouse gesture commandへ同時に
fall throughする構造ではない。

採用したreference behaviorは、XButton1/XButton2をprevious/next book commandとして独立所有し、
既存のsame-window open transitionへ渡す点である。ZipPlaForkのparent再列挙、再sort、candidate
archive probe、boundary dialogは採用していない。NivisViewerではBrowserのsearch AND rating filter、
stable sort、folders-firstを通過してViewerを開いた時点のimmutable visible-order snapshotの方が
強いauthorityだからである。

### 28.2 NivisViewer側の統合

`BrowserWindow.adjacent_book_snapshot`は、既存のsource/filter/stable-sort pipelineから得た
visible item orderと各itemの`openable_by_nivisviewer`を、sort/filter identityと共に
`AdjacentBookBrowserSnapshot`へ固定する。`_invoke_open_path_handler` ->
`ApplicationController._handle_browser_open_request` -> `ApplicationController.open_path` ->
`ViewerWindow.open_path`の既存Browser-to-Viewer handoffへ同snapshotを追加した。folder、Browser-only
OTHER、Viewer非対応itemはsnapshot内に残っても`AdjacentBookBrowserSnapshot.viewer_paths`ではeligible
targetにならない。image targetでは同snapshotから`FolderListingSnapshot`を再構成するため、folder
sourceのpage topologyにも同じvisible image orderとselected image identityが渡る。

`ApplicationController.open_adjacent_book`はsnapshotがある場合、`adjacent_viewer_path`で現在のrequested
identityの直前/直後を同期的に選ぶだけで、filesystem scan、Browser live model参照、再filter、再sort、
archive probeを行わない。既存`ViewerWindow.open_path` / `BookSession.open_book_async` / raster runtime replacement、
generation cancellation、atomic first-frame presentationをそのまま使い、同じViewer windowで遷移する。
XButtonのdefault previous/next-book dispatchはsnapshot必須として渡すため、snapshotが無い
direct/open-dialog起点では安全にno-opとなり、filesystem searchを開始しない。menu/keyboardやbook-end
auto moveなどXButton以外の既存adjacent-book commandは従来のbackground searchを維持する。
Viewerのbook-changed同期時も、Browserがcapture元parentを表示中ならsame-folder selectionだけ更新し、
Browserが別locationへ移動済みならそこをsnapshot元へ戻さないため、XButton操作からBrowser historyを
追加しない。

`ViewerWindow`はactive snapshot/current pathとは別に最新pending snapshot pathを1個だけ保持する。
rapid XButtonで前requestのopen完了前に次のinputが来ても、次candidateはpending identityから選ばれ、
`BookSession`のopen generationが古いsource/runtime completionを拒否する。成功時だけpending snapshotをactiveへ
promoteし、失敗時は既存bookのsnapshotを維持する。plain reloadはsnapshotを保持し、Viewer側で明示的に
topologyを変更するreloadは破棄する。

`ViewerWidget.mousePressEvent`はBackButton/ForwardButton pressを`extraMouseButtonPressed`へ1回だけemitして
acceptし、releaseもacceptする。したがってleft/right canvas click、middle magnifier、right gesture/context、
wheel/page signalへfall throughしない。default configの`mouse_back_button_action=previous_book`、
`mouse_forward_button_action=next_book`から既存Viewer command dispatcherへ接続される。boundaryではopenを開始せず、
既定のnon-modal Viewer statusだけを更新する。`loop_book_navigation`が明示的に有効な既存設定の場合だけ従来どおり
wrapを許す。

### 28.3 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedした箇所は`source/ZipPla/ViewerForm.cs` class `ViewerForm` constructorの
`KeyboardShortcut` X-button setup、enum/process `Command.OpenNext` / `Command.OpenPrevious`、
`getMouseGestureSettingTemplate` dispatch、methods `OpenNext`、`OpenPrevious`、
`OpenNextOrPrevious`、class `OpenNextOrPrevious_Comparer`、および
`ViewerFormConfig.DefaultKeyboardShortcutCommands`である。NivisViewer側の対応箇所は
`app/viewer_widget.py` `ViewerWidget.mousePressEvent` / `mouseReleaseEvent`、
`app/adjacent_book_search.py` `AdjacentBookBrowserSnapshot`、`app/browser_window.py`
`BrowserWindow.adjacent_book_snapshot` / `_invoke_open_path_handler`、`app/application_controller.py`
`ApplicationController.open_adjacent_book` / `_open_path_in_viewer`、`app/viewer_window.py`
`ViewerWindow.open_path` / browser navigation stateである。必要なlicense本文とcopyright noticeは
section 1記載の`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。


## 29. Browser viewport-centered thumbnail read-ahead（2026-08-24）

### 29.1 固定revisionのCatalog実装

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/CatalogForm.cs`では、旧来の`CatalogForm.SetBackgroundMode`が
`ThumbViewer.DisplayedStartShowIndex` / `DisplayedStopShowIndex`を先頭にし、表示範囲の外側を
近い側から交互に並べるworker orderを構築する。ただし同revisionの実際のfork動作では
`bmwMakePreview_DoWork`からサムネイル取得が削除され、`ThumbViewer.PaintPart`が描画中の範囲を
authorityとして`ThumbViewerItem.LoadAsync`を起動する。保持／load範囲は表示行に前後1行を足した
範囲だけで、それ以外の`ThumbViewerItem.Clear`が進行中loadをcancelしBitmapを破棄する。
`LoadAsync`はstatic `SemaphoreSlim(1, 1)`で同時生成を1件に制限し、path identityを再確認してから
resultをpublishする。`ThumbViewer.GetThumbnailCountInScreen`とCatalog側の呼び出しはthumbnail寸法、
padding、text行高、alignment、`tvCatalog.Size`から画面内個数を計算する。

同fileの`AllowReadAheadProcess` / `PrepareLookAheadProcess` / `StopLookAheadProcess`はCatalogの
スクロール先サムネイルqueueではない。これはhidden `-LookAheadMode` Viewer processをIPCで準備し、
選択archiveをViewerで開く前にwarm upする別process contractである。したがってNivisViewerへ
process modelやkill/wait動作は移植していない。採用したreference principleは、viewport geometryを
需要の境界にし、表示範囲が変わるたびに近傍だけを再設定し、遠いworkを保持しない点である。

### 29.2 変更前のNivisViewerとの差分

NivisViewerの既存`calculate_grid_visible_range`はQt grid寸法、viewport寸法、vertical offsetから
visible rowsを計算し、model row/pathだけを扱うためQWidgetを全件生成しない。既存plannerはvisible
item countの1 screen分を上下両側へ対称に足していたため最大`visible + 2 * visible`件だった。一方、
providerの`PREFETCH`はmemory/disk cache lookup後、folder/image/archive decodeをskipしていたので、
通常画像の次viewportはdisk cacheが既にある場合以外readyにならなかった。fast scroll時だけ古い
PREFETCHをcancelし、方向identityは保持していなかった。

### 29.3 採用したbounded directional policy

`build_thumbnail_request_plan`を既存scheduler authorityのまま次の順序へ変更した。

1. visible missing rows: `VISIBLE`、常に最高priority。
2. selected offscreen rows: `SELECTED`。既存selection contractを維持する。
3. recent scroll方向の直後1 viewport: `READ_AHEAD`。個数は現在のvisible row countと同数で、
   ordinary `BrowserItemKind.IMAGE`だけcache miss時のdecodeを許可する。
4. 逆方向の直近`ceil(visible_count * 0.25)`件: 既存`PREFETCH`。ordinary image、folder、archiveは
   memory/disk cache lookupだけ行い、cache missをdecodeしない。既存PDF prefetch contractは維持する。

従って通常時のspeculative rangeは最大`1.25 * visible_count`（端数切り上げ）、要求近傍全体は
selected offscreenを除き最大`2.25 * visible_count`である。directory総件数はrange計算に使わない。
初期方向は通常のforward/downwardとし、scroll valueの符号が変わればそのeventで方向を反転する。
fast scroll中はspeculative rowsを0にし、visibleを`VISIBLE`のまま要求する。idle timer後は最後に観測した
方向のbounded neighborhoodだけを再開し、その先へ歩かない。

`BrowserWindow._request_visible_thumbnails`は各planのpath集合を作り、既存
`BrowserThumbnailProvider`へ同じsize/generationのqueue recenterを依頼してからpriority順にrequestする。
新しいscheduler/thread pool/concurrencyは追加していない。queueに残っている旧visible/speculative workで
新plan外のものは`QThreadPool.tryTake`で除去する。既に実行中の最大1件（shared coordinator使用時も既存の
bounded browser worker数まで）は安価な置換よりfinishを優先し、queueを増殖させない。新visibleと同pathの
queued read-aheadは既存request promotion pathで`VISIBLE`へ上げる。

`READ_AHEAD`は既存memory cache、suitable-thumbnail reuse、disk cacheを先に通る。cache hitならdecodeしない。
cache miss decodeを許すのはordinary imageだけで、folder cover、ZIP/archive、PDF、video、shell/text previewは
cache-onlyで終える。folder/archiveがvisibleになった場合は従来どおり`VISIBLE`経路で生成する。
read-ahead image artifactは既存memory/disk cacheへ保存され、次viewportでmodel-compatible thumbnailとなる。
directory/spec変更時は既存`begin_generation`がpending workをcancelし、providerとBrowserWindowのgeneration
checkが旧resultのpublishを拒否する。

### 29.4 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたfile/class/method/processは`source/ZipPla/CatalogForm.cs` class
`CatalogForm`の`SetBackgroundMode`、`tvCatalog_DisplayedChanged`、`bmwMakePreview_DoWork`、
`AllowReadAheadProcess`、`PrepareLookAheadProcess`、`StopLookAheadProcess`、class `ThumbViewer`の
`OnPaint`、`PaintPart`、`GetThumbnailCountInScreen`、class `ThumbViewerItem`の`LoadAsync` / `Clear`である。
NivisViewer側の対応箇所は`app/browser_thumbnail_scheduler.py`の
`build_thumbnail_request_plan`、`app/browser_window.py`の`_request_visible_thumbnails` / `_on_list_scrolled`、
`app/thumbnail_provider.py`の`BrowserThumbnailProvider.request` / `_load_pipeline` /
`cancel_requests_except`、`app/image_work_coordinator.py`のBrowser priority mappingである。
必要なlicense本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 30. Browser `中` thumbnail preset and Catalog quality pipeline（2026-08-24）

### 30.1 固定revisionのTiny寸法authority

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/CatalogForm.Designer.cs`は`ThumbViewer.ThumbnailSize`のnormal初期値を
`210 x 297`にする。`source/ZipPla/CatalogForm.cs`の`CatalogForm.ChangeThumbnailSize` /
`ThumbnailSettingToCatalog`はTiny / Small / Normal / Large / Hugeを内部size
`-2 / -1 / 0 / 1 / 2`へ対応させ、config `CatalogFormConfig.ThumbnailSize`にはmenu内index
`0..4`を保存する。default config値はNormalの`2`である。

実寸は`ChangeThumbnailSize_NeutralLength = sqrt(210 * 297)`、default aspect
`1 / sqrt(2)`、`scale = 2^(size / 2)`から、
`neutral * scale * sqrt(aspect)`と`neutral * scale / sqrt(aspect)`を求め、
`source/ZipPla/Program.cs` `Program.DpiScalingX`がDPI倍率適用後に`Math.Round`する。
従ってTinyは100% DPIで厳密には`105 x 148` logical pxである（高さの中間値は
.NET既定のto-even roundingで148になる）。125 / 150 / 200%では同じ式から
`131 x 186`、`158 x 223`、`210 x 297` physical pxになる。これはthumbnail bitmap/contentの
寸法であり、class `ThumbViewer`の`updateRowsCols`は別途frame、padding、filename text heightを
grid cellへ足す。

NivisViewerでは既存`BrowserDisplayDensity` / `GRID_PROFILES` /
`GRID_PRESET_THUMBNAIL_SIZES` authorityへ`MEDIUM = "medium"`、表示名`中`、logical long edge
`149`を追加した。default `portrait_1_sqrt2`の`frame_size_from_long_edge`は`105 x 149`を返す。
既存`極小=96`、`コンパクト=128`、`標準=180`、`ゆったり=240`、`大=320`は変更せず、順序は
`極小 -> コンパクト -> 中 -> 標準 -> ゆったり -> 大`である。`中`はCompactと同じ
font、1行filename、margin、spacing profileを再利用するため、content以外の新しいlayout policyを
作っていない。placeholder canvas、fallback icon、associated icon、rating、filenameは同じdelegate
rect authorityから再計算される。

`ThumbnailRenderSpec`は新presetのlogical edge 149だけをcache request identityへ含める。
auto qualityとDPIの組合せによって149と180が同じphysical bucketへ量子化されてもgeneration/tokenは
別になり、旧149/180 requestのpublishを相互に受け付けない。一方`family_token`は従来どおりphysical
artifact互換性を表すため、既存disk/memory cacheの十分な解像度を再decodeなしで再利用できる。
149以外のcustom sizeは、同じphysical bucketならgenerationを再利用する既存contractを維持する。

### 30.2 ZipPlaFork Catalog thumbnail pipeline

固定revisionで確認した実経路は次の通りである。

1. `ThumbViewer.PaintPart`がvisible rowsと前後1行だけの`ThumbViewerItem.LoadAsync`を起動し、範囲外を
   `Clear`する。`LoadAsync`はstatic `SemaphoreSlim(1, 1)`で1件ずつ
   `CatalogForm.GetThumbnail`を呼び、path変更後の結果を破棄する。
2. `CatalogForm.GetThumbnail`はdirectory/archiveを`GetArchiveThumbnail`、通常画像を
   `GetImageThumbnail`、movie/otherを各loaderへ分岐する。
3. 通常画像の`GetImageThumbnail`は`GPSizeThumbnail.TryGet`を先に試す。miss時はfull decode可能形式を
   `ImageLoader.GetFullBitmap`、thumbnail-only形式を
   `ImageLoader.GetAtLeastThumbnailBitmap`で取得し、`GPSizeThumbnail.TrySet`を経由してから
   `DoJustClipping`で表示寸法へfit/cropする。
4. `GPSizeThumbnail`はcache画像を小さい順に保持し、`TryGet`がrequested width/heightへ
   `SIZE_MARGIN = sqrt(2)`を掛けた条件を最初に満たす、最小の十分なsourceを選ぶ。letterboxは少なくとも
   一辺、cropは両辺を満たすことを要求する。`TrySet` / `GetResizedInfo`はsource aspectとrequestから
   margin付きsize列を作り、`COMMON_RATIO=2`で大きい画像から順に
   `BitmapResizer.CreateNew`（WPF `TransformedBitmap`）へ縮小しJPEG XRで保存する。legacy ADS/cache
   format自体はNivisViewerへ移植しない。
5. archive/directoryの`GetArchiveThumbnail`はfilename `ZipPlaInfo.ThumbnailInfo`の明示cover pageと
   crop positionを最優先し、次に特殊entry `{ZipPlaCoverFile}.jpg`を使う。どちらもなければ
   `PackedImageLoader.GetPackedImageEntries`を順に評価する。current clipがPlaClipなら
   `GetImagePointAndEtc`がblank avoidance、page/spread/book-layout heuristicを使って代表entryと初期clipを
   選び、早期終了条件も持つ。単純な「自然順の最初の画像」ではない。
6. PlaClip/Smart Clipの焦点は`source/ZipPla/BitmapAnalyzer.cs` `BitmapAnalyzer.GetFocus`が求める。
   対象をlong edge最大128へ縮小してgrayscale化し、対角差分から得たgradient強度をgradient line上へ
   投票し、最大投票cellをfocus pointへ戻す。`GetArchiveThumbnail`と`DoJustClipping`はfocus中心のcropを
   source/allowed rectangle内へclampして一度表示bitmapへ描画する。focus座標だけの永続cacheはないが、
   結果source/cropped thumbnailは`GPSizeThumbnail`に保存されるのでcache hitでは再解析しない。
7. `DoJustClipping`はcache/source bitmapを最終`tvCatalog.ThumbnailSize`へ1回描画する。既に同寸法なら
   `needToResize=false`へ落とす。class `ThumbViewer.DrawItem`は完成済みbitmapを
   `drawImageUnscaled` / `Graphics.DrawImageUnscaled`でcanvasへ描き、最終paintで再filterしない。

### 30.3 現在のNivisViewer pipelineとの差分

NivisViewerは`BrowserWindow._request_visible_thumbnails`がviewport-derived planを既存
`BrowserThumbnailProvider`へ送り、provider generationとrender-spec tokenで旧結果を拒否する。
`BrowserThumbnailProvider._load_pipeline`はmemory/disk cache後にitem kindへ分岐する。通常画像はPillow、
ZIP/CBZはnatural-sortした最初のreadable image、external archiveとPDFは各image sourceの最初のimage/page、
folderは直下のnatural-sortした最初のreadable imageを使う。whole-folder generationや第2schedulerはない。

`render_pil_thumbnail`はEXIF transpose後、letterbox、center crop、または既存Smart Cropを適用し、Pillow
LANCZOSでphysical cache specへ1回縮小してQImage化する。既存`detect_smart_crop`は最大256 proxyでalpha/
corner-color blankを除外し、target aspectの21候補windowをedge-energyとcenter penaltyで比較する。
normalized cropはsource size/mtime/entry/ratio/version keyのbounded memory `SmartCropCache`へ保存され、完成
QImageは既存disk cacheへ保存される。このためNivisViewerは「常に幾何学的中央」ではないが、ZipPlaForkの
gradient-line focus、book/spread heuristic、representative-page scoringとは異なる。

`ThumbnailDiskCache.get_suitable`とprovider `_memory_candidate`は同じfamilyから、十分なものでは最小edge、
不足時は最大edgeをlow-resolution provisionalとして選ぶ。従ってZipPlaForkのclosest adequate source原則は
既に実現しており、small cacheの無条件upscaleをfinal resultにはしない。miss時decodeは現在のbucketだけを
作り、ZipPlaForkのmulti-resolution legacy containerは作らない。

通常のcache missはsourceからcache QImageへのPillow LANCZOSが1段、delegate
`BrowserItemDelegate._paint_thumbnail_image`のphysical cache QImageからDPI-snapped logical destinationへの
Qt `SmoothPixmapTransform`が1段で、計2段のresamplingである。disk/memory hitは後段だけである。delegateは
QIconへの事前縮小を挟まないが、同じitemをrepaintするたび後段filterを実行する点が、表示寸法bitmapを一度
作って`DrawImageUnscaled`するZipPlaForkと異なる。Browser display `center_crop`ではdelegate source rectも
計算するが、通常のgenerated imageはframe aspect済みなので追加のbitmap生成stageではない。

archiveの差は画質filter以上に大きい。NivisViewerはnatural first readableをcoverとするのに対し、ZipPlaForkは
明示metadata、special cover entry、layout/blank/scoreによる候補選択を行う。同じresize/crop品質でもpage選択の
差が「thumbnail quality」の差として見える場合がある。

### 30.4 Ranked future improvements

1. **Archive cover/page selection（expected benefit: high、cost: medium、complexity: medium）**
   既存ZipPla filename metadata parserを利用して明示cover pageを尊重し、その次にboundedなspecial-cover/
   first-few-candidate policyを既存provider内へ接続する案が最も見た目へ効く。cache key/entry pathへ選択結果を
   含め、visible/selected missだけで評価し、distant read-aheadでは既存cacheだけを見る。誤ったmetadata index、
   blank preface、two-page spread、encrypted/corrupt entryをfallback可能にする。次に実装する候補として推奨する。
2. **ZipPla-style focus-aware Smart Clip refinement（benefit: medium-high、cost: medium、complexity: medium-high）**
   新しいcrop systemを作らず、既存`detect_smart_crop`をversioned algorithmとしてgradient-line focusとbook/spread
   constraintsで強化する。最大128/256 proxyなら計算量はboundedだが、各edgeからline投票するZipPla方式は概ね
   proxy pixels × line lengthで、現在の21 window meanよりCPU/cache pressureが増える可能性がある。visible/
   selectedだけで解析し、normalized cropを既存SmartCropCacheとdisk artifactへ再利用する。顔のないtexture、漫画の
   枠線集中、文字密集、複数subject、意図的余白では誤focusしうるためcenter fallbackとfixture corpusが必要である。
3. **Display-ready memory surface（section 32で完了）**
   physical cache QImageをsource authorityのまま、source image/crop/physical target/DPR単位のbounded
   display surfaceへ一度準備し、repaintをunscaledにする方式を採用した。disk format/schedulerは増やさず、
   DPI/size/geometry変更でsurfaceだけ破棄する。実装、memory bound、計測結果はsection 32に記録する。
4. **Cache source-size selection tuning（benefit: low-medium、cost: low、complexity: low-medium）**
   現状は既にclosest adequateとlower provisionalを実装しており、ZipPlaForkとの差は小さい。改善するならaspect別の
   effective width/height判定とdecode proxy hintを追加し、edgeだけ十分でもcrop軸が不足する候補を避ける。既存family
   tokenとdisk rowsを使い、parallel cacheは不要である。
5. **Resampling/filter comparison（benefit: uncertain、cost: low for benchmark、complexity: low）**
   WPF `TransformedBitmap`/GDI+既定filterを盲目的に移植せず、同じfixtureをPillow LANCZOS、Qt smooth、single-stage
   display-ready pathで比較する。現在の主差はfilter名よりpage/focus選択とfinal repaint resamplingなので、filter交換
   単独の優先度は低い。

### 30.5 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially inspected/referencedしたauthorityは`source/ZipPla/CatalogForm.Designer.cs`
`tvCatalog.ThumbnailSize`、`source/ZipPla/CatalogForm.cs` class `CatalogForm` fields/methods
`aspectRatio`、`ChangeThumbnailSize_NeutralLength`、`ChangeThumbnailSize`、
`ThumbnailSettingToCatalog`、`GetThumbnail`、`GetImageThumbnail`、`GetArchiveThumbnail`、
`GetImagePointAndEtc`、`DoJustClipping`、class `CatalogFormConfig.ThumbnailSize`、class
`ThumbViewerItem.LoadAsync` / `Clear`、class `ThumbViewer.PaintPart` / `DrawItem` /
`drawImageUnscaled`、`source/ZipPla/Program.cs` `Program.DpiScalingX` /
`GetBlankAvoidRectangle`、`source/ZipPla/GPSizeThumbnail.cs` class `GPSizeThumbnail`
`TryGet` / `TrySet` / `GetResizedInfo`、`source/ZipPla/BitmapResizer.cs`
`BitmapResizer.CreateNew`、`source/ZipPla/BitmapAnalyzer.cs` `BitmapAnalyzer.GetFocus`、
`source/ZipPla/ImageLoader.cs` `ImageLoader.GetAtLeastThumbnailBitmap`である。

今回materially derivedしたbehaviorはTinyの210x297 neutral、size=-2、scale=0.5から新しい`中`の
105x149に近似する寸法選択で、NivisViewer側対応は`app/browser_sort.py`
`BrowserDisplayDensity.MEDIUM`、`app/browser_item_delegate.py` `GRID_PROFILES` /
`GRID_PRESET_THUMBNAIL_SIZES`、`app/config_manager.py`、`app/settings_dialog.py`の既存projection、
`app/thumbnail_render.py`の既存render/cache identityである。このsection 30のtaskではquality pipelineは監査と
提案だけだったが、後続のsection 32でfinal unscaled-paint processを採用した。ZipPlaForkのSmart Clip、cache
format、archive scoring implementationは移植していない。必要なlicense本文と
copyright noticeはsection 1記載の`licenses/ZipPlaFork/AGPL.txt` /
`licenses/ZipPlaFork/About.txt`に保持している。

## 31. Browser mouse-wheel scroll amount（2026-08-24）

### 31.1 固定revisionのCatalog wheel実装

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/CatalogForm.cs`では、thumbnail Browserはclass `ThumbViewer :
ScrollableControl`がwheelを所有し、`OnMouseWheel`でbase implementationを呼ばずに
`MouseWheelScrollAmount` / `MouseWheelScrollUnit`を適用する。既定値は
`120` / `ThumbViewerScrollUnit.AbsolutePixel`で、`scrollByAbsolutePixel`は
`-e.Delta * amount / 120`を現在のvertical positionへ加える。preset menu handlerは
30 / 60 / 120 / 240 / 480 logical pixelsをTiny / Small / Normal / Large / Hugeとして
設定する。加速、viewport-height比、OSのwheel-lines設定はthumbnail側では使用しない。

同じmenuには1 / 2 / 3 lineの`GridCeiling` presetもある。
`scrollByFloorGrid`は`gridSizeV`へalignしながら指定行数を動かすため、thumbnail寸法、
padding、text行数に追随する。ただし`getScrollCount`は非zeroのpartial deltaを120単位へ
切り上げるので、高解像度wheel/touchpadを小刻みに保つ方式ではない。
`preRenderScroll`は既存canvasの再利用と露出領域の再paintを行い、wheel量そのものから
thumbnail load範囲を広げない。load authorityはsection 29記載の`PaintPart`であり、
表示範囲と前後1行に限定される。file-list側の`DgvFileList_MouseWheel`は別実装で
`SystemInformation.MouseWheelScrollLines`を使うが、thumbnail `ThumbViewer`のpolicyではない。

設定は`CatalogForm.setMouseWheelScrollAmount`から`ThumbViewer`へ反映され、
`CatalogFormConfig.ThumbnailMouseWheelScrollAmount` / `ThumbnailMouseWheelScrollUnit`へ保存される。
profileにも同じnullable fieldsがあり、profile指定時は同じsetterへ戻す。固定revisionには
system/default pass-throughや任意数値の設定UIはない。

### 31.2 NivisViewerで採用したmodelと差分

変更前のNivisViewerは`ExplorerListView`に`wheelEvent` overrideがなく、
`BrowserWindow._build_ui`が`QListView.ScrollPerPixel`を指定するだけだった。したがって
angle/pixel delta、OS wheel lines、scrollbar single stepはQt/styleの既定処理が所有し、
page stepはviewport由来だった。Browser grid heightは`BrowserItemDelegate.grid_metrics.grid_size`
からthumbnail size、frame ratio、density、spacing、filename行数を含めて求められる。

既定の`System / Default`は現在のQt handlerへそのまま委譲し、変更前の挙動を保持する。
custom policyはZipPlaForkのadaptive `GridCeiling` principleを採用し、標準angle delta 120あたり
Small=1行、Medium=2行、Large=3行、Custom=1--12行を、現在のlogical grid heightでpixel移動へ
変換する。従ってthumbnail size、DPI logical scaling、text/grid geometryが変われば自然に移動量も
追随する。ZipPlaForkと異なりrow boundaryへ強制alignせず、angle deltaを120で比例配分し、
subpixel remainderを次eventへ保持する。`pixelDelta`があるtouchpad eventとShift-wheelはQtへ委譲し、
smooth/native inputをcoarse row jumpへ変換しない。mode/custom値の変更時だけremainderをresetする。

設定authorityは既存`ConfigManager` / `SettingsDialog`で、keysは
`browser_wheel_scroll_mode`と`browser_wheel_scroll_custom_rows`である。modeは
`system/small/medium/large/custom`以外をsystemへ戻し、Customは1--12行へclampする。
open Browserは`ConfigManager.settings_changed`から同じ`ExplorerListView`へ即時反映するだけで、
rescan、thumbnail generation変更、decode request、toolbar controlを追加しない。Viewerの
`ViewerWidget.wheelEvent`とpage navigationは変更していない。

wheel distanceとthumbnail speculationは独立している。scroll後の既存
`BrowserWindow._on_list_scrolled`は実scrollbar valueからdirection/fast stateだけを観測し、
`build_thumbnail_request_plan`は引き続き現在のvisible rangeから最大1 viewport directional +
25% reverse safetyを計算する。configured rowsをplanner inputにしておらず、大きいCustom値でも
1 eventの移動先が変わるだけでspeculative neighborhoodはsection 29のviewport boundを越えない。

### 31.3 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたfile/class/method/processは`source/ZipPla/CatalogForm.cs` class
`ThumbViewer` fields `MouseWheelScrollAmount` / `MouseWheelScrollUnit`、methods
`OnMouseWheel`、`scrollByAbsolutePixel`、`scrollByFloorGrid`、`getScrollCount`、
`preRenderScroll`、enum `ThumbViewerScrollUnit`、class `CatalogForm` methods
`setMouseWheelScrollAmount`とTiny/Small/Normal/Large/Huge/1--3 line menu click handlers、
class `CatalogFormConfig` fields `ThumbnailMouseWheelScrollAmount` /
`ThumbnailMouseWheelScrollUnit`である。NivisViewer側の対応箇所は
`app/browser_wheel_scroll.py`、`app/explorer_list_view.py` `ExplorerListView.wheelEvent` /
`set_wheel_scroll_policy`、`app/browser_window.py` Browser settings projection、
`app/config_manager.py`、`app/settings_dialog.py`である。必要なlicense本文とcopyright noticeは
section 1記載の`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 32. Browser display-ready thumbnail surfaces（2026-08-24）

### 32.1 固定revisionで確認したresampling / final-paint process

参照元は`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
`source/ZipPla/GPSizeThumbnail.cs`の`SIZE_MARGIN`はLanczos3の再現余裕として`sqrt(2)`、
`COMMON_RATIO`は2である。`TryGet`はletterboxならmargin付きrequired width/heightの少なくとも一辺、
cropなら両辺を満たす最初のcache bitmapを読む。`TrySet` / `GetResizedInfo`はsource aspectから
margin付きsize列を作り、`BitmapResizer.CreateNew`で大きいものから順に縮小する。

`source/ZipPla/CatalogForm.cs`の`GetImageThumbnail`、`GetArchiveThumbnail`等はそのsource/cache bitmapを
`DoJustClipping`へ渡す。`DoJustClipping`は常に最終`tvCatalog.ThumbnailSize`のbitmapを作り、sourceが既に
同寸法なら`needToResize=false`へ切り替えてunscaled copyする。それ以外はfit/cropをこの段階で一度だけ
最終canvasへ解決する。class `ThumbViewerItem.LoadAsync`が完成bitmapを所有し、class `ThumbViewer.DrawItem`は
`drawImageUnscaled` / `Graphics.DrawImageUnscaled`でitem canvasへ転送し、item repaintごとのfilterを行わない。
partial repaintでも`DrawItem_slicedCanvas`へunscaled copyする。legacy ADS/JPEG-XR container、WPF bitmap cache、
GDI canvasはNivisViewerへ移植していない。

### 32.2 変更前のNivisViewer実経路

通常画像、folder preview、ZIP/CBZ cover、external archive coverは最終的に既存
`BrowserThumbnailProvider._render_image`から`render_pil_thumbnail`へ入る。decoded original dimensionsは
Pillow `Image.size`そのもので、folderは直下のnatural-first readable image、archiveは現在のrepresentative
entryのdimensionである。EXIF transpose後、letterboxは全source、center/smart cropはnormalized cropから
丸めたsource rectangleを使う。Smart Cropだけは表示pixelとは別に最大256x256のBILINEAR proxyを解析するが、
proxy pixelをthumbnailへ流用しない。表示pixelはcrop/fit sourceからphysical cache specへPillow LANCZOSで
1回縮小され、QImage/disk WebP-or-PNG artifactになる。disk encode/decodeはresamplingではない。

`ThumbnailDiskCache.get_suitable`とprovider `_memory_candidate`は同familyで十分な候補の最小long edgeを選び、
不足候補はprovisionalだけにするため、closer adequate sourceがあるのに大き過ぎる／小さ過ぎるartifactを選ぶ
具体的な不具合は見つからなかった。このpolicyは変更していない。

変更前のdelegateはcache missでもmemory/disk hitでも毎回
`QPainter.SmoothPixmapTransform=True`として`drawImage(target, image, source)`を実行した。quality margin、bucket
quantization、shared content insetのためcache physical pixelsとdestination physical pixelsは通常一致しない。
default portrait frame、fit、auto qualityでの実測例は次の通りで、各`target`はDPR適用後のphysical pixelsである。

| preset / logical frame | DPR | cache QImage | final target |
|---|---:|---:|---:|
| 極小 / 68x96 | 1.00 / 1.25 / 1.50 / 2.00 | 113x160 / 136x192 / 181x256 / 226x320 | 60x84 / 74x104 / 90x128 / 120x170 |
| コンパクト / 91x128 | 1.00 / 1.25 / 1.50 / 2.00 | 136x192 / 181x256 / 226x320 / 272x384 | 83x118 / 103x146 / 125x176 / 166x234 |
| 中 / 105x149 | 1.00 / 1.25 / 1.50 / 2.00 | 181x256 / 226x320 / 226x320 / 362x512 | 97x137 / 121x172 / 145x206 / 194x274 |
| 標準 / 127x180 | 1.00 / 1.25 / 1.50 / 2.00 | 181x256 / 226x320 / 272x384 / 362x512 | 119x168 / 149x211 / 179x252 / 238x336 |

従ってnormal cache missはPillow LANCZOS 1段 + repaintごとのQt smooth 1段、cache hitはrepaintごとの
Qt smooth 1段だった。folder previewとarchive coverも同じである。previewless folder、broken/unreadable archiveは
QImageを持たず、shared placeholder canvasとfallback iconを描くため、このnormal-raster resampling経路には入らない。

### 32.3 採用したNivisViewer process

既存disk/family cacheをsource authorityのまま維持し、`BrowserItemDelegate`に
`BrowserDisplaySurfaceCache`を追加した。実際にpaintされたthumbnailだけについて既存
`thumbnail_image_rects`のDPI-snapped target/sourceを使い、targetのphysical width/heightと同寸法の
ARGB QImageへQt smoothで一度準備する。source rectangleが既にそのphysical surfaceと厳密に同寸法ならfilterせず
copy/shareする。surfaceにはwindow DPRを設定し、最終paintは
`QPainter.drawImage(target.topLeft(), surface.image)`のpoint overloadだけを使うためpixel scalingを行わない。

cache keyはsource QImage `cacheKey`、source crop、target physical size、DPRであり、最大96件かつ32 MiBのLRUである。
DPR変更時とthumbnail size/frame ratio/display mode変更時はsurfaceだけをclearする。disk cache、provider memory cache、
request generation、family token、worker数、Smart Crop、representative archive pageは変更しない。display surfaceは
delegate paintからしか作られないのでread-ahead itemやwhole folderを変換せず、visible paintが常に起点である。

変更後のnormal cache missはPillow LANCZOS 1段 + 最初のvisible paint時のQt smooth display preparation 1段 +
unscaled final paint、memory/disk hitは最初のvisible paint時のQt smooth preparation 1段 + unscaled final paintである。
同一source/geometryのwarm repaintはcache hit + unscaled final paintだけでresampling 0段になる。これは初回の
stage数を人工的に減らす変更ではなく、確認された「同じcache bitmapをrepaintごとに再filterする」冗長性を除く。

fine line、small text、photographic detail、high-contrast edge、portrait illustration fixtureで旧single paintと
新しいprepare+unscaled paintを同一targetで比較し、channel差最大1以内を確認した。medium 149、DPR 1.25、
source 226x320、target 121x172のoffscreen 1,000 warm paintsでは旧25.340 ms、新8.439 ms（3.00x）だった。
これはsynthetic timingであり画質向上の根拠ではない。根拠はfinal delegate paintのpixel scalingが0になったことと、
同じphysical output geometryをfixtureで維持したことである。memory tradeoffはvisible/recent surface最大32 MiBである。

### 32.4 Provenance

materially referencedしたfile/class/method/processは`source/ZipPla/GPSizeThumbnail.cs` class
`GPSizeThumbnail` constants `SIZE_MARGIN` / `COMMON_RATIO`、methods `TryGet` / `TrySet` /
`GetResizedInfo`、`source/ZipPla/BitmapResizer.cs` `BitmapResizer.CreateNew`、
`source/ZipPla/CatalogForm.cs` class `CatalogForm` methods `GetThumbnail` / `GetImageThumbnail` /
`GetArchiveThumbnail` / `DoJustClipping`、class `ThumbViewerItem.LoadAsync`、class `ThumbViewer.DrawItem` /
`drawImageUnscaled`である。materially derivedしたprocessは「十分なcache sourceを表示寸法へ一度準備し、完成pixelを
final paintでunscaled転送する」構造である。NivisViewer側の対応は`app/browser_item_delegate.py`
`prepare_display_thumbnail_surface` / `BrowserDisplaySurfaceCache` /
`BrowserItemDelegate._paint_thumbnail_image`である。必要なlicense本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 33. Browser item page-count metadata（2026-08-24）

### 33.1 固定revisionで確認したCatalog flow

固定revisionの`source/ZipPla/CatalogForm.cs` class `CatalogForm`はCatalog行と同じindexの
`int[] FileCountArray`を持つ（field付近`:7153`）。`MetaPageCount_NotLoaded = -1`、
`MetaPageCount_NotBook = -2`でunknown bookと非bookを分離する。directory準備の
`ShouldBeSkipForFile`はarchiveを`MetaPageCount_NotLoaded`として分類し（`:7686-7707`）、
`getDirectoryItemsInfo`はその初期値をfile行へ入れ、folder行を`-1`で初期化する
（`:7783-7950`）。従ってstatus-bar selection changeからarchive/folderを同期列挙しない。

count取得authorityは`getFileCount`（`:4661-4688`）である。まず
`GPSizeThumbnail.TryGet(thumbnailCache, path, getArchiveIndexWithAIAInfo(path), out data)`を試し、
`dataToFileCount`でcacheの付加dataを読む。cache missかつ
`ArchivesInArchiveMode.Ignore`の場合だけ`PackedImageLoader.GetPackedImageEntries().Count`を取得する。
`GetThumbnail`（`:4863-5008`）のdirectory/archive分岐はfull loadなら
`GetArchiveThumbnail`からcoverとcountを同じ処理で返し、header-only分岐なら`getFileCount`を使う。
`GetArchiveThumbnail`のcache writeは`GPSizeThumbnail.TrySet`へ
`fileCountToData(filecount, isZipPlaImageData)`を渡す（`:6154-6166`）。serializerはASCII
`BOOK`、`Int32 fileCount`、`Boolean zipPlaImageData`、readerは先頭`BOOK`を検証してcountを読む
（`:5100-5128`）。つまりpage countはthumbnail/cache metadataであってstatus-bar独自cacheではない。

countが配列へpublishされる経路ではpath/indexの再確認後に`FileCountArray[index]`を更新する。
legacy completion path `bmwMakePreview_EachRunWorkerCompleted`はwork-set GUID、index bounds、
`ResultTuple.Item1 == ZipPathArray[index]`、maskを検査してから`ResultTuple.Item4`を代入し、選択中なら
`ShowStatusBar()`を再実行する（`:9024-9155`）。現行snapshotの個別reload path
`ReloadOneThumbnailForSubThread`も`startingGuid == loadingGuid`、path/indexを再検査し、
`GetThumbnail`のout countを`FileCountArray[index]`へ代入後、選択中なら`ShowStatusBar()`を呼ぶ
（`:18573-18777`）。`ShowStatusBar`はsingle selected directory/archiveについて
`FileCountArray[selectedIndex]`を読むだけで、負値は`?`として表示する（`:18835-18885`）。

固定snapshot固有の注意として、visible `ThumbViewerItem.LoadAsync`（`:30231-30303`）は
`GetThumbnail`のcount out値を破棄し、画像だけをpublishする。そのため通常の初回visible-loadの全経路が
常にcount配列を埋める、という実装ではない。一方、既知値の行保持、BOOK cache metadata、header/reloadの
count publication、unknown表示、statusのpassive readという設計原則は上記のとおり確認できる。
NivisViewerへはこの原則を移し、WinForms配列、ADS/cache binary、GDI thumbnail、static semaphoreは移植しない。

### 33.2 NivisViewer対応

canonical metadataは既存`BrowserItem` / `BrowserScanEntry`のnullable `page_count`である。
`BrowserItemModel`は同じpath identity上で値をpublishし、refresh時はkind、size、mtime fingerprintが一致する
場合だけ既知値を引き継ぐ。rating renameは既存immutable itemの`replace`なので同じfieldを自然に保持する。

既存`BrowserThumbnailProvider`のfolder cover listingは`os.scandir`で直下だけを列挙し、production
`FolderImageSource(recursive=False)`と同じsupported image extension / file semanticsで候補数を返す。
ZIP/CBZ cover listingは`ZipFile.infolist()`の非directoryかつsupported image entryだけを数える。
どちらもcover生成時に既に作るcandidate listの長さを`ThumbnailLoadResult.page_count`としてpublishするため、
追加列挙はない。countだけが不足する場合も、新scanner/thread/storeを作らず、同じproviderのbounded worker、
generation、cancellation、folder/ZIP listing helperを使い、`BROWSER_PREFETCH` priorityでheader/list metadataだけを
取得する。画像entryのopen、展開、pixel decode、recursive traversalは行わない。

memory reuseはthumbnail LRUの同じcache keyへcountを添え、eviction/clearもthumbnailと同時に行う。
disk reuseは既存SQLite `entries`へnullable `page_count`列を後方互換`ALTER TABLE`で追加した。
lookupはsource path/kind/size/mtime/formatを検証し、QImage fileを読まない。生成済みcountは同じvalid sourceの
thumbnail variantsへattachする。独立した永続DBやunbounded path mapはない。

section 33実装時のsingle selected folder/archive infoは`サイズ: …    ページ: …`を一つのlabelへ表示した。
unknownは`—`で、label更新は既知item metadataのreadだけである。当初の150 ms metadata fallbackは30 msの
visible-thumbnail planを先行させ、同じproviderの最低priorityへqueueした。通常thumbnailがcountを返した場合、
未開始fallbackはcancelされた。このslot geometryとpriorityはsection 34で後続改善している。
provider generationとcurrent selected pathの双方を検査するため、Aを選択後Bへ移った時にAの完了値がBのlabelを
上書きしない。Aのmodel itemが同じgeneration内に残る場合はAへ安全に保存され、再選択時に即時再利用される。

### 33.3 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたfile/class/method/processは`source/ZipPla/CatalogForm.cs` class
`CatalogForm` fields `FileCountArray` / `MetaPageCount_NotLoaded` / `MetaPageCount_NotBook`、methods
`ShouldBeSkipForFile`、`getDirectoryItemsInfo`、`getFileCount`、`GetHeader`、`GetThumbnail`、
`GetArchiveThumbnail`、`dataToFileCount`、`dataToBookData`、`fileCountToData`、
`bmwMakePreview_EachRunWorkerCompleted`、`ReloadOneThumbnailForSubThread`、`ShowStatusBar`、class
`ThumbViewerItem.LoadAsync`である。materially derivedしたprocessは「per-item unknown/known countをthumbnail/header
metadataと同じlifecycleでpublish/cacheし、statusはその値をpassively読む」構造である。NivisViewer側の対応は
`app/browser_model.py`、`app/browser_scanner.py`、`app/thumbnail_provider.py`、
`app/thumbnail_disk_cache.py`、`app/browser_window.py`である。必要なlicense本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 34. Browser compact chrome / stable status metadata（2026-08-24）

### 34.1 固定revisionのCatalog geometryとstatus slot

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/CatalogForm.Designer.cs`では、Catalog `menuStrip`は24 logical px、
Back / Forward buttonは各24x22、`cbLocation` / `cbSortBy` / `cbFilter`は各20 px高で、
main Catalog contentは概ねy=50から始まる。`statusStrip`は22 px高で、permanent metadataは
`itemsCountToolStripStatusLabel` 90 px、`fileSizeToolStripStatusLabel` 70 px、
`selectedFileContainsCountToolStripStatusLabel` 90 pxの`AutoSize=false` slotである。後二者は
right alignされ、値の桁数が変わっても隣接slotの開始位置を動かさない。

`source/ZipPla/CatalogForm.cs` class `CatalogForm.ShowStatusBar`はsingle selectionについて、
sizeを`fileSizeToolStripStatusLabel`へ、もう一つのmetadataを
`selectedFileContainsCountToolStripStatusLabel`へ投影する。通常画像なら`ImageInfoArray`の
width x height、directory/archiveなら`FileCountArray`のpage countで、両方を同時には表示しない。
unknownは既知になるまで`?`であり、このmethod自身はarchive/folderの同期列挙やpixel decodeをしない。
sort modeやthumbnail presetもこのright-side metadata slotへ常駐表示しない。

### 34.2 NivisViewerのcompact chromeとstatus projection

変更前の100% offscreen実測はmenu 22 px + navigation toolbar 33 px = upper chrome 55 px、
Back / Forwardは32x31、toolbar iconは24x24、location/sort/search outer rowは31 px高だった。
変更後はfontを維持したままtop-level `QMenuBar::item`だけを上下1 px paddingへし、popup `QMenu`へは
styleを適用していない。navigation toolbarは28 px、outer marginはleft/right 1、top/bottom 0、
buttonは26x26、iconは20x20、location/sort/search shellは24 px高である。100%ではmenu 22 + toolbar 28 =
50 pxとなり、main contentの開始位置はy=50である。値はQt logical pxなので125/150/200%でも同じlogical
geometryを保ち、physical hit areaだけがDPIに従って拡大する。

bottom messageからsort/order（例`更新日時・降順`）とdisplay density（例`表示：中`）を除去した。
左側はcurrent folder、item count、single/multiple selectionという閲覧中に有用な情報だけを保持する。
右側は既存selection metadata projectionを二つのfixed-width `QLabel`へ分離した。size slotはcurrent
font metricsの`サイズ: 999.9 GB`、secondary slotは`ページ: 999999`と`99999 × 99999`の大きい方に
左右余白を加えて構築し、14 logical pxの明示的spacingを置く。両slotはright alignされる。imageはsecondaryに
resolution、folder/archiveは同じsecondaryにpage countを表示し、irrelevantな値は併記しない。

### 34.3 Page-count readinessとbounded priority

変更前はvisible thumbnail requestを30 ms後に計画した後、selection fallbackを150 ms待ち、さらに既存workerへ
`ThumbnailPriority.PREFETCH`として投入していた。このためcache missのselected metadataがvisibleだけでなく
ordinary read-ahead/safety workの後ろに滞留し、正しい`—`表示が体感上長く残った。

canonical authorityはsection 33の`BrowserItem.page_count`、provider thumbnail LRU metadata、既存SQLite
`entries.page_count`のままである。folder previewの既存direct-child candidate listing、ZIP/CBZの既存central-directory
image-entry listing、external archiveの既存header listingは、candidate countが確定した時点でworker signalをpublishする。
このsignalは最初のcover pixelをopen/decode/renderする前に発生する。同じlistingをcountのために繰り返さず、完成thumbnail
にも従来どおりcountを添えてmemory/SQLiteへ保存する。既存disk thumbnail metadata hitもworker上でcountをpublishする。

selection fallbackはvisible planに先行機会を与える60 ms後に動き、同じprovider/coordinator laneの
`ThumbnailPriority.SELECTED`を使う。priority orderingは`VISIBLE > SELECTED > READ_AHEAD > PREFETCH`なので、
selected unknown metadataはspeculative workを追い越すが、queued visible thumbnailを追い越さない。visible listingが先に
countを発見すれば同pathの未完fallbackをcancelする。folder fallbackは直下だけ、archive fallbackはheaderだけで、image
payloadをopen/decodeしない。scanner、worker pool、recursive traversal、独立map/databaseは追加していない。

workset boundも既存のままである。archive countのpiggybackは通常のvisible/selected thumbnail workに入ったitemだけ、
folder countのpiggybackはfolder previewが実際に行ったlistingだけである。`READ_AHEAD`/`PREFETCH`のfolder/archive decode skip
を維持するため、巨大directoryの全folder/archiveをcount目的で先回り列挙しない。unknown selected itemだけが既存metadata
request authorityを使う。provider generationとselected path identityの双方を引き続き検査し、stale completionは新しい
selection slotを更新しない。

### 34.4 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたfile/class/method/processは`source/ZipPla/CatalogForm.Designer.cs`の
`menuStrip`、`btnGoToBack`、`btnGoToForward`、`cbLocation`、`cbSortBy`、`cbFilter`、`statusStrip`、
`itemsCountToolStripStatusLabel`、`fileSizeToolStripStatusLabel`、
`selectedFileContainsCountToolStripStatusLabel` geometry、`source/ZipPla/CatalogForm.cs` class
`CatalogForm.ShowStatusBar`、field `FileCountArray` / `ImageInfoArray`、metadata completion/update process
`bmwMakePreview_EachRunWorkerCompleted` / `ReloadOneThumbnailForSubThread`である。materially derivedしたbehaviorは
compact two-row Catalog density、fixed right-aligned size/secondary slots、known per-item metadataのpassive status read、
metadata completion時のselected status refreshである。NivisViewer側対応は`app/browser_window.py`、
`app/thumbnail_provider.py`、既存`app/browser_model.py` / `app/thumbnail_disk_cache.py` authorityである。WinForms control、
`FileCountArray`、cache binary serializerは移植していない。必要なlicense本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 35. Browser chrome distribution / immediate selected metadata（2026-08-25）

### 35.1 Follow-up前後の実測geometry

section 34後の100% offscreen geometryはupper chrome 50 logical pxだったが、内訳には偏りがあった。
menu barは22 px、先頭menu action（`ファイル`）はy=0、高さ14 pxで、action bottomからmenu bar bottomまで
8 pxの空きがあった。menu bar bottomとnavigation toolbar topの間隔自体は0 pxだった。navigation toolbarは
28 px、layout marginsはleft/right 1、top/bottom 0で、location shellはy=2、高さ24、native edit fieldは
y=2、高さ20だった。toolbar content layoutとbreadcrumb layoutのvertical contentsMarginsはいずれも0である。
従って主因はinter-row spacerではなく、22 px menu rowを決めるrating controlと14 px menu actionの高さ不一致だった。

follow-up後はfontを変えず、rating quick-filterのvertical size hintをfont height + 8（この環境で20 px）へし、
top-level menu itemを上下4 px paddingにした。menu barとmenu actionはともに20 pxで、action bottom gapは0 pxである。
popup `QMenu`にはstyleを適用していない。navigation toolbarは27 pxで、26x26 buttonはy=1から完全に収まり、
location shellはy=1、高さ24、native edit fieldは引き続き20 pxである。location/breadcrumbのvertical layout marginは
0のままなので、native combo frameの上下2 pxを無理に削らなかった。main content開始位置はy=47である。
同じlogical geometryを100/125/150/200% DPIで検査し、font、button icon、edit field、rating starsのclippingがないことを
確認する。

### 35.2 Compact status text

right-side statusは既存の二slot authorityを維持し、sizeから`サイズ:` prefixを除いた。ordinary imageは
`4.8 MB` / `1920 × 1080`、folder/archiveは`123.4 MB` / `42 ページ`というprojectionである。unknown page countは
`— ページ`を直ちに表示する。size slotはcurrent font metricsの`999.9 GB`、secondary slotは
`999999 ページ`と`99999 × 99999`の大きい方に各6 px余裕を足し、14 logical pxのspacingを置く。
100% offscreenのcurrent fontではsize 102 px、secondary 162 pxだった。両方をfixed width/right alignedにし、
桁数やimage/folder/archive種別の変更でslot位置を動かさない。sort/orderとdisplay-density textは戻していない。

### 35.3 Timer-free latest-wins metadata scheduling

section 34時点のselected unknown pathには固定60 ms single-shot timerが残っていた。これを削除し、selection changeで
unknown folder/archiveを認識した同じcall stackから、既存`BrowserThumbnailProvider.request_page_count`へ
`ThumbnailPriority.SELECTED`で直ちにsubmit/upgradeする。known `BrowserItem.page_count`はworkerを起こさず同期表示する。

providerは同path/current generationのnormal thumbnail workerが既に`VISIBLE`/`SELECTED`なら、そのworkerの既存
folder/header listingをadoptする。まだqueuedの`READ_AHEAD`/`PREFETCH`なら同じworkerを`SELECTED`へpromotionし、別の
metadata workerを追加しない。低priority workerが既に走っていてpromotion不能な場合だけlightweight count requestを
使用する。逆に`VISIBLE` thumbnail requestが到着した場合はqueued selected-count workをcancelし、visible workerの
listing-before-decode publicationへ譲る。priority orderingは引き続き
`VISIBLE > SELECTED > READ_AHEAD > PREFETCH`で、新poolはない。

selection A -> B -> Cでは同generationのselected-count requestをpath identityでreplaceする。queued old workerは
`tryTake`して除去し、running old workerは既存cancel tokenをsetする。folder `os.scandir`とZIP central-directory iterationは
entryごとにcancelを確認する。cancel済みselected workerのfinished signalは`page_count_ready`をemitしないため、raceで
listingが完了してもstale metadataをpublishしない。normal visible metadataは従来どおりmodelへcacheできるが、status slotは
current selected path identityを再検査する。

count-only ZIP workはcentral-directory/headerのeligible image entryだけ、folder workはdirect-child eligible image fileだけを
列挙し、recursive traversalとpixel decodeを行わない。folder/archiveのnormal visible thumbnail listingからのearly
page-count callbackも維持する。scanner、bulk pre-count、独立map/database、worker poolは追加していない。

### 35.4 ZipPlaFork provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたauthorityは`source/ZipPla/CatalogForm.Designer.cs`の`menuStrip`、
`btnGoToBack` / `btnGoToForward`、`cbLocation`、`statusStrip`、`fileSizeToolStripStatusLabel`、
`selectedFileContainsCountToolStripStatusLabel` geometry、`source/ZipPla/CatalogForm.cs` class `CatalogForm`
field `FileCountArray`、methods `ShowStatusBar`、`getFileCount`、`GetThumbnail`、`GetArchiveThumbnail`、
`dataToFileCount`、`fileCountToData`、metadata completion/update processes
`bmwMakePreview_EachRunWorkerCompleted` / `ReloadOneThumbnailForSubThread`である。materially derivedしたbehaviorは、
fixed compact status slots、statusのknown-array passive read、BOOK cache metadata優先、header/listing fallback、metadata
completion時のselected status refreshである。NivisViewer側対応は`app/browser_window.py`、
`app/browser_rating_filter_widget.py`、`app/thumbnail_provider.py`と既存`BrowserItem` / thumbnail SQLite authorityである。
WinForms control、`FileCountArray`、BOOK binary serializerは移植していない。必要なlicense本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 36. Browser status left-side count / selected path（2026-08-25）

### 36.1 ZipPlaForkのCatalog status behavior

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/CatalogForm.cs` class `CatalogForm`は、`CatalogForm_Load`で
`ToolStripSpringTextBox selectedFileNameToolStripTextBox`をread-onlyにし、status stripの
`itemsCountToolStripStatusLabel`とright-side metadata slotの間へ挿入する。このtextboxは残り幅を受け持ち、
single selectionでは`ShowStatusBar(IEnumerable<int>)`が`ZipPathArray[selectedIndex]`のフルパスを入れる。
multiple selectionでは`Message._1ItemsAreSelected`にselection countを投影し、zero selectionでは空文字にする。

`tvCatalog_ShowIndexToDataIndexChanged`は`tvCatalog.ShowIndexToDataIndex.Length`を
`itemsCountToolStripStatusLabel`へ投影する。これはraw directory entry countではなく、Catalogが実際に
表示するfilter/sort mappingの要素数である。`ShowStatusBar`は同時にright-side size/page/resolutionを更新するが、
left-side textboxの長さでそれらのfixed slot幅を変えない。

### 36.2 NivisViewerのprojection

idle statusのplain messageを、left-aligned item-count `QLabel`とread-only/frameless `QLineEdit`の組み合わせへ
置き換えた。countはcanonical visible modelの`BrowserItemModel.rowCount()`を`N 個の項目`として表示し、
search/rating filterでvisible orderが変われば同じupdate pathで更新する。path fieldはsingle selectionで
`BrowserItem.path`の全文字列、multiple selectionで`N 個を選択`、zero selectionで空文字を持つ。
single pathは長くてもelideせずcontrol内に保持し、focus/select/Ctrl+Cでcopyできる。

count/path間は12 logical pxの明示spacingで、path editだけがflexible middle widthを受け持つ。
section 35のfixed-width/right-aligned sizeとpage-count/resolution slotはpermanent widgetのままで、pathの長さや
selection countで位置を変えない。loading/error/file-operationは従来の`QStatusBar.showMessage`でnormal
left widgetを一時的に隠すため、既存の運用status lifecycleは保持する。

### 36.3 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたfile/class/method/processは`source/ZipPla/CatalogForm.cs` class `CatalogForm`の
field `itemsCountToolStripStatusLabel` / `selectedFileNameToolStripTextBox`、`CatalogForm_Load`、
`ShowStatusBar()` / `ShowStatusBar(IEnumerable<int>)`、`tvCatalog_ShowIndexToDataIndexChanged`、
`SelectedFileNameToolStripTextBox_TextChanged`、`statusStrip_SizeChanged`である。materially derivedしたbehaviorは
visible mapping count、single full path、multiple-selection summary、zero-selection empty text、read-only selectable flexible field、
fixed right-side metadataとの幅分離である。NivisViewer側対応は`app/browser_window.py`とfocused offscreen testsである。
WinForms `ToolStripSpringTextBox`やZipPlaFork source codeの直接移植は行っていない。必要なlicense本文とcopyright noticeは
section 1記載の`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 37. Browser active-directory filesystem synchronization（2026-08-28）

### 37.1 ZipPlaFork fixed-revision watcher system

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
`source/ZipPla/CatalogForm.Designer.cs`の`fileSystemWatcher`は.NET `FileSystemWatcher`で、
`NotifyFilter`を`FileName | DirectoryName | Size | LastWrite | LastAccess`とし、`SynchronizingObject`を
Catalog formへ設定して`Changed` / `Created` / `Deleted` / `Renamed`をUI authorityへmarshalする。

`source/ZipPla/CatalogForm.cs` class `CatalogForm`のlocation load processはreal directoryで
`fileSystemWatcher.Path = preCurrentLocation`、`IncludeSubdirectories = true`、
`EnableRaisingEvents = true`とし、archive等ではwatchを無効化する。`fileSystemWatcherStopper`はwatchが有効か、
event parentがcurrent pathか、subfolder search/display modeとarchive-in-archive depth boundに合うかを再検査し、
深いdescendant eventを必要に応じて直下childへ畳む。initial `CreatingZipPathArray`中のeventは
`bmwMakePreview.RunWorkerStarting`まで保留し、その境界でもstopperを再検査する。

event処理は次のとおりである。

- `fileSystemWatcher_Created`はcurrent `loadingGuid`を捕捉し、
  `addOrReloadItem(fullPath, thumbnailChanged: true, startingGuid)`を呼ぶ。
- `fileSystemWatcher_Changed`はthumbnail cache folderを除外して`Created`と同じreload pathへ送る。
- `fileSystemWatcher_Deleted`は`GPSizeThumbnail.TryMove(..., null)`でcache identityを除去して`removeItem`する。
- `fileSystemWatcher_Renamed`はold/new path間でthumbnail cacheを移動し、editing suppressionを検査して
  `renameItem(newPath, oldPath, startingGuid)`を呼ぶ。
- `removeItem`はcanonical arraysのmask/path/itemを無効化する。focused item削除時には隣接visible itemへfocusを移す。
- `renameItem`は同一typeかつ`ZipPlaInfo.ThumbnailInfo`が同じsuccessful itemならpath/name、bookmark/undo、
  thumbnail/file-list表示をin-place更新する。再利用不能なら既存slotを`addOrReloadItem`でreloadする。
- `addOrReloadItem`は既存pathならthumbnail reload、新規pathなら空きslotまたは拡張arrayへ追加する。
- `enterQuickReload` / `exitQuickReload(Guid)`は複数quick updateを束ね、最後の完了かつ同じ`loadingGuid`でのみ
  `GetSortArray(preSortArray: ...)`を再構築する。その前後で`tvCatalog.ScrollBarPercentage`を保存/復元し、
  file listも`tryToKeepScroll: true`で同期する。

ZipPlaFork自身の変更との競合は一律時間blacklistではない。`GPSizeThumbnail.EnterEditing` /
`ExitedEditing` / `Editing`はCatalog/Viewer自身のmetadata writeをpath + last-access-timeで抑止する。
`renameItem`のreload分岐はnew pathが既に`ZipPathArray`に存在する場合を「ZipPla自身が先に更新済み」として
二重追加しない。new-folder/paste pathsは`addToUserAddFileList`で追跡し、watcherによる追加完了後にselectionを
まとめて復元して`ScrollBarToIndexWithMinimalMove`する。大量virtual-directory pasteではitem-by-item taskを避け
full `UpdatePreview`へ退避する。

### 37.2 NivisViewer translation

NivisViewerは既存依存のQt `QFileSystemWatcher`を`app/browser_directory_watcher.py`のisolated event sourceとして使う。
watch対象はBrowserが読み込もうとしているactive directory一つだけで、recursive watch、polling thread、追加dependencyはない。
navigationごとにfresh watcher objectとgenerationを割り当てるため、detach後にqueueへ残ったold-directory signalは
`BrowserDirectoryChange(path, generation)`検査で捨てる。watch対象のdirectory自身が消失した場合は既存scannerの
`NOT_FOUND` / `NOT_DIRECTORY`結果をauthorityとし、watchをdetachして既存`navigate_to`経由でparentへ回復する。

Qt directory notificationはexact changed pathやrename old/new pairを保証しないため、ZipPlaForkのper-path deltaを
不確かな推測で再現しない。raw eventを80 ms single-shot windowでcoalesceし、一burstにつき既存
`BrowserDirectoryScanner`のsame-directory refreshを一回だけ行うreconciled designである。eventがinitial load、
Back/Forward、manual/automatic refresh中に届いた場合は`_PendingDirectoryScan.directory_watch_dirty`一bitへ畳み、
model commit後にもう一度だけreconcileする。Back/Forwardの`atomic_restore` full commit/layout/restore-before-paint契約は維持する。

reconciliation resultは既存の
`scanner -> BrowserItemModel.set_sorted_items(preserve_thumbnails=True) -> search AND rating filter -> stable sort -> Viewer snapshot`
だけを通る。model tupleが同じならresetもthumbnail generation changeもない。変更がある場合も
`_retain_compatible_thumbnails`がpath + source mtime fingerprintの一致するready thumbnailを保持し、変更itemだけを失効する。
new visible itemのrequestは既存VISIBLE planning、近傍は既存bounded read-aheadを使う。worker数、whole-folder thumbnail generation、
独立sorted list/cacheは追加していない。

refreshは既存`_ListViewState`を使い、selectionが残ればpath selectionを復元してvisibleならscrollしない。
selectionなしではfirst-visible stable path + pixel offsetを復元する。削除selectionは同rowの別itemへ置換しない。
Qt eventからrename pairを得られないexternal renameはold remove + new addとして扱い、偽のidentity relocationを行わない。
一方NivisViewer自身のrename/moveは従来どおり`FileOperationCoordinator`と
`BrowserNavigationHistory.relocate_tree`が唯一のrelocation authorityである。operation中のwatch burstはdeferし、
operation completionが既存refreshを開始した場合はそのreconciliationへ吸収する。後着eventは無視せずidempotentな次burstとして扱う。

idle時はOS watcher以外のtimer、stat、scan、recursive enumerationを実行しない。event burstはdirectory listing一回を必要とするが、
per-event relayoutは行わない。これはZipPlaForkのevent-driven quick reload原則をQt/async generation architectureへ翻訳したもので、
WinForms `FileSystemWatcher` handler、array mutation、`QuickReloadProcessingCount`、C# source codeは移植していない。

### 37.3 Provenance mapping

materially referencedしたfile/class/method/processは、
`source/ZipPla/CatalogForm.Designer.cs`の`fileSystemWatcher` configuration、
`source/ZipPla/CatalogForm.cs` class `CatalogForm`のwatch setup、`fileSystemWatcherStopper`、
`fileSystemWatcher_Created` / `Changed` / `Deleted` / `Renamed`、`addOrReloadItem`、`addOrReloadItem_Add`、
`removeItem`、`renameItem`、`enterQuickReload` / `exitQuickReload`、`addToUserAddFileList`、fields
`CreatingZipPathArray` / `loadingGuid` / `QuickReloadProcessingCount`、および
`source/ZipPla/GPSizeThumbnail.cs`の`EnterEditing` / `ExitedEditing` / `Editing`である。
materially derivedしたbehaviorはactive-location event watch、load-generation rejection、load中eventのcommit-boundary defer、
burst後の一回sort/filter再投影、viewport preservation、own-operation deduplicationである。NivisViewer側対応は
`app/browser_directory_watcher.py`、`app/browser_window.py`、既存`app/browser_scanner.py` /
`app/browser_model.py` / `app/thumbnail_provider.py` authorityとfocused offscreen testsである。
必要なAGPL-3.0-or-later license本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 38. Browser context-menu filename copy（2026-08-29）

### 38.1 ZipPlaFork fixed-revision behavior

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/CatalogForm.cs` class `CatalogForm`は、`CatalogForm_Load`でread-only
`ToolStripTextBox rightClickFileNameToolStripTextBox`を`cmsRightClick`の先頭へ挿入する。
`cmsRightClickPrepareAndShow_`はcanonical `ZipNameArray` / `ZipPathArray`とCatalog selectionから
`selectedNameArray`を作り、single renamable itemでは`getBaseName(singleName)`、それ以外では
`TextAnalyzer.TextToWildcard(selectedNameArray)`をtextboxへ表示してimportant rangeを選択する。
したがってCatalogのright-click UIから選択項目名を直接選択・copyできる。`readme_original.txt`も
right-click menu内textboxの活用、filename auto-selection、text selection customizationを記録している。

### 38.2 NivisViewer behavior

NivisViewerはeditable WinForms textbox、rename、wildcard selection processを移植せず、既存Browser item
context menuのfile-operation `コピー`の隣に明示的な`名前をコピー` actionを追加した。値はdelegateの
rendered/elided textではなく、`BrowserItemModel`のvisible selectionをrow順に読む既存
`selected_file_operation_paths()`のcanonical absolute pathからbasenameだけを取得する。single fileは拡張子込み、
folderは末尾component全体を保持するため`Folder.Name`のdotをextensionとして分離しない。multiple selectionは
current Browser-visible orderのbasenameをLF改行で連結し、clipboardには`text/plain`だけを設定する。zero selectionの
background menuにはaction自体を追加せず、既存file URL clipboard `コピー`の実装とdispatchは変更していない。

### 38.3 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referencedしたfile/class/method/processは`source/ZipPla/CatalogForm.cs` class `CatalogForm`の
field `rightClickFileNameToolStripTextBox`、`CatalogForm_Load`、`rightClickContextMenuPrepareAndShow`、
`contextMenuPrepareAndShow`、`cmsRightClickPrepareAndShow_`、`getBaseName`、canonical arrays
`ZipNameArray` / `ZipPathArray` / `tvCatalog.SelectedIndices`、および`readme_original.txt`のright-click menu
textbox／filename auto-selection記録である。materially derivedしたbehaviorはright-click UIからcanonical selected
item nameをcopyしやすくすることだけで、C# control、rename、wildcard、selection algorithmのcodeは移植していない。
NivisViewer側対応は`app/browser_window.py`とfocused offscreen testsである。必要なlicense本文とcopyright noticeは
section 1記載の`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

## 39. Viewer true-fullscreen transition and rapid-wheel dispatch gate（2026-09-03）

### 39.1 ZipPlaFork fixed-revision fullscreen behavior

固定revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`の
`source/ZipPla/ViewerForm.cs` class `ViewerForm`、property `FullScreen`
（fixed-revision source lines 2005–2117）は、true fullscreenを
`FormBorderStyle == None && WindowState == Maximized`として定義する。enter時は以前の
`FormWindowState` / `FormBorderStyle`を保存する。すでにmaximizedで通常のViewer processなら、
visible windowを一時的に隠して`WindowState = Normal`へ戻す。このlineには
「タスクバーが残る問題の回避」と明記されている。その後`FormBorderStyle = None`、
`WindowState = Maximized`の順でborderless full-monitor surfaceを作り、必要な設定の場合だけ
`TopMostInFullscreen`を`TopMost`へ投影する。exit時は以前のborder style/state/sizeを戻し、
`TopMost = false`とする。monitor移動の可能性があるnormalization pathでは
`Screen.FromControl(this).WorkingArea`を比較し、normal boundsを対応displayへ移すが、fullscreen
surface自体はborderless maximized stateが所有する。

### 39.2 NivisViewer translation

変更前のNivisViewerは`ViewerWindow.show_initial` / `toggle_fullscreen` / `exit_fullscreen`から
Qt `showFullScreen()` / `showNormal()`を直接呼び、pre-fullscreen maximized state、normal geometry、
target screen、window flagsをtransition authorityとして保持していなかった。特にmaximized HWNDから
直接`WindowFullScreen`へ変えるため、Windows shellとのworking-area/taskbar関係がnative surfaceに残る
遷移を避ける構造がなかった。

既存`FullscreenChromeController`を唯一のfullscreen reconcilerのまま拡張し、enter時にwindow flags、
normal geometry、target `QScreen`、maximized stateを一snapshotとして保存する。visible maximized windowは
neutral stateを表示しないよう先にhideし、`WindowNoState`、`FramelessWindowHint`、target screenの
`QScreen.geometry()`（`availableGeometry()`ではない）、Qt `showFullScreen()`の順で遷移する。
Windows native platformではQtのlogical rectangleをWin32へ渡さない。2026-09-05 review修正で、
neutral-state遷移前のViewer HWNDからtyped `MonitorFromWindow`でtarget HMONITORを捕捉し、
遷移後にtyped `GetMonitorInfoW`のnative `rcMonitor`（`rcWork`ではない）を取得して
`SetWindowPos(HWND_TOP, ..., SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)`へ渡す。
`app/windows_fullscreen.py`の`WindowsFullscreenAdapter`がこのnative boundaryだけを所有する。
Qt logical QRect全体へのDPR乗算や`QScreen.name()`とのnative device-name照合は行わない。
pointer-sized HWND/HMONITOR、signed rectangle、BOOL returnとAPI failureを明示的に扱い、
monitorが消失した場合／API failureではQt boundsを保持する。
根拠は[Qt High DPI](https://doc.qt.io/qt-6/highdpi.html)、
[MonitorFromWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-monitorfromwindow)、
[GetMonitorInfoW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmonitorinfow)、
[SetWindowPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos)である。
`HWND_TOPMOST`、`WindowStaysOnTopHint`、taskbar API、shell auto-hide settingは使用しないため、
Alt+Tabやdialog ownershipをblanket topmost workaroundで変更しない。exitは一度neutral/hidden stateにし、
保存したflags、screen、normal geometryを戻してから、以前がmaximizedなら`showMaximized()`、それ以外は
`showNormal()`へ復帰する。fullscreen中のapplication shutdown snapshotも保存済みpre-fullscreen
`saveGeometry()`を使うため、次回fullscreen exitでfull-monitor boundsがnormal geometryへ混入しない。
windowed/maximized modeの通常bounds authorityは変更していない。

### 39.3 Rapid-wheel dispatch invariant

mixed ready/cold wheel burstでは、ready transit frameを通常の`RasterBookRuntime.request()`で表示すると、
同じcall末尾のcontinuous warmup `_drive()`が次のcold transit unitをsole workerへ投入できた。その直後の
wheel inputはそのjobを新currentとしてadoptするため、final cold target admissionより先にtransit decodeが
開始されていた。test expectationではなくproduction orderingの欠陥である。

shared runtimeの既存`stage` gateを拡張し、cache-ready transitは`publish_cached=True`でatomic presentationを
publishできる一方、decode/warmup dispatchはsuspendedのまま保持する。wheel boundaryはexact staged requestを
`release_staged()`で開く。2026-09-05 review修正ではcacheとrequest publicationを分け、
`_publish_frame`でpublication済みのrequest objectをsignal送信前に記録する。adoptしたworkerがstage中に
完了した場合、release時にcached final frameを同期publishする。stageで既にpublishしたready transitの
同じrequestは二重publishしない。未完了なら既存current-first driveに任せる。obsolete request、
異なるsource epoch、duplicate releaseは既存exact-object gateで拒否する。これによりready
intermediate presentation、display-ready retention、single-lane runtime、latest wheel coalescingを保持しつつ、
mixed burstのcold transit decodeを開始しない。これはZipPlaFork sourceを移植した処理ではなく、
NivisViewer固有async runtimeのcorrectness repairである。

### 39.4 Provenance

参照元repositoryは`himamon/ZipPlaFork`、固定revisionは
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、licenseはAGPL-3.0-or-laterである。
materially referenced／translatedしたfile/class/method/processは`source/ZipPla/ViewerForm.cs` class
`ViewerForm`のproperty `FullScreen`、fields `FullScreen_pseudoFullScreen`、`prevFormWindowState`、
`prevFormBorderStyle`、`prevSize`、`TopMostInFullscreen`、maximized enter時のhidden -> Normal ->
borderless -> Maximized transition、exit restoration processである。NivisViewer側対応は
`app/fullscreen_chrome.py`の`FullscreenChromeController.enter_true_fullscreen` /
`leave_true_fullscreen` / `_apply_native_fullscreen_bounds`と`app/viewer_window.py`の既存fullscreen
entry pointsである。必要なlicense本文とcopyright noticeはsection 1記載の
`licenses/ZipPlaFork/AGPL.txt` / `licenses/ZipPlaFork/About.txt`に保持している。

2026-09-05のpublication修正とWin32 coordinate adapterはNivisViewerの不具合修正であり、
追加のZipPlaFork code移植やdependency追加はない。固定revisionとAGPL-3.0-or-later provenanceは上記のまま保持する。
offscreen/fake testsは100/125/150/200%、負のorigin、mixed-DPI target monitor、geometry復元、
ZIP/Folder completion-before/after-releaseを検証する。実Windows taskbar、Alt+Tab、native visible windowの
動作はこの検証では実行しておらず、以前記載したtaskbar persistence原因は実機で確定した結論ではない。

### 39.5 Current-tree verification (2026-09-05)

The follow-up inspected the authoritative uncommitted implementation above;
it did not reapply or replace those repairs. Four alternatives remain distinct:
ZipPlaFork's borderless/maximized transition provides the Windows transition
reference; the former NivisViewer direct Qt transition lacked explicit restore
ownership; the implemented hybrid retains Qt fullscreen/chrome ownership and
adapts the neutral transition plus a narrowly scoped native monitor correction;
a new shell/taskbar manager or blanket topmost policy is unnecessary and was
not introduced. Native taskbar suppression is still an unverified hypothesis,
not a result demonstrated by the fake adapter tests.

Fresh offscreen verification: ZIP runtime/integration suites **40 passed**;
fullscreen state/controller/navigation suites **81 passed**. The exact
`test_rapid_wheel_presents_only_ready_intermediate_frames[mixed]` case passed
in **5/5 separate processes**; ready/cold/mixed variants also passed together
(3 tests). No assertion was weakened and no timing sleep was added.

The existing production `scripts/benchmark_viewer_navigation.py` ran with
13 synthetic 4096 × 6500 JPEG pages, a 1920 × 1080 viewport, and a 512 MiB
cache under offscreen Qt (Python 3.12.14 / PySide6 6.11.2). Request-to-paint:
immediate cold final target 46.772 ms; sequential 3.011 ms; reverse 1.876 ms;
direction reversal 1.870 ms; ping-pong median 1.464 ms; rapid final 2.376 ms.
The immediate burst committed only its final page (5). One cancelled old
result was rejected; terminal errors were zero. These are synthetic single-run
measurements, not a before/after speedup or a real-machine responsiveness claim.
The solid/compressible fixture archive was only 13,525 bytes despite the large
decoded dimensions, so it does not measure real large-archive storage latency.
No real application, taskbar interaction, native input, commit, or push ran.

### 39.6 Attachment re-verification against the current tree (2026-09-06)

The repeated fullscreen/mixed-wheel request was checked against the actual
uncommitted files. Both production repairs and their focused tests already
existed; they were preserved, not reapplied. The fixed-revision
`ViewerForm.FullScreen` source was read again and confirms section 39.1.
No production or test expectations changed in this verification pass.

Fresh processes: the exact mixed case passed **5/5**; the ZIP runtime and
integration suites passed **40 tests**; fullscreen state, controller and
navigation suites passed **81 tests**. Syntax parsing passed for the seven
relevant production/test files, and `git diff --check` passed.

An additional production-runtime offscreen smoke used nine seeded high-detail
2400 x 3600 JPEG pages (41,472,435-byte ZIP), a 1280 x 800 viewport and 256 MiB
cache on Python 3.12.14 / Qt 6.11.2. Request-to-paint measurements were:
immediate cold final target 84.183 ms, sequential 1.111 ms, reverse 1.382 ms,
direction reversal 1.415 ms, ping-pong median 1.201 ms, rapid final 2.452 ms.
The immediate burst committed only its final page (5); one cancelled old
result was rejected and terminal errors were zero. These are a single
synthetic smoke run, not a before/after speedup or Windows compositor timing.

Native taskbar suppression (ordinary and auto-hide), Alt+Tab and real dialog
ordering remain unverified. Fake native bounds/state tests do not establish
that the shell symptom is resolved on the user's machine. No real application,
native input, shell-setting change, commit or push was performed.

### 39.7 Current attachment verification (2026-09-09)

The current attachment requests the mixed-wheel/fullscreen repairs already
present in the authoritative working tree. They were inspected and preserved;
no production code or test expectation was changed in this verification pass.
The fixed-reference `ViewerForm.FullScreen` source was reread; the methods,
transition comparison and AGPL-3.0-or-later provenance in 39.1–39.4 still apply.

On Python 3.11.9 / PySide6 and Qt 6.11.2, offscreen verification passed the
exact mixed-wheel case in **5/5 fresh processes**. The runtime, ZIP integration,
fullscreen-state and navigation-policy suites passed **59 tests**. A combined
controller/navigation run aborted during garbage collection while the first
Viewer fullscreen-wheel case imported PDF support with a raster worker active;
that run is not counted as passing, and its root cause was not established.
The same fullscreen-wheel case and four bottom-edge reveal/hover cases passed
in a fresh focused process (**5 tests**). This does not establish that the
broader test-process lifetime problem is resolved. The standalone controller
suite also passed **7 tests**. Syntax parsing passed for seven relevant files,
and `git diff --check` passed.

The existing large-image offscreen smoke used nine seeded high-detail
2400 × 3600 JPEGs (41,472,435-byte ZIP), 1280 × 800 viewport and 256 MiB cache.
Request-to-paint: cold burst final 66.212 ms, sequential 0.905 ms, reverse
0.901 ms, direction reversal 1.197 ms, ping-pong median 0.904 ms and rapid
final 1.597 ms. Only final page 5 committed in the cold burst; one cancelled
result was rejected and terminal errors were zero. These are single-run
synthetic measurements, not native compositor timing or a before/after claim.

Actual ordinary/auto-hide taskbar suppression, Alt+Tab, dialogs and native
multi-monitor transitions remain unverified. No real application, native
input, external GUI, shell configuration change, commit or push was performed.

### 39.8 Windows taskbar-edge recurrence and native maximize state (2026-09-14)

Portable 1.04 still allowed the Windows taskbar to appear when the pointer
reached the bottom edge. Its temporary `HWND_TOPMOST` change affected only
z-order; it did not change the window's screen-occupation state or the shell's
auto-hide edge policy. Microsoft documents auto-hide and foreground-window
handling separately from z-order. The user's recurrence confirms that
topmost alone was insufficient; native taskbar activation was not reproduced
inside this offscreen-only environment.

The fixed ZipPlaFork source was reread directly at revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:
[`source/ZipPla/ViewerForm.cs`, `ViewerForm.FullScreen`](https://github.com/himamon/ZipPlaFork/blob/07955f5267e2fb92d6fc6e40fde2507d8fb07b3b/source/ZipPla/ViewerForm.cs#L1872-L1975).
It hides a visible maximized form, normalizes `WindowState` to `Normal`, then
sets `FormBorderStyle.None` followed by `WindowState.Maximized`. The source
comment identifies the normalization as a workaround for a taskbar left
visible during transition. `TopMostInFullscreen` defaults to false, and this
path does not call taskbar/AppBar APIs. This evidence distinguishes a stale
taskbar after a state transition from the separate edge-hover activation
reported here.

Qt exposes distinct [`WindowFullScreen` and `WindowMaximized` states](https://doc.qt.io/qt-6/qt.html#WindowState-enum), while
Win32 identifies a maximized window by [`WS_MAXIMIZE`](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features#minimized-maximized-and-restored-windows).
Windows documents taskbar auto-hide and full-screen coverage as separate
behavior from ordinary z-order ([Taskbar](https://learn.microsoft.com/en-us/windows/win32/shell/taskbar)).
NivisViewer previously
used `showFullScreen()` even on Windows, then corrected bounds with native
`rcMonitor` and temporarily promoted the HWND to topmost. The current Windows
translation keeps the existing hidden/normal/borderless transition and saved
restore snapshot, but finishes with `showMaximized()` so Qt requests the
OS-managed maximized state; the typed Win32 adapter then projects the outer
rectangle to the captured monitor's `rcMonitor` for full-display coverage.
Non-Windows platforms retain `showFullScreen()`. The application-level
fullscreen contract is derived from the controller's owned transition so
chrome, page-list, cursor and state-snapshot behavior remain fullscreen while
Qt reports the Windows surface as maximized. No taskbar preference is changed
and no blanket topmost state is applied.

Alternatives were evaluated explicitly: ZipPla's borderless/maximized
transition addresses shell state normalization; the prior NivisViewer
`WindowFullScreen` plus topmost approach did not address the reported symptom;
the adopted hybrid retains Qt's chrome/controller and restoration contract
while using Windows' maximized state and native monitor bounds; a new
AppBar/taskbar manager was rejected because ZipPla does not use one and it
would alter shell-owned behavior. Whether the shell now suppresses this user's
edge activation remains an inference until real Windows verification.

Focused offscreen/fake coverage in `tests/test_viewer_fullscreen_state.py`
checks the Windows branch remains Qt-maximized, restores the prior normal
bounds/state, still reveals the bottom chrome on a synthetic pointer event,
and retains native `rcMonitor` coverage for mixed-DPI origins. No real window,
native pointer, taskbar, or monitor was exercised. Portable 1.04 was left
untouched and was not rebuilt.

Provenance: repository `himamon/ZipPlaFork`, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later; source file
`source/ZipPla/ViewerForm.cs`, class `ViewerForm`, property `FullScreen` and
its saved-state / entry / exit process. The translated structure corresponds
to `app/fullscreen_chrome.py` (`enter_true_fullscreen`,
`leave_true_fullscreen`, native Windows branch) and
`app/viewer_window.py` (`_is_fullscreen_mode`). No C# code was copied and no
dependency was added. The license text and copyright notice remain in
`licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`.

## 40. Browser snapshot-owned navigation index (2026-09-05)

### 40.1 Fixed source and design choice

Inspected the commit object in the local reference repository, not its working
tree: `https://github.com/himamon/ZipPlaFork`, revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, `source/ZipPla/CatalogForm.cs`,
class `CatalogForm`. Both `getSelectedPathArray` overloads read `ZipPathArray`
and order selected data indexes through `tvCatalog.DataIndexToShowIndex`;
`tvCatalog_ShowIndexToDataIndexChanged` reads the retained
`ShowIndexToDataIndex.Length` for the visible count. These are examples of
reusing visible/data correspondence, not evidence that every ZipPla operation
is constant-time (the selection path itself sorts selected indexes).

- ZipPla: retained visible/data arrays correspond to canonical path arrays.
- Previous NivisViewer: immutable captured visible order, but openable paths,
  normalized keys, membership sets and image lists were rebuilt on each input.
- Adopted hybrid: retain NivisViewer's immutable captured-order authority and
  derive its lexical normalized indexes once, then reuse them.
- New design: a global navigation index would duplicate ownership/invalidation;
  it is unnecessary and was not introduced.

This is structural reference, not a direct C# port. Source license is
AGPL-3.0-or-later; the upstream license and copyright notices remain in
`licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`. Corresponding
NivisViewer code is `app/adjacent_book_search.py`,
`AdjacentBookBrowserSnapshot.__post_init__`, `viewer_paths`,
`adjacent_viewer_path`, `contains_viewer_path`, and its image-only projection;
`app/application_controller.py`, `_synchronize_browser_to_viewer_item` and
`_folder_snapshot_from_browser_navigation`. No dependency was added.

### 40.2 Implementation and preserved contracts

The frozen snapshot owns tuple paths and read-only `MappingProxyType` indexes.
`setdefault` preserves the FIRST normalized duplicate exactly as the previous
`tuple.index`. Membership reuses the same Viewer index, not a second set.
The image-only index is a different filtered domain: the existing controller
also reconstructed all image paths/fingerprints on every adjacent-book open,
even for ZIP targets. It now reuses snapshot tuples while constructing the
existing `FolderListingSnapshot` with only the selected index/image changed.
This small local change removes the remaining per-action snapshot traversal;
it does not alter folder loading or establish another listing authority.

Constructor arguments, frozen behavior, equality, hash and repr are unchanged:
all derived fields are `init=False, compare=False, hash=False, repr=False`.
Actual consumers pass snapshots in memory; no snapshot serialization/deepcopy
consumer was found. Path identity still uses `lexical_absolute` / `path_key`,
without stat, resolve, scans or sorting. Negative direction means previous;
zero and positive mean next. Empty/missing, boundary, loop, mixed eligibility,
captured sort/filter and first-duplicate behavior are preserved. Browser sync
checks Browser existence/location before membership and leaves unrelated
Browser navigation untouched. Its no-snapshot fallback is unchanged.

### 40.3 Same-runtime A/B and cost tradeoff

`scripts/benchmark_browser_snapshot_index.py` contains an explicitly isolated
benchmark-only copy of the former algorithms. Both implementations receive
the same prebuilt synthetic entries (alternating archive/image, Japanese
basenames) in one Python 3.12.14 / Windows process. Times below are medians of
7 samples; indexed lookup samples batch 1,000 calls and report per-call time.
Construction is timed separately; `tracemalloc` runs separately from timing
and excludes the shared input entries. There is no timing-based pass threshold.

| Entries | Neighbor before → after ms | Membership before → after ms | Image projection before → after ms |
|---:|---:|---:|---:|
| 1,000 | 1.016600 → 0.000979 | 1.025600 → 0.000789 | 0.702200 → 0.002079 |
| 10,000 | 10.443700 → 0.000981 | 10.712600 → 0.000794 | 7.012900 → 0.002087 |
| 50,000 | 54.987400 → 0.000996 | 58.234100 → 0.000804 | 42.698400 → 0.002532 |

| Entries | Construction before → after ms | Derived retained / construction peak bytes (after) |
|---:|---:|---:|
| 1,000 | 0.000700 → 1.385300 | 311,336 / 328,456 |
| 10,000 | 0.000600 → 14.259400 | 3,193,640 / 3,362,408 |
| 50,000 | 0.000600 → 91.636500 | 17,525,920 / 18,408,256 |

Before construction retained only 288–424 bytes beyond shared entries, paying
for derivation each time instead. At 50,000 entries, neighbor/membership peak
temporary allocations were 11,031,646 / 12,653,484 bytes before, 468 / 468
after. Image projection peak was 10,061,136 before versus 1,000 bytes after.
Snapshot creation is still O(n) and now pays the derivation upfront; each live
snapshot retains its derived memory until released. Large snapshot capture can
therefore still incur noticeable one-time UI cost. Lookup work is independent
of entry count (but depends on target path length and ordinary dictionary
lookup behavior). These are string-only results, not measured end-to-end
Viewer/Browser latency or a demonstrated real-device speedup.

Focused tests verify no entry iteration and exactly one target normalization
per neighbor/membership/image-projection call at 1/1,000/50,000 entries, plus
immutable derived maps and dataclass contracts. Existing integration coverage
checks rapid pending XButtons, same Viewer reuse, moved-away Browser, image /
folder / archive selection sync, hidden rating target neutrality, and transient
search round trips. Runtime: bundled Python 3.12.14 / PySide6 6.11.2 with existing
site-packages; Python 3.11 is unavailable and was not repaired. Tests use
offscreen Qt/fakes/temp fixtures only; no real app, native input, commit or push.

Fresh-process groups passed **41 tests**: 12 new snapshot-index cases, 9 Viewer
XButton cases, 9 controller snapshot/sync/roundtrip cases, and 11 adjacent-search
stability cases. The repository-required production navigation check also ran
on the same 13-page 4096 × 6500 synthetic fixture described in section 39.5:
sequential 1.593 ms, reverse 1.542 ms, direction reversal 1.793 ms, ping-pong
median 1.700 ms, rapid final 2.981 ms (request-to-paint); zero terminal errors.
It is a regression smoke check, not a performance result attributable to this
snapshot change. The compressible-fixture and offscreen limitations still apply.

## 41. Shared initial-open capture and detailed ZIP observation (2026-09-05)

### 41.1 One Browser capture per initial open

The accepted section 40 implementation is the baseline, not the pre-index
implementation. Previously `BrowserWindow.open_item` called
`_folder_snapshot_for_item` and `adjacent_book_snapshot` independently; both
executed `_visible_order_snapshot_items` (source + remaining progressive items,
filter, stable sort). The `_invoke_open_path_handler` fallback could do the same.

`open_item` now delegates preparation to `_invoke_open_path_handler`, carrying
the exact selected `BrowserItem`. That dispatch captures the visible-order
tuple once when snapshots are missing and passes it to the existing snapshot
builders through an optional `snapshot_items` argument. No global cache or
additional order authority exists. Progressive `remaining_item_offset` and
same-location/committed guards are unchanged. Supplied snapshots are preserved;
external drops with `use_browser_order=False` do not capture; 2/3/4-argument
handlers, reuse/new-window flags and bookmark/history fallbacks remain intact.

The image-only builder deliberately retains its original semantics: every
IMAGE entry and exact path/selected-item matching. Scanner-produced IMAGE
entries are supported (`browser_scanner.scan_entry_from_dir_entry`) and are
converted to absolute paths (`browser_model.browser_item_from_scan_entry`).
Nevertheless the model can accept constructed items with disabled openability
or normalized duplicate identities. The mixed snapshot's image index filters
openability and selects the first normalized match, so substituting it blindly
would change these contracts. Both snapshots derive from the same captured
items, but keep their existing eligibility/path rules. The accepted mixed
snapshot indexes are still constructed and reused for subsequent navigation.

`scripts/benchmark_browser_open_capture.py` measures both snapshot constructors
and dispatch, using production Browser/model methods with a fake handler.
Before = the original separate builders with the accepted indexed mixed
snapshot; after = shared preparation. Identical prebuilt reversed mixed
image/archive items, rating-descending/folders-first, search `keep`, rating≥3;
7-sample medians in one runtime. Input/model setup and real file opening are
excluded. `tracemalloc` runs separately and excludes shared input storage.

| Source / visible items | Pipeline calls before → after | Preparation before → after ms | Retained bytes before → after | Peak bytes before → after |
|---:|---:|---:|---:|---:|
| 1,000 / 514 | 2 → 1 | 8.2291 → 4.7565 | 318,476 → 285,372 | 326,556 → 297,708 |
| 10,000 / 5,143 | 2 → 1 | 84.2286 → 48.7380 | 2,680,552 → 2,680,496 | 2,768,152 → 2,809,384 |
| 50,000 / 25,714 | 2 → 1 | 582.7358 → 364.7863 | 13,651,720 → 13,651,664 | 14,086,040 → 14,291,840 |

Large retained results are effectively unchanged; keeping the capture alive
through both builders slightly increases temporary peak memory. Allocator/cache
effects are included in the small case. Preparation still performs one full
filter/sort and O(n) snapshot derivation; 365 ms is not an instantaneous large
folder open. These string/model measurements do not establish real UI latency.

### 41.2 Reproducible high-detail fixture

The maintained `scripts/benchmark_viewer_navigation.py` keeps the default flat
JPEG mode and adds `--fixture-mode high-detail`. Algorithm
`seeded-tile-texture-pseudo-glyphs-v1`: Python Random seed `20260905 + page`,
independent 256×256 luminance-texture tiles mapped to 168–231, 24-pixel ruled
rows, 20×24 pseudo-glyph pitch with three variable-width dark bars per glyph,
and 256-pixel panel lines. This distributes texture and structured/text-like
edges across the page without relying on a system font or a pure-noise image.
It is synthetic stress content, NOT a real scan. JPEG quality 88, subsampling 2.
One RGB page plus a tile and compressed buffers is generated at a time; there
is no all-pages pixel array. Generation and hashing precede navigation timing.
All ZIPs/configs are temporary, no image-library access or new dependency.

The JSON report records dimensions/pages, algorithm/seed/parameters, JPEG
quality/subsampling, generation duration, JPEG payload bytes, total payload
including optional ignored padding, physical ZIP bytes, content digest
(entry names + JPEG payloads + optional padding), physical ZIP digest, and
Python/Pillow/JPEG/libjpeg-turbo/Qt/PySide/zlib/OS versions. ZIP member timestamps
are fixed for reproducible containers; default flat JPEG bytes remain unchanged.

### 41.3 Paint-based residence and final-target observation

Schema 5 extends existing input/commit/`framePainted` instrumentation; it does
not change production scheduling. The immediate burst remains synchronous
`next_page(WHEEL)` calls followed by the existing wheel-end release, with NO
inter-input pumping. Previously the hidden Viewer was explicitly rendered only
after the target was ready; now every post-burst event-pump poll renders the
current surface. The existing 1 ms idle wait is unchanged. This observation
policy is explicit in the report and identical for both fixture modes. It can
observe ready intermediate frames, but does not manufacture them or simulate
a human input cadence. Old schema-4 timings with target-only rendering are not
a like-for-like comparison with this observer.

All timestamps/deadlines use `perf_counter`. Residence starts with the already
painted initial page at the first input, includes every distinct painted unit
transition, and ends at the explicit final observation (first final-target
paint observed by the pump, or timeout). Repaints and same-unit quality updates
do NOT reset residence; `same_unit_paints` counts those separately, and paint
records retain serials. Maximum residence and first final-target paint time
are computed from full-precision timestamps, then rounded. Leading and trailing
intervals are included. No transitions means the entire observation duration,
not zero. A missing final paint yields null time and `incomplete`/`timeout`;
an initially visible target reports `already_at_target`, time 0, but still
accounts for the observation duration. An actual immediate-observer timeout
returns a failed report and explicitly skips remaining scenarios; CLI exits 1.
Preparation failures still raise explicitly rather than yielding success.

The immediate report includes input count, final accepted target, full painted
sequence, distinct transitions, observation duration and unchanged intervals.
`request_to_paint_ms` now means the FIRST final `framePainted` timestamp, not
the later return from a wait helper. Work counters are accurately named
`work_through_final_observation`. Offscreen forced rendering is not Windows
compositor presentation; real wheel delivery, display timing and subjective
smoothness remain unverified.

### 41.4 Matched workload observations and verification

Fresh-process flat/high-detail runs used identical 9×2400×3600 pages,
1280×800 viewport, 256 MiB cache, DEFLATED ZIP, no padding, 5 immediate inputs
from page 0 to accepted page 5, and 30 s timeout. Both observed only the distinct
transition `[0] → [5]`; no ready intermediate painted in this cold burst.

| Metric | Flat | High-detail |
|---|---:|---:|
| JPEG payload bytes | 1,220,661 | 41,509,228 |
| Physical ZIP bytes | 6,905 | 41,472,435 |
| Fixture generation ms (not navigation) | 126.016 | 1,746.077 |
| First final-target paint ms | 21.579 | 61.165 |
| Maximum unchanged-page interval ms | 21.579 | 61.165 |
| Trailing observation interval ms | 0.159 | 0.117 |
| Total observation ms | 21.738 | 61.283 |
| Warm sequential / reverse ms | 1.611 / 0.978 | 1.075 / 1.014 |
| Direction reversal / ping-pong median ms | 1.282 / 0.993 | 1.265 / 1.049 |
| Warm rapid-final ms | 1.870 | 1.675 |
| Terminal errors | 0 | 0 |

Content SHA256: flat
`6342fe6ce3fede938dabcbc114e9bb72860b8c1032457ac9f89b8af74cd44f3b`,
high-detail `eb1ade19ee3aa110f986e9a3bdee7d8a58681ab53d5fcdc071250376ad313582`.
Physical ZIP SHA256: flat
`c84b993c66f7367ebf2e71c11001efb4ea001eca17832bd87dfd5c19d114afe8`,
high-detail `6e762622fca5c52b89219b007a148816e954794c932f11fdb8c51e50337b32be`.
These are workload differences, not a production speedup. A prior matched run
observed 21.413 / 65.871 ms; do not treat single-run values as stable latency
thresholds. Both runs produced identical fixture digests.

Runtime: bundled Python 3.12.14, PySide6/Qt 6.11.2, Pillow 12.3.0, JPEG 8.0,
libjpeg-turbo 3.1.4.1, Windows 11 build 26200. Python 3.11 remains unavailable;
no environment repair was performed. Focused groups passed 58 tests: 10 new
Browser capture cases, 7 benchmark fixture/observation cases, 12 snapshot-index
cases, 18 XButton/controller cases, 7 progressive-scan/drop cases and 4 existing
snapshot cases. Tests cover repeatable/different JPEG fixtures, leading/trailing
gaps, same-unit paints, no changes, already-at-target and failed timeout reports.
No real app launch/native input/external GUI, commit or push occurred.

### 41.5 Fixed-revision reference and four-way decision

Reference: `https://github.com/himamon/ZipPlaFork`, revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.
`source/ZipPla/CatalogForm.cs`, class `CatalogForm`, `getSelectedPathArray`
overloads, `ShowIndexToDataIndex` / `DataIndexToShowIndex` / `ZipPathArray`
correspondence remain the structural reference for reusing captured order.
`source/ZipPla/ViewerForm.cs`, class `ViewerForm`, property `NextPage`, methods
`movePageNatural` and `moveToNextPage` were inspected at the fixed revision:
unready size/frame paths can keep current position; natural movement restores
old current when it cannot advance. Equal input counts therefore do not imply
equal accepted distance versus NivisViewer's latest-target contract.

ZipPla's retained correspondence is useful; previous NivisViewer duplicated
initial-open capture; the adopted hybrid shares action-local capture while
retaining NivisViewer order, eligibility and input contracts. A new global cache
or benchmark scheduler would add another authority and was rejected. The
fixture and paint metric are new benchmark-only evaluation code; no ZipPla
scheduler/input suppression was ported. NivisViewer correspondence is
`app/browser_window.py` snapshot/open helpers and
`scripts/benchmark_viewer_navigation.py` fixture/immediate observer. No direct
C# port is claimed. Existing `licenses/ZipPlaFork/AGPL.txt` and `About.txt`
retain upstream license/copyright notices. Search UI and production runtime
scheduling were not changed.

## 42. Requested-page feedback without changing committed presentation (2026-09-05)

The user requested only the existing slider position and existing page fraction
to communicate the latest accepted destination while a cold frame is pending.
No spinner, overlay, dimming, arrow/current-to-target label, loading caption,
setting or search-field change was added.

### Ownership and reconciliation

Inspection confirmed that every accepted same-book page movement reaches
`ViewerWindow._refresh_view` / `_begin_presentation_request` before the runtime
admission decision, `stage`, cold decode or wheel-end release. Thus the existing
`ViewerPresentationState.requested` already holds the validated/clamped focused
page even when `_pending_zip_runtime_request` has not been dispatched (or is
absent because the leading/reversal input was admitted immediately).

`ViewerPresentationState.navigation_feedback` is a read-only derived
`PresentationNavigationFeedback(total_pages, page_index)` view, not another
mutable cursor. `_project_presentation_surface` projects it synchronously to
the existing slider and page fraction. `status_values`, `slider_page_index`,
`progress_values`, displayed frame identity, image path/size/resolution, history
and persistence remain committed-only under their existing contracts. A first
request with no committed image can show its page fraction without inventing
image details. Existing temporary status overrides keep their priority.

`ViewerPageSlider.set_page_state` already uses `QSignalBlocker`; projecting a
target cannot generate navigation. `_on_slider_changed` now reconciles against
that target rather than snapping a drag back to the committed page. It does
not clear slider-down state. No decode admission, wheel accumulation, slider
single-page behavior, renderer, worker, cache or warmup policy was changed.
No forced rendering or visibility change was added; hidden/fullscreen chrome
continues to be managed by its existing controller.

`fail_pending` or a superseding fence clears the request, so feedback falls
back to the retained committed page. A subsequent valid request can immediately
establish a new target through the same authority. Existing token/book/layout
guards reject obsolete completions/errors before projection. Ready intermediate
frames do not provide a separate widget cursor that can override a newer
request. A committed error frame keeps its pre-existing commit/progress
semantics; it is distinct from terminal abandonment with an old frame retained.

Replacement-open fencing discards the old uncommitted target. While the old
canvas is retained, its own committed page/total remains the feedback fallback,
including while a different-book request is pending. The new book's page/total
becomes visible at its first commit. A failed replacement retains the old book;
the existing recovery path may issue a fresh valid request for that book.
`clear_book` and `close` yield no feedback and disable/reset the slider, so no
previous book target/total survives into the empty state.

### Verification and limits

Before implementation the new blocked-worker reproduction failed for both ZIP
and folder sources: accepted target 1, slider still 0. After implementation,
controls reflect every accepted forward/reverse target before release while
the old pixmap, displayed values, history and progress remain unchanged.

Fresh-process groups passed **51 tests**: 13 focused offscreen feedback cases
(ZIP/folder blocked decode, drag signal count, abandonment/stale error, LTR/RTL
focused spread/bounds/hidden controls, real replacement/close flow, and a PDF
source using a fake backend), 7 presentation-state cases, 15 runtime/Viewer
integration cases and 16 slider/fullscreen wheel cases. Existing mixed
ready/cold transit tests now also assert current target controls during the
burst. The old control-only committed assertions in `test_viewer_window.py`
were deliberately updated; its atomic image, page-list, history and progress
assertions were retained and strengthened. Syntax/diff checks passed.

The maintained high-detail fixture smoke used the same section 41 settings:
9×2400×3600 pages, 1280×800 viewport, 256 MiB cache, five cold burst inputs to
page 5. First final paint and maximum unchanged-page interval were 60.879 ms;
the observed distinct image sequence was still `[0] → [5]`. Sequential/reverse/
direction-reversal/ping-pong median/warm rapid-final request-to-paint were
0.984 / 0.965 / 1.249 / 0.958 / 1.649 ms; terminal errors zero. The page controls
now communicate intent during that retained-image interval; these results are
not a claim of faster decoding or real Windows compositor responsiveness.
No benchmark paint boundary was redefined to count control updates as paints.

Runtime: bundled Python 3.12.14 / PySide6 6.11.2; Python 3.11 remains unavailable
and was not repaired. Only offscreen Qt, fake input/backends and temporary
fixtures were used. No real app/native input/external GUI, commit or push ran.

### Reference disposition

This is a NivisViewer-specific UX change, not a new ZipPlaFork port. The
fixed reference remains `himamon/ZipPlaFork`, revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later; existing provenance
and license/copyright notices remain unchanged. ZipPla's `ViewerForm.NextPage`
/ `movePageNatural` readiness-gated input is not being adopted. The chosen
NivisViewer approach derives feedback from its existing request authority;
a hybrid suppression policy would change accepted movement, and a new widget
cursor/overlay system would duplicate ownership. Neither was introduced.
Correspondence: `app/viewer_presentation_state.py` navigation feedback view;
`app/viewer_window.py` presentation/slider/status projection only.

## 43. Unified Browser sorting and persistent Random (2026-09-06–07)

### Fixed source findings and disposition

Reference: [himamon/ZipPlaFork](https://github.com/himamon/ZipPlaFork), fixed
revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.
The fixed source was read before implementation, not the moving worktree.

`source/ZipPla/CatalogForm.cs`, class `CatalogForm`, populates `cbSortBy`
(lines 3876–3890) with Type, Name, Rating, Created, Accessed, Modified and Size,
each ascending/descending, followed by Random. `GetSortArray` (14101 onward)
uses captured creation/access/modification arrays, keeps unrated entries after
rated entries in both directions, and calls `GetTypeNameArray` for type sorts.
`GetTypeNameArray` (14638) caches localized Windows type descriptions per
extension; it does not classify every image/archive as one coarse category.

The Random branch (14566 onward) retains `GetSortArray_RandomSeed` and builds
a per-path hash table. `GetRandomIndex` (14678) hashes a metadata-stripped base
path plus seed with SHA-1. Its comment explicitly protects order across tag
changes. `source/ZipPla/ZipPlaInfo.cs`, class `ZipPlaInfo`, method
`GetBasePathRoughly`, strips a basename metadata block, not parent-directory
components. NivisViewer uses its existing validated metadata parser instead
of that rough substring parser and SHA-256 instead of SHA-1; permutations are
not intended to be byte-for-byte compatible.

`cbSortBy_SelectedValueChanged` (14745 onward) clears the seed for a Random
action. Settings save/load paths (9921, 10130, 10281) persist/restore it; the
save shown there emits the seed only when the active mode is Random.
`cbSortBy_DropDownClosed` around 16540 is commented out, not evidence of an
active same-item reselect gesture. NivisViewer's explicit Random reactivation
is a requested UX adaptation. ZipPla's Random action clears selection and
scrolls to the top; that side effect was deliberately not adopted.

Four-way decision: a literal ZipPla port would bring WinForms controls,
localized shell lookups and unwanted selection clearing; keeping NivisViewer
unchanged would retain two selectors and omit the new criteria. The chosen
hybrid adopts the combined catalog and stable seeded-path ordering while
retaining the existing NivisViewer sort/filter, selection, viewport and frozen
snapshot authorities. A new parallel sorting/history/cache system was rejected.

### NivisViewer correspondence and ordering contract

`app/browser_sort.py` owns `BROWSER_SORT_CHOICES`, normalizers and
`BrowserSortPolicy`. Both `BrowserWindow` and the Browser section of
`SettingsDialog` use the same 15 entries: 種類, 名前, レート, 作成日時,
アクセス日時, 更新日時, サイズ (each 昇順/降順), then exactly one ランダム.
The independent direction control is removed. The existing compact chrome,
font sizes and search sizing rules are unchanged.

`BrowserWindow._apply_browser_controls` uses QComboBox `activated`, not
`currentIndexChanged`: one explicit activation applies key/direction (and,
for Random, seed) in one `ConfigManager.apply` and one model reorder. Same-item
Random activation is included. Programmatic selection/synchronization and
popup open/close do not activate. Settings keeps changes as a local draft until
Apply/OK; Cancel does not persist a seed. An unchanged dialog preserves even
a legacy Random/descending stored pair without a redundant reset.

ConfigManager retains separate `browser_sort_key`/`browser_sort_order` for
compatibility and adds `browser_random_seed`, a JSON integer in 0..2^64-1.
Missing/invalid seeds normalize deterministically to zero, the migration/default
seed; there is no entropy generation during normalization or sort. Each explicit
Random activation generates a different bounded seed. The seed remains stored
even when switching to regular criteria (an intentional extension of upstream
persistence), and survives refresh, batches, filters, metadata changes,
Back/Forward and restart. A different seed need not produce a different
permutation of a tiny list.

Random sorts the normalized lexical path with valid basename ZipPla metadata
removed via `ZipPlaFilenameMetadata.parse(...).display_name`; it never trusts
delegate/rendered text, uses Python `hash()`, stats paths or shuffles per call.
Folders-first is an independent leading partition. Hash ties use stripped
identity, then case-folded/original full path. Distinct physical names collapsing
to the same stripped identity therefore have deterministic order; a rename
within that degenerate duplicate-identity group can change its final tie-break.
Ordinary rating/tag metadata renames keep the same random position. New items
join their seeded position and removing/filtering items retains the relative
order of survivors. Random ignores the legacy direction value.

Regular criteria retain NivisViewer natural-name secondary order, deterministic
path ties and natural name sorting. Unrated stays last both ways. Missing dates
and sizes retain the existing numeric `-1` sentinel (before ordinary nonnegative
values ascending, after them descending); newly exposed timestamps follow the
same rule. This is not ZipPla's size-ascending unknown-last normalization.
Folders-first overrides all primary directions. No recursive folder size is
computed. Type sorting deliberately uses case-insensitive extension groups,
including unsupported extensions and a separate folder group. The inspected
`ShellAssociatedIconProvider` supplies icons, not cached type descriptions;
no shell calls or new description cache were added to comparators/painting.
This is meaningful file-type grouping, not exact localized Explorer parity.

`app/browser_scanner.py` appends optional `created_time_ns`/`accessed_time_ns`
fields to `BrowserScanEntry`; `app/browser_model.py` carries them into appended
`BrowserItem` fields. Existing positional constructors remain valid. The same
`DirEntry.stat(follow_symlinks=False)` supplies all timestamps: birthtime_ns
when present, ctime_ns only as a Windows fallback, and atime_ns for filesystem
access (not reading-history time). This follows the
[Python stat_result documentation](https://docs.python.org/3/library/os.html#os.stat_result).
POSIX ctime is not treated as creation; no access-time settings or timestamps
are written. Worker-prepared sort policy equality includes the seed. The
existing Browser refresh tuple/dataclass equality detects timestamp-only
changes, retaining compatible thumbnails; no second reconcile mechanism exists.

`app/adjacent_book_search.py` and the existing sibling-folder request in
`app/application_controller.py` carry the same seed and optional timestamps,
including the adjacent-search cache variant. Sibling date sorts request captured
stat metadata; rating sorting uses the existing filename parser.
`BrowserWindow._sync_browser_controls`, `_current_browser_sort_policy`,
`_capture_list_view_state`/restore and `_finalize_rating_batch` remain the
production authorities. Browser-originated Viewer opens still capture the
single filtered/sorted order once, and the immutable snapshot indexes remain
unchanged. Existing Viewer reload/navigation retain their open-time snapshot;
a fresh Browser open receives the current order.

### Verification and measured limits

Focused fresh-process offscreen runs completed during this task:

- Sort/scanner/config/Settings/Browser window suites: **125 passed**.
- New `test_browser_unified_sort.py` plus the new real Browser-to-Viewer
  random snapshot integration case: **50 passed** (49 + 1).
- Existing capture/index/XButton/controller snapshot, sibling, sync and
  roundtrip selection: **40 passed**, 26 deselected.
- Browser navigation/watcher/page-count/chrome suites: **69 passed**.
- Unified selector plus chrome geometry at process scale 1/1.25/1.5/2:
  **2 passed at each scale** (8 executions).
- Existing Viewer navigation-target feedback: **13 passed**. Final syntax
  parsing passed for 15 affected Python files; `git diff --check` passed.

New coverage includes all persisted key/order pairs, malformed seeds, exact
activation/reset counts, Settings draft/cancel and unchanged Apply, Japanese
natural ties, extension grouping, unknown dates/sizes, unrated/folder partitions,
hash collisions, subsets/incremental batches, metadata/rating changes,
selection/minimal viewport preservation, scan metadata propagation and
timestamp-only refresh equality, fake Windows 3.11/3.12 stat shapes,
Back/Forward/refresh seed retention, and frozen Viewer order across reshuffle.

`scripts/benchmark_browser_random_sort.py` measures three-run medians using
prebuilt items and the production single-capture/two-snapshot open path:

| Items | Random sort | Full single-open capture |
| ---: | ---: | ---: |
| 1,000 | 11.500 ms | 18.431 ms |
| 10,000 | 133.564 ms | 190.440 ms |
| 50,000 | 742.460 ms | 1124.159 ms |

Each open uses one pipeline capture; no filesystem scan/decode occurs. Random
has O(n) temporary keys and O(n log n) sorting, with no retained random list or
additional cache authority. These are synthetic timings, not native latency or
a speedup claim; 50k capture still has a noticeable cost.

The required high-detail navigation smoke completed with nine 2400 x 3600 JPEG
pages, 1280 x 800 viewport and 256 MiB cache: cold immediate final 70.039 ms,
sequential 1.490 ms, reverse 1.638 ms, reversal 1.849 ms, ping-pong median
1.338 ms, rapid final 2.283 ms; terminal errors zero. No Viewer decoding or
presentation implementation changed in this task.

Runtime: bundled Python 3.12.14 / Qt/PySide6 6.11.2. Python 3.11 was unavailable;
its Windows stat shape was faked, not executed on a repaired runtime. Real
native UI responsiveness remains unverified. No new dependencies, real app
launch, native input, external GUI, commit, push or destructive git operation.

### License/copyright provenance

The combined directional catalog, persisted seed/path-hash process and
metadata-rename stability principle above are adapted from the fixed
`CatalogForm`/`ZipPlaInfo` files and methods explicitly listed in this section,
AGPL-3.0-or-later. NivisViewer correspondence is the Browser sort/catalog,
config/UI activation and metadata propagation code listed above, not a new
Viewer runtime port. Copyright notice **Copyright © 2016 Rio's Toolbox** and
license text remain in `licenses/ZipPlaFork/About.txt` and
`licenses/ZipPlaFork/AGPL.txt`; neither was changed or removed.

### Review correction: deterministic filter/rename test boundaries (2026-09-07)

The successful initial runs above did not establish stability of the new
`test_random_selection_filter_rename_and_open_time_snapshot` test. Independent
review observed **231 passed, 1 failed** across its initial groups and reproduced
the selection assertion failure in an isolated process. Instrumentation found
the selection was already empty before rating finalization, not lost by the
rename's path remapping.

The test had applied a filter, cleared it, reset the model and selected an item
before processing the filter's queued post-layout restoration. Its selected
random row could either survive or be excluded by the filter, making the later
selection assertion depend on seed/temp-path order. This was a test sequencing
defect; no settled production restoration defect was reproduced.

The corrected test explicitly selects a retained identity (`本110.jpg`) or an
excluded identity (`本80.jpg`) in two parameterized cases. A read-only observer
around the existing `_restore_list_view_state` records both synchronous and
queued calls: each filter operation processes Qt events and asserts the same
state's post-layout restore has completed before proceeding. Model reset also
settles before the next selection. The test asserts both filter-selection
contracts and the intended selected/current path immediately before finalizing
the rating rename. Post-rename selected identity, exact row and viewport-offset
assertions remain intact. No production code, timeout, sleep, skip or xfail was
added, and no post-rename assertion was weakened.

Fresh offscreen verification after correction: both cases passed together in
**10/10 separate repeated processes** (20 case executions), in addition to the
initial corrected two-case run. Unified-sort/sort/scanner/config suites passed
**119 tests** in another fresh process. Python 3.12.14 / PySide6 6.11.2 was used
with `-B` and pytest `no:cacheprovider`. Syntax and diff checks passed. This
follow-up changed only the focused test and this documentation; no real app,
native input, external GUI, commit, push or destructive git operation occurred.

## 44. Spread loupe, reading-direction slider and gesture stroke (2026-09-09)

### Fixed-source inspection and design choice

Reference: https://github.com/himamon/ZipPlaFork, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later.
Inspected the fixed `source/ZipPla/ViewerForm.cs`, class `ViewerForm`:
`MagnifierCanvas`, `bwMagnifierMaker_DoWork`, magnifier branch of `pbView_Paint`,
`GetMagnifierRectangle`, `MagnifierPhase2`, and `setSeekBarDirection`.
The magnifier worker prepares separate page canvases and assigns offsets in
one combined coordinate space. Binding direction determines horizontal
placement; unequal heights are centered. Paint crops each canvas using its
offset and fills uncovered regions with the Viewer background. Magnifier
readiness is fenced by the current page and worker completion. Seek-bar
`SameAsPage` projects right-to-left binding into `InversedSlider` without
changing logical page indexes.

Also inspected fixed `source/ZipPla/MouseGesture.cs`, class `MouseGesture`,
`GetPen` / `GestureBegin` and orbit drawing: rounded caps, antialiasing and
display-scaled stroke width. NivisViewer uses Qt logical coordinates, so it
must not apply the WinForms physical-pixel DPI multiplication a second time.

Four-way evaluation: **ZipPla** supplies the combined magnifier coordinate
space and direction projection; the previous **NivisViewer** per-page crop
and focused-page cancellation cannot represent a continuous spread. The
chosen **Hybrid** preserves NivisViewer's actual painted rectangles, modern
Qt DPR handling, source hydration, render workers, cache budget and generation
fences, adapting ZipPla's page-offset composition. A **New** monolithic bitmap
cache/worker or framebuffer-resize-per-pointer-move design was rejected: it
would duplicate ownership and repeatedly resample already known pixels.

### Repairs and retained contracts

`ViewerWindow._refresh_view` formerly cancelled a loupe whose source page was
not `model.focused_index`, even if that page remained in the displayed spread.
Source hydration calls this path, explaining the left/non-focused-page failure.
It now tests displayed-spread membership. `ViewerWidget` snapshots actual
painted rectangles for both pages and maps the pointer crop across their union,
including gutter/background, not just the originally hit image. Worker-built
per-page magnifier artifacts are drawn at translated positions at their native
DPR; pointer movement only updates the crop/translation. No navigation,
filesystem access, decode or worker resize is introduced per pointer move.

The existing `_magnifier_artifact_cache`, `ViewerRenderTask` and coordinator
are reused. The spread dictionaries are current-session key/paint bindings,
not another cache. Aggregate enlarged page pixels use the existing 32-Mpixel
artifact ceiling (zoom is limited only when required by that ceiling); retained
unique pixmaps, including active bindings, count against the existing 160-MiB
cache ceiling. Queued obsolete keys are removed and old completions cannot
activate a cancelled/new display. Preview or retained display pixmaps are
resized on the worker as a coherent two-page fallback while existing source
hydration runs; the normal spread/selection remains until both artifacts exist.
Full-source replacements keep the same displayed placement. PDF target sizes
are retained for both visible pages, with resume after prepared-frame commit.
Single-page crop behavior is unchanged.

`ViewerPageSlider.set_reading_direction` pins local layout direction to LTR
and sets both `invertedAppearance` and `invertedControls` for RTL. Logical
indices, wheel commands and requested-versus-committed state are unchanged.
Viewer startup and live direction actions update this one slider, also used
by fullscreen chrome. No overlay or spinner was added.

The previous Browser/Viewer stroke consisted of separate translucent lines:
overlapping round endpoints brighten the sampled joins, while antialiasing
was not enabled and logical pen width was multiplied by DPR. Both now call
`app/gesture_trail.py:draw_gesture_trail`, one antialiased path with round
caps/joins and a 3-logical-pixel pen, retaining the previous color/alpha.
Recognition, thresholds, commands and clearing remain with their owners.
The existing `mouse_gesture_show_trail` setting is reused, default unchanged;
its Mouse Settings label now says Viewer/Browser shared. It stays enabled
when Viewer recognition is disabled. A visibility-only change clears/repaints
the trail without cancelling active recognition on either surface. Existing
ConfigManager signals provide immediate application and persistence.

### Provenance and verification

The page-offset composition and direction/rounded antialiased stroke principles
above are adapted from the listed fixed source methods, not a line-for-line
WinForms port. NivisViewer mappings are `app/viewer_widget.py` spread magnifier
methods, `app/viewer_window.py` hydration membership/slider wiring,
`app/viewer_page_slider.py:set_reading_direction` and
`app/gesture_trail.py:draw_gesture_trail` with `ExplorerListView.paintEvent`.
AGPL-3.0-or-later attribution applies. Existing license and notices are retained
in `licenses/ZipPlaFork/AGPL.txt` and `licenses/ZipPlaFork/About.txt`, including
`Copyright © 2016 Rio's Toolbox` and the bundled third-party notices. No new
dependency or license was added.

Python 3.11.9 x64 / PySide6 6.11.2, process-local clean PATH and
`QT_QPA_PLATFORM=offscreen`: new focused tests plus existing magnifier tests
**253 passed**; selected existing slider/navigation-feedback/gesture/settings
tests **29 passed**. Coverage includes asymmetric page pixels, both entry sides,
LTR/RTL, unequal pages, rotation 0/90/180/270, zoom/pan, full/preview/pixmap tiers,
100/125/150/200% DPR, gutter pixels, no per-move work, stale rejection, real ZIP
hydration, slider endpoints/intermediate/groove/drag/keys, pending feedback,
shared fullscreen slider, sparse trail alpha continuity, active recognition
with trail off, and real Settings persistence/live Browser/Viewer application.
Seven changed Python files passed syntax parsing; diff whitespace was checked.

One bounded high-detail offscreen smoke used nine 2400 x 3600 JPEG pages,
1280 x 800 viewport and 256-MiB cache. Request-to-paint: sequential 0.995 ms,
reverse 0.951 ms, reversal 1.239 ms, ping-pong median 0.931 ms, rapid final
1.562 ms; terminal errors zero. These are synthetic single-run observations,
not native responsiveness claims. No real interactive app, native input,
external GUI, portable rebuild, commit or push ran. Real monitor/input-device
visual behavior still requires user observation; offscreen DPR tests do not
verify the native compositor.

### 44.1 Review repair: live fallback policy and PDF zoom targets

The independent review reproduced an early-return defect in the new spread
fallback branch: any existing page key bypassed full policy/size validation.
Two focused tests reproduced this before the repair: changing the explicit
upscale algorithm queued **zero** replacement jobs, and increasing PDF loupe
zoom left its promotion count at **two**, not four (one upgrade per page).

Fallback identity now uses the retained painted QPixmap cache key, then builds
the complete algorithm/size/DPR render key before consulting the existing
artifact cache and pending jobs. `toImage()` is deferred until an actual miss;
there is no additional retained source cache. Live bilinear changes build both
required artifacts; cursor-only updates still perform no decode/resize work.
Zoom changes rebuild both target sizes while retaining source identity.

Promotion bookkeeping now retains the largest requested QSize per page within
the existing loupe session. PDF requests upgrade only when a dimension grows;
smaller/repeated targets, algorithm-only changes, source-ready callbacks and
pointer moves do not re-request it. Raster promotion remains once per page
because it hydrates the full source independently of magnifier target size.
Cancellation clears this session bookkeeping as before. These are local
correctness repairs, with no additional ZipPla port or license/dependency;
section 44's fixed-revision provenance and architecture remain unchanged.

Changed in this review pass: `app/viewer_widget.py`,
`tests/test_spread_loupe_slider_trail.py`, and this document only. Source-tier
tests assert both algorithm-bearing keys, replacement pixmaps and full RGBA
pixel-byte agreement with the submitted source rendered under the requested
policy, unchanged geometry, bounded jobs, raster hydration deduplication and
PDF target upgrades. New/existing loupe suites: **255 passed**; the two new
regressions also passed in **3/3 fresh processes**. Verified runtime was Python
**3.11.9 x64**, PySide6 **6.11.2**, offscreen with process-local PATH. Syntax and
diff checks passed. The bounded nine-page 2400 x 3600 high-detail navigation
smoke completed with zero terminal errors: sequential 0.967 ms, reverse
0.932 ms, reversal 1.249 ms, ping-pong median 0.954 ms, rapid final 1.605 ms.
These are synthetic observations, not a native-performance claim. No real app,
native input, external GUI, portable rebuild, commit or push was performed.


## 45. Independent background for files without thumbnails (2026-09-09)

Fixed source inspected: https://github.com/himamon/ZipPlaFork revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later;
`source/ZipPla/CatalogForm.cs`, `CatalogForm.setColors`,
`tvCatalog_ThumbnailPaint`, `drawFileImage`, `drawFileIconImage` and nested
`ThumbViewer.DrawItem`. `setColors` sets the Catalog background to black.
`DrawItem` fills the entire thumbnail canvas with `BackColorBrush` before
drawing the image and raising the thumbnail-paint callback. FileNotFound,
LoadError and NotYet enter the fallback branch. At this fixed revision
`drawFileImage` immediately returns: its comment explains that an incomplete
internal load state could otherwise cause an icon to overlap an actual image.
The lower-left associated icon is handled separately in the completed branch.
Thus the effective missing-file default is the shared black canvas, not a
separate rounded card or an actively drawn large file icon.

Four-way choice: ZipPla's shared black canvas is the reference; NivisViewer
already has a correctly aligned placeholder helper and useful centered icons,
but previously enabled its fill only for folders or errors. The chosen Hybrid
extends that existing helper to every item without a valid QImage, retaining
NivisViewer icons, framing and overlays. A new renderer, preview-state store
or background cache was unnecessary and was not introduced.

`BrowserItemDelegate._uses_placeholder_canvas` now tests actual image
availability, not file kind/error alone. Unsupported/no-preview, pending,
unrequested/cancelled, null-image and failed file previews use the same neutral
fallback. Successful QImages take precedence, including when an error flag is
stale; existing asynchronous generation guards are unchanged. Broken archives
use the file color through this same path, not a competing archive renderer.
Folder colors remain independent. The default for both is black `#000000`.
The fill is exactly `thumbnail_content_rect`: the existing real-image maximum
target, physically snapped at DPR, with no extra placeholder inset. The
centered icon remains constrained to half each axis; associated icon alpha,
rating/error/selection/hover overlays and the removed lower-left plate are
unchanged. This change adds no image decode, association lookup, thumbnail
request or filesystem scan to painting.

Settings -> Browser -> サムネイル now places
`ファイルの代替サムネイル背景` beside `フォルダの代替サムネイル背景`.
The independent `browser_file_fallback_background` stores `auto` or normalized
`#rrggbb`; missing legacy keys and invalid values become `auto`. Shared
normalization and color-picker/reset/button helpers preserve folder behavior.
The explicit `既定に戻す` button resets the staged value to `auto`;
Apply/OK persists and repaints open Browser views immediately. Cancel discards
unapplied changes, including an unapplied reset; repeated reset remains valid.
Color-only configuration does not invalidate display surfaces, decoded caches,
provider generation, scheduling or directory generations.

Provenance mapping: the fixed Catalog canvas/default and not-yet/error process
inform `app/browser_item_delegate.py:_uses_placeholder_canvas` /
`_paint_placeholder_canvas`. This is an adaptation of the already documented
section 24.3.1.1 authority, not a new WinForms code port. AGPL-3.0-or-later
provenance and notices remain in `licenses/ZipPlaFork/AGPL.txt`,
`licenses/ZipPlaFork/About.txt` (Copyright © 2016 Rio's Toolbox and bundled
third-party notices) and `THIRD_PARTY_NOTICES.md`. No dependency was added.

Verification: Python 3.11.9 x64 / PySide6 6.11.2, offscreen. New file-fallback
suite **133 passed**; existing fallback/placeholder/badge/config/settings
selection **27 passed**. Pixel checks cover four file kinds, unsupported,
pending, cancelled and failed states, success transitions with stale error,
centered icon visibility, bounded fill, fit/center-crop, compact/standard
density and 100/125/150/200% DPR. Settings tests use the actual Browser tab,
fake color picker and thumbnail provider, a visible model item, persisted
custom/default/legacy/invalid values, Cancel, repeated reset, independent
folder color and repaint with zero new requests/scans/generation changes.
The broken-archive regression now selects the independent file color; its
shared-geometry pixel assertions are retained. Six Python files passed syntax
parsing; diff checks passed. One required bounded nine-page 2400 x 3600
high-detail ZIP smoke completed with zero errors: sequential 1.065 ms, reverse
0.961 ms, reversal 1.320 ms, ping-pong median 1.015 ms, rapid final 1.683 ms.
These are synthetic observations, not native performance/visual verification.
No real app, native input, external GUI, portable rebuild, commit or push ran.

## 46. Automatic spatial clicks and RAR diagnosis (2026-09-09)

### 46.1 Implemented option; no RAR optimization implemented

Settings → Viewer → ページ表示 → 左右クリックのページ送り方向 now offers
`綴じ方向に合わせる（自動）` (`viewer_canvas_click_direction="auto"`).
The existing ConfigManager key, Settings draft/Apply/Cancel flow and
ViewerWindow._move_from_canvas_side authority are reused. At the confirmed
left-button spatial click, `ViewerWindow.reading_direction` resolves RTL to
left=next/right=previous and LTR to right=next/left=previous. This is the same
effective field used by spread rendering, model options and the page slider;
set_reading_direction changes the next click immediately. There is currently
no separate per-book reading-direction override in BookSession/MetadataStore
to apply or migrate. UI locale is not consulted.

`right_next` and `left_next` remain fixed physical-side choices. New profiles
and missing/invalid values use `auto`, while an existing saved fixed value is
preserved; no private preference is silently rewritten. Existing single-page/display-unit actions still use
PageNavigationController and the existing navigation policy. Pointer gesture,
pan, loupe, overlay and context-token handling are unchanged; this option
does not remap the right mouse button, wheel, keys or XButtons.

### 46.2 Actual backend and pipeline inspection

The readable repository config selects `auto` with empty WinRAR/7-Zip paths.
Actual automatic registry discovery on a disposable RAR selected
`WinRARBackend`, `C:\Program Files\WinRAR\UnRAR.exe`, **7.13 x64**.
The installed Rar.exe console encoder and UnRAR.exe report file version
7.13.0. No tool was installed. This identifies this environment's discovery,
not an uninspected running portable profile or the user's archive format.
ArchiveBackendRegistry._select_backend respects explicit preference/paths and
7-Zip file association; auto supports one safe failover and remembers the
successful backend per archive.

`WinRARBackend.list_entries` executes `lb -scfr -- archive`; read_entry executes
`p -inul -- archive entry`. WinRARProcessRunner inherits SevenZipProcessRunner:
each run creates a **new console subprocess**, despite retaining the runner
object. Stdout is fully collected before returning image bytes, with bounded
output, timeout and cancellation. `_collect_output` polls process completion
every 20 ms, so measured extraction includes startup, output copying and
completion-observation delay, not just RAR decompression. Cancellation stops
the owned process; it cannot retain its decompressor state for later reuse.
The 7-Zip CLI alternative also launches a process per extraction; merely
selecting it does not create a persistent archive session.

`SevenZipImageSource` is the shared external-archive source even for WinRAR.
Its constructor lists once, keeps entry identities/listing_snapshot, and
fork_for_thumbnail reuses that snapshot. There is **no full relisting per
page**. open_image calls read_entry, then Pillow Image.open/EXIF transpose/copy
on the entire image. It does not implement the target-JPEG decode shortcut.
ImageCache's worker then converts to QImage; ViewerWidget's preparation worker
resizes/prepares the frame before GUI publication. There is no new GUI-thread
decode path in this task.

BookSession._replace_viewer_runtime selects ZipRasterBookRuntime for ZIP and
FolderRasterBookRuntime for folders, but not for the external RAR source.
RAR remains on ImageCache plus Viewer prepared frames, with decoded caching
and bounded current-first read-ahead. It is not uncached and does not lack
prefetch. Obsolete queued work is removed/cancelled through existing cache
and source cancellation, with generation/request guards; wanted in-flight
cache work can be reused, but cancelled extraction is not a retained session.
The legacy ViewerWindow._queue_decode_demand still starts its **16 ms** timer
after an existing frame, including a discrete cold navigation. ZIP uses its
newer runtime admission policy instead; the mere existence of the timer in
a ZIP window is not evidence that ZIP waits on it.

For non-solid RAR there is no reason to infer whole-prefix decompression on
every page, although archive/process reopening is certain. For solid RAR,
separate extraction processes cannot share dictionary/reader progress and
may repeat preceding data within the solid block. WinRAR's bare listing
currently reports `solid=None`, even for the known solid fixture. We cannot
classify the user's RAR as solid. Password-protected, missing-volume,
multivolume, damaged and RAR4 cases were not benchmarked; all existing backend
error/size/cancel limits must survive any future work.

### 46.3 Real CLI/offscreen measurements, not mocks

`scripts/diagnose_rar_navigation.py` creates six identical seeded high-detail
2400 × 3600 JPEG payloads in ZIP, RAR5 non-solid (`-s-`) and RAR5 solid (`-s`)
archives in a TemporaryDirectory. It uses installed console Rar/UnRAR,
production sources and the actual offscreen Viewer, 1280 × 800 viewport and
256 MiB setting. The synthetic archives are about 27.4–27.6 MB. A separate
auto-discovery probe confirms the backend above; Viewer timings explicitly
inject that same UnRAR backend for a controlled comparison.

Windows, Python **3.11.9**, Pillow **12.3.0**, PySide6/Qt **6.11.2**;
high-resolution perf_counter. Representative single run, milliseconds:

| Observation | ZIP | Non-solid RAR5 | Solid RAR5 |
| --- | ---: | ---: | ---: |
| Open request → first paint | 60.703 | 193.503 | 214.227 |
| Forward to page 1 / 2 | 52.453 / 51.017 | 149.442 / 148.695 | 169.621 / 167.904 |
| Cold jump to page 5 | 61.920 | 164.313 | 209.061 |
| Reverse to uncached page 4 | 52.035 | 167.458 | 192.618 |
| Cached backtrack to page 1 | 0.638 | 0.738 | 0.786 |
| Cached page 2 / 1 | 0.538 / 0.732 | 0.530 / 0.447 | 0.484 / 0.423 |

Each RAR Viewer session listed once and completed five image extractions
(pages 0,1,2,5,4); all three cached return legs completed **zero** extractions.
Successful Viewer extraction durations were 63.766–84.987 ms non-solid and
63.584–105.834 ms solid. Standalone component probes measured listing
45.186/44.677 ms; non-solid extraction 64.330–66.173 ms, solid
63.773–105.078 ms, and Pillow full decode/transpose/copy 43.268–44.817 ms.
Solid timings are not strictly monotonic; this small JPEG fixture is not a
claim about every solid dictionary, archive size or storage device.

The component probes are separate from concurrent Viewer timings. Native
process startup versus decompression, QImage conversion versus resize,
publication versus Qt paint, cancelled extraction count and RAR rapid-burst
latency were **not individually measured**. Do not subtract unrelated probes
and label the remainder as resize time. All paint timings are offscreen,
not Windows compositor latency. Files were just generated and component
probes precede Viewer runs: "cold" means application-cache miss, **not a cold
OS/storage cache**. No private archive or real interactive application was used.

### 46.4 Fixed ZipPlaFork comparison and provenance

Reference: https://github.com/himamon/ZipPlaFork at
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, **AGPL-3.0-or-later**.
The fixed local source `source/ZipPla/PackedImageLoader.cs`, class
PackedImageLoader, was inspected directly:

- RAR5 dispatch uses getSevenZipArchiveEntry / getSevenZipArchiveEntry_Base
  (around 879, 1454–1490). It retains the in-process ArchiveFile and Entry[]
  rather than launching an extraction executable per page.
- Ordinary RAR can use the SharpCompress archive route or configured SevenZip
  route. OpenInnerImageStreamSharpCompress (1906 onward) uses entry streams
  for non-solid; its solid fallback retains ExtractAllEntries()/iReader,
  advances physical entries and caches decoded bitmaps for reuse/Clone.
- GetInnerImageStreamSevenZip (2044 onward) extracts a retained Entry to
  MemoryStream, optionally via SevenZipEntryExtractInTask. Its comment says
  7z has its own cache. That is source intent, not measured proof of speed
  for every solid archive. Dispose releases the retained archive/resources.

This task adopts **no RAR source code or optimization**. The auto-click option
is NivisViewer-owned logic, not a port. This section records analysis provenance;
existing copyright/license notices remain in licenses/ZipPlaFork/About.txt
(© 2016 Rio's Toolbox), AGPL.txt and THIRD_PARTY_NOTICES. No dependency added.

| Alternative | Benefit and cost | Assessment |
| --- | --- | --- |
| ZipPla retained library/session | Removes per-page process startup; solid reader progress can be reused. Native/library lifetime, thread safety, crash isolation, format compatibility and licensing require review; bitmap retention must be bounded. | Useful structural reference, not a literal drop-in. |
| Current Nivis CLI + decoded/prepared caches | Strong subprocess isolation and cancellation, bounded stdout and working cached turns. Repeated cold extraction/full decode and legacy admission remain. | Preserve safety/identity/commit authorities. |
| Hybrid existing source/cache/UI with improved admission, target decode and bounded extraction/session reuse | Can keep existing UX, generation guards and backend fallback while addressing measured stages. Memory/disk budgets and source lifetime must be explicit. | Preferred incremental direction. |
| New full-archive upfront extraction | Amortizes solid decompression and enables folder-like access, but startup, disk quota, privacy cleanup, cancellation and huge archives become expensive. | Do not make this the default or rewrite user archives. |

### 46.5 Smallest proposed next steps (not implemented)

1. First reuse the existing input-kind admission policy for external raster
   discrete misses instead of the unconditional 16 ms wait; retain wheel
   latest-wins coalescing and started-work cancellation. This removes a known
   admission cost, not the measured extraction/decode cost. Instrument process
   exit/completion separately before changing the 20 ms worker polling; an
   event-driven completion wake must retain responsive cancellation/timeouts.
2. For a material non-solid cold-page improvement, implement target-aware JPEG
   decode from the already-extracted bytes through the existing ImageCache
   source hook, preserving full-resolution zoom/loupe fallback, EXIF and
   original dimensions. It reduces decode/copy/resize work and RAM, but does
   not remove extraction startup. A bounded compressed-payload cache helps
   re-decodes after frame eviction, not the first-ever page miss; merely
   increasing decoded cache does not fix the cold timings above.
3. For demonstrated large solid archives, prototype one sequential extraction
   session or bounded temporary extraction reuse behind the existing source
   authority. Only completed entries may publish; enforce byte/disk quotas,
   cleanup and book-generation cancellation. This can amortize repeated solid
   prefix work but trades startup/disk writes or memory and may conflict with
   rapid far-jump latency. A retained native library is a larger alternative
   requiring license/security/crash review, not an automatic recommendation.

Acceptance: same JPEG payloads in ZIP/non-solid/solid, open/forward/reverse/
backtrack/rapid final-target traces, multiple runs with median/p95, explicit
application-cold versus OS-cold labels, listing/process/bytes/decode counters,
and stage timing. Require immediate discrete admission without speculative
work blocking it, zero extraction on retained cache hits, bounded memory/temp
usage, stale-result rejection on direction/book changes, and unchanged output
pixels/EXIF/spread/history/loupe. Include malformed, encrypted and missing-volume
fixtures before changing the backend contract. Do not claim ZipPla speed parity
without running a comparable permitted native benchmark.

### 46.6 Focused verification

The new auto-click suite passed **18 tests**: config roundtrip/default/invalid,
real Settings Cancel/Apply and live Viewer binding, both fixed modes, RTL/LTR
changes, both regions, single/spread, single-page/display-unit steps,
fullscreen, bounds, blocked ZIP/folder pending history, modifier/drag/drop and
fullscreen-edge guards. Existing loupe/right-drag gesture and XButton tests
passed **10 tests**, including unchanged wheel routing. No per-book override
test was invented because that authority does not currently exist. Syntax
parsing passed for the three production files, auto-click test and diagnostic
script; `git diff --check` passed.

The required bounded navigation smoke already ran in this turn: nine synthetic
2400 × 3600 JPEGs, 41,472,435-byte ZIP, 1280 × 800, 256 MiB. Cold burst final
66.212 ms, forward 0.905 ms, reverse 0.901 ms, reversal 1.197 ms, ping-pong
median 0.904 ms, rapid final 1.597 ms; final-only cold commit, zero terminal
errors. This smoke was not rerun as an unrelated historical task. No real app,
native input, external GUI, portable rebuild, commit or push was performed.

## 47. Authorized external-raster admission, JPEG tiers and extraction reuse (2026-09-09)

This implementation supersedes section 46's diagnosis-only boundary following
explicit approval of all three improvements. Earlier work, including automatic
spatial clicks, is preserved. This is not a replay of the ZIP/fullscreen task.

### 47.1 Architecture and stage-specific changes

**Stage 1 — use the existing raster authority.** BookSession now constructs the
existing source-agnostic RasterBookRuntime for SevenZipImageSource (the shared
WinRAR/7-Zip external source). ViewerWindow admits it through the same request,
presentation, topology, memory and page-list paths as ZIP/folder. The previous
ImageCache/prepared-frame route is suspended for these books, not run alongside
the raster runtime. There is no additional extraction scheduler, thread pool
implementation, navigation history or source authority.

This makes discrete/initial-key misses immediate via NavigationAdmissionPolicy,
retains cadence-derived wheel staging and key/slider release boundaries,
adopts a wanted started job, cancels unrelated speculation and releases only
the latest final target. Cached intermediate presentation stays separate from
cold dispatch. The old 16 ms decode-demand timer still exists for legacy paths
but is inactive for measured RAR navigation. The 20 ms backend process polling
was **not changed**: its contribution is not confused with pure decompression.
First display has no preceding whole-archive extraction; normal runtime
preparation proceeds only after the current unit under its bounded memory
policy. One current-first worker owns source decode/render. Existing history
side effects now follow the shared accepted-frame/paint gate rather than
external-source open completion.

**Stage 2 — reuse native JPEG tiers.** SevenZipImageSource implements the existing
open_compatible_jpeg_at_most/open_qimage_at_most, estimate and header-probe hooks.
It uses the established Pillow/libjpeg draft helper (native 1/2, 1/4, 1/8 or
full tier), EXIF transform and detached QImage conversion on the worker. The
shared renderer performs final resize/rotation. Original oriented dimensions
remain independent from preview dimensions; the existing source requirement
and frame keys prevent preview/full aliasing. Manual zoom/loupe request full
sources, while viewport/spread/DPR determine normal preview requirements.
Non-JPEG uses the existing Pillow fallback; PDF does not enter this runtime.
Malformed declared JPEGs fail without a second fallback extraction.

**Stage 3 — bounded completed-payload reuse.** The external source retains
successful compressed image bytes in its own LRU, keyed by existing entry
identity. Header-to-decode, preview-to-full, different display tiers and
decoded-frame eviction/revisit can reuse extraction output. This is an
extraction-result cache, **not a persistent CLI/decompressor session**. Each
first miss or evicted payload still starts the selected backend's process;
new solid entries may still repeat earlier solid-block work. No undocumented
batch stdout splitting, native DLL dependency or temporary extraction is used.

BookSession reserves `min(64 MiB, configured hard cache budget / 8)` for these
payloads and deducts it from raster hard/soft budgets. At 256 MiB, this is
32 MiB payloads plus 224 MiB raster-cache allowance. Runtime memory-pressure
sampling includes retained payload bytes. Smaller settings evict immediately;
oversized entries are not retained. This bounds resident **cache** storage,
not all process RSS: one active extraction/decode and existing protected-current
oversize policy remain separately bounded by existing entry/runtime contracts.
Standalone/thumbnail forks have zero payload allowance; no hidden per-fork
retention or duplicate full-directory scan is introduced.

Payload access uses short metadata locks, never a lock held across subprocess
I/O or decoding. The shared runtime already serializes wanted book work and
can adopt it. Unwanted backend requests retain cooperative cancellation and
timeouts; completed-but-cancelled output cannot enter the LRU. Backend success,
nonempty/bounded output and exact known entry size are checked before retention;
invalid image data is removed when decoding/header validation fails. Original
backend error codes propagate. Source close clears retained bytes and cancels
active reads; no production disk artifacts exist, hence no crash-residue
cleanup or path-traversal extraction surface is introduced.

Archive identity uses device/inode/size/mtime/ctime checked around extraction,
before navigation cache reuse and before frame publication. Replacement causes
source invalidation, cancellation, artifact clearing and an explicit reopen
message, not stale publication. Recognized `.partNN.rar` and legacy `.r00`
multipart layouts disable payload retention: the first-volume stat cannot
validate all volumes. Their selected backend/extraction behavior is retained.
Solidity remains the listing authority's true/false/unknown value; no guessed
classification or new metadata scan is added.

### 47.2 Four-way comparison and provenance

The fixed PackedImageLoader.cs source was reread: getSevenZipArchiveEntry_Base
retains ArchiveFile/Entry[]; GetInnerImageStreamSevenZip extracts from retained
entries; OpenInnerImageStreamSharpCompress keeps the solid ExtractAllEntries
reader and bitmap reuse; Dispose ends their lifetimes. The useful principle
is book-scoped reuse of completed expensive work, not literal WinForms or
unbounded bitmap retention.

| Choice | Decision |
| --- | --- |
| ZipPla retained DLL/archive/sequential reader | Strong reuse, but importing a library API changes crash containment, threading, formats and dependency/license obligations. Not needed for the measured completed-entry reuse benefit. |
| Former Nivis CLI + legacy rendering | Keeps subprocess safety but lacks the shared admission/tier path. Keeping both schedulers would duplicate policy. Retired for external raster main display. |
| Hybrid existing raster runtime + selected CLI + source-owned bounded payload reuse | Chosen. Preserves first-target priority, cancellation and format fallback, reuses proven tiers/cache/commit logic, avoids repeated extraction for retained entries. First new-entry process costs remain explicit. |
| New sequential/temp whole-archive extraction | Could amortize solid prefix work across different entries, but adds disk quota/lifetime/crash cleanup and out-of-order jump contention. Not selected as the default; no first-image wait for it. |

Reference repository https://github.com/himamon/ZipPlaFork, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, **AGPL-3.0-or-later**.
Source: `source/ZipPla/PackedImageLoader.cs`, class PackedImageLoader,
getSevenZipArchiveEntry_Base (~1468), OpenInnerImageStreamSharpCompress (~1906),
GetInnerImageStreamSevenZip (~2044), retained archive/reader/entry lifetimes and
Dispose. Nivis correspondence: BookSession._replace_viewer_runtime /
_source_runtime_limits, SevenZipImageSource._read_payload / close / JPEG hooks,
ViewerWindow's existing raster request and publication path. The new LRU is
Nivis code implementing the compared reuse principle, not a copied native
archive session. Shared raster/decoder provenance remains documented in earlier
sections. Copyright © 2016 Rio's Toolbox and license text remain in
licenses/ZipPlaFork/About.txt and AGPL.txt. No dependency added or installed.

### 47.3 Measurements and limits

Same six seeded high-detail 2400 × 3600 JPEGs, 1280 × 800 viewport, 256 MiB,
Windows / Python 3.11.9 / Pillow 12.3.0 / Qt and PySide6 6.11.2;
UnRAR **7.13 x64** automatically discovered at the path recorded in section 46.
RAR5 fixtures use explicit `-s-` and `-s`; production bare listings still report
unknown solidity. Warm filesystem cache, initially empty application caches.

Section 46's **single-run** before values versus medians of **three fresh-process**
after runs, milliseconds (not a statistical parity/speedup guarantee):

| Measurement | Non-solid before | Non-solid after | Solid before | Solid after |
| --- | ---: | ---: | ---: | ---: |
| First paint | 193.503 | 174.200 | 214.227 | 155.872 |
| Forward page 1 | 149.442 | 106.690 | 169.621 | 126.562 |
| Forward page 2 | 148.695 | 107.349 | 167.904 | 127.926 |
| Cold far jump | 164.313 | 112.798 | 209.061 | 152.377 |
| Cold reverse | 167.458 | 107.206 | 192.618 | 128.235 |

Observed first-paint maxima were 194.221 ms non-solid and 172.838 ms solid;
cold-jump maxima 114.344/154.552 ms. Three samples do not support a meaningful
p95 claim. Cached returns remained approximately 0.5–1.2 ms with zero new
extractions. Each measured session listed once and completed five entry reads
for pages 0,1,2,5,4; cancelled speculative attempts are recorded separately.
Payload retention at the snapshot was 23,062,520 bytes, under the 32 MiB reserve.

The controlled Stage-3 probe decodes the same actual entry at preview/full/
larger-preview tiers with retention disabled versus enabled. Median elapsed
time fell **312.737 → 179.731 ms** (non-solid) and **316.310 → 181.296 ms**
(solid). Extraction subprocesses fell **3 → 1**, retaining one 4,613,096-byte
payload instead of extracting 13,839,288 bytes over the three calls. This
isolates real extraction-result reuse; it does not claim that reading three
different cold entries shares a decompressor.

Observational wrappers around the real JPEG helper and render_qimage measured
approximately 32.3–34.8 ms for native-tier decode/EXIF/QImage and 6.6–8.9 ms for
RAR display resize/rotation. Backend read timing continues to include process
startup, full stdout collection and 20 ms completion polling. These are directly
timed phases, not unrelated measurements subtracted from total latency.
Process startup/decompression and GUI upload/publication cost are not isolated.
Publication order/count is measured from production commit signals; paint is
offscreen Qt, not Windows compositor timing.

Fresh-epoch rapid wheel bursts committed **only final page 5** for ZIP and both
RAR fixtures. Median final paint was 54.813 ms ZIP, 113.947 ms non-solid,
153.636 ms solid (observed maxima 55.220/130.340/153.699). Final entry 5 was
admitted before post-commit warmup; entries shown as `started` after final
paint are not transit commits. Fake event-gated tests separately prove wanted
started-job adoption and cancellation with no cold page-2/3 transit extraction.
RAR→ZIP book switches painted in 60.5–63.5 ms; first ZIP→RAR use in these
sessions was 195.2–217.8 ms and also pays lazy backend discovery. These are
different directions, not interchangeable book-switch comparisons.

Real CLI safety fixtures: complete RAR5 multipart decoded correctly with payload
retention disabled; missing volume and encrypted-header archives failed, and
corrupt input did not open. Existing UnRAR mappings are coarse: missing volume
and encrypted-header fixtures reported `corrupt_archive`, plain corrupt bytes
reported `no_images`. These mappings were observed and **not silently fixed or
claimed precise** by this performance change. Unit tests separately preserve
structured password/corrupt/timeout/not-found propagation, empty listings and
oversized/incomplete output rejection. No user archive was inspected.

An additional attempt to isolate GUI completion timing was blocked by execution
review, even after the current request's console-tool authorization was reread.
No workaround was used and the unverified wrapper was removed. Further console
runs require direct approval; earlier completed CLI results above remain valid.
Native interactive performance, huge solid archives and arbitrary volume layouts
remain unverified. No claim of ZipPla performance parity is made.

### 47.4 Verification and changed files

Focused fresh-process results: **29** new RAR pipeline tests; **7** existing
external Viewer tests; **65** source/thumbnail/WinRAR/7-Zip/registry/error tests;
**66** shared BookSession/ZIP/folder runtime tests; **224** auto-click and
spread-loupe/slider/trail tests. The RAR tests include all eight EXIF orientations
with quadrant pixel checks, preview/full extraction reuse, LRU/budget/oversize,
close/cancel/replacement races, multipart retention policy, final-target-first
adoption/cancellation, both spread directions, real offscreen loupe full-source
upgrade, book switch, and 100/125/150/200% DPR plus rotation/manual zoom.

The old external fixtures' made-up 100-byte metadata was replaced with actual
fixture lengths to satisfy complete-entry validation. One history test now waits
for the existing accepted-frame/paint gate before asserting exactly one record;
its no-reopen/no-relist/no-position-restore assertions remain. No skip/xfail or
timing sleep was added to hide failures.

Required large-image ZIP smoke: nine seeded 2400 × 3600 JPEGs, 1280 × 800,
256 MiB, zero terminal errors; forward 1.081 ms, reverse 1.021 ms, reversal
1.262 ms, ping-pong median 0.934 ms, rapid final 1.640 ms. One cancelled old
result was rejected. The same ZIP diagnostic's application-cold legs remained
48.8–54.6 ms, near the section-46 baseline.

Changed for this authorized task: app/book_session.py, app/image_source.py,
app/viewer_window.py, scripts/diagnose_rar_navigation.py,
tests/test_rar_raster_pipeline.py, tests/test_external_archive_image_source.py,
tests/test_external_archive_viewer.py, tests/test_external_archive_thumbnail.py,
and this comparison document. Syntax parsing passed for all eight Python files;
`git diff --check` passed. Existing unrelated dirty/untracked work remains.
No interactive real application, native input, external GUI, dependency install,
portable rebuild, commit or push was performed. Console tools operated only on
disposable fixtures. All production extraction remains in memory; no user image
folder receives configuration, cache or extraction files.

## 48. Thumbnail cache resolution versus display scaling (2026-09-10)

### 48.1 Fixed-revision source and provenance

Read directly from https://github.com/himamon/ZipPlaFork at revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`:

- `source/ZipPla/GPSizeThumbnail.cs`: constants near 95; image `TryGet` near
  390; image `TrySet` near 796, full-original sentinel near 1013;
  `GetResizedInfo` near 1203.
- `source/ZipPla/CatalogForm.cs`: `sizeProvider` near 5255,
  `GetImageThumbnail` near 5343, `DoJustClipping` near 5534/5538,
  `GetArchiveThumbnail` near 5762 (cache read near 5853, write near 6154,
  normal display clipping near 6271).

License: **AGPL-3.0-or-later**; copyright notice: Copyright (C) 2016 Rio's
Toolbox. Existing license/third-party notices remain retained. This follow-up
is source inspection and explanation, not a new code translation/port. The
NivisViewer counterpart is the existing `ThumbnailRenderPolicy`/render spec,
provider and disk cache. No resolution/upscaling policy was changed here.

### 48.2 What the cache actually stores and selects

ZipPla stores multiple JPEG XR thumbnail levels, usually smaller than the source
but deliberately larger than their intended display. `SIZE_MARGIN` is sqrt(2),
`COMMON_RATIO` is 2, and `AREA_UBOUND` is 160 x 120. `GetResizedInfo` chooses
the fit/fill controlling dimension with that margin, caps at original dimensions,
then builds successively halved levels. The area floor limits the smaller levels.
For low-load sources when full-size caching is disabled, near-original requests
can decline caching rather than save an almost-original copy. `TrySet` resizes
largest to smallest and stores the entries in smallest-to-largest lookup order.
JPEG XR quality 30 is not numerically equivalent to WebP quality 30 or 60.

`TryGet` validates source/index metadata and multiplies requested dimensions by
sqrt(2). It skips undersized levels and decodes the first acceptable one. For
letterbox, width OR height satisfying the requirement is sufficient (aspect-fit
controlling dimension); crop requires both. If no level qualifies, it reports a
miss. Catalog's image/archive paths then load the source/cover and regenerate.
It does not ordinarily take an insufficient reduced thumbnail from a large
original and accept permanent enlargement as a sufficient cache hit.

Important exception: when a saved level is the full original, `TrySet` writes
`uint.MaxValue` width/height markers. This original-detail ceiling satisfies later
requests even if the display grows; no higher source detail exists. It does not
create a cache bitmap upscaled beyond the original's dimensions.

### 48.3 Display can scale independently

Catalog's normal display path calls `DoJustClipping(..., needToResize: true)`.
`sizeProvider` and `Graphics.DrawImage` fit/crop the chosen bitmap into the display
rectangle without a universal scale <= 1 restriction. Normally a margin-sized
cached image is downscaled. A small full-original cache/source can be upscaled
for display. Therefore “stores smaller images and enlarges them” needs this
distinction: smaller than ORIGINAL is common; smaller than the required DISPLAY
despite available higher-resolution source detail is not its normal cache policy.

NivisViewer already has Auto's sqrt(2) margin with its own DPR/bucket policy.
The current 149 logical px / portrait 1:sqrt(2) / Auto / max 512 comparison keeps
that policy intact. Compression quality is separate and is now user-configurable
(default 60); see `THUMBNAIL_COMPRESSION_SETTING.md` for measurements, request-owned
identity, lazy maintenance and focused verification. No Viewer scheduler or
thumbnail-resolution redesign was undertaken for this compression follow-up.

## 49. Read-only Browser context filename selection (2026-09-10)

### 49.1 Fixed-revision reference and provenance

Reference: https://github.com/himamon/ZipPlaFork, fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, **AGPL-3.0-or-later**,
Copyright (C) 2016 Rio's Toolbox. Existing `licenses/ZipPlaFork/AGPL.txt`
and `licenses/ZipPlaFork/About.txt` remain retained; no dependency was added.

Inspected `source/ZipPla/CatalogForm.cs`, class `CatalogForm`:

- Constructor near 3183 inserts `rightClickFileNameToolStripTextBox` at the
  beginning of the context menu and attaches text-selection/focus handlers.
- `rightClickFileNameToolStripTextBox_GotFocus` / `LostFocus` and
  `setRightClickMenuShortcutKeys` near 3220–3258 disable file Delete/Cut/Copy/
  Paste shortcuts while the textbox owns focus, including read-only mode.
- `cmsRightClickPrepareAndShow` near 12884–12922 chooses the name text,
  measures its width, selects an important range and sets read-only according
  to whether context renaming is permitted.
- `RightClickFileNameToolStripTextBox_KeyDown` near 13791 handles Ctrl+A and
  Up/Down menu focus. MouseMove/KeyUp and
  `rightClickFileNameToolStripTextBox_SelectedTextChanged` near 13826 propagate
  selected text into dynamic menu actions. MouseLeave/Enter near 13848–13855
  switch focus between menu and textbox.
- `StringToFilterForDynamicStringSelection` near 13866 sends selected text
  through `SearchManager.TrimAndPutQuotationIfNeeded` and records filter history.

This is a Qt adaptation of the filename-selection/shortcut-isolation interaction
principle, not a port of the editable textbox, rename-on-close, important-range
heuristic, multi-name wildcard synthesis, or WinForms control implementation.
NivisViewer counterparts are `_BrowserContextFilenameEdit`,
`BrowserWindow.eventFilter` and `BrowserWindow._show_context_menu` in
`app/browser_window.py`.

### 49.2 NivisViewer contract

A single selected file/archive/folder gets a read-only `QLineEdit` at the top
of its item menu. Its full basename comes from the existing canonical selected
path authority, including extensions, dots and filename metadata—not delegate
display text. Width is font-measured, normally 240–480 logical px, capped by
available screen width minus 80 logical px. Long text scrolls horizontally;
the full string is retained. No text is selected or copied implicitly.

`選択文字をコピー` and `選択文字で検索` are enabled only for a nonempty selection.
Copy and Ctrl+C publish only `text/plain`; Ctrl+A selects only this text.
Selection survives menu focus changes, including UTF-16 surrogate pairs.
Escape closes the menu; Up/Down and Tab/Backtab return to enabled menu actions;
Left/Right/Home/End retain text navigation/selection. Typing, Enter, rename,
cut/paste/delete and Browser navigation shortcuts cannot mutate a filename or
invoke a file operation while this editor owns focus.

Search sets the existing Browser search control and invokes its existing
commit path: normal edit notification, immediate literal substring filtering,
rating-filter composition, MRU and ConfigManager persistence. NivisViewer's
`BrowserSearchPredicate` has no wildcard/operator grammar, so importing ZipPla's
quotation helper would incorrectly add literal characters. Quotes, `+r=3`,
wildcards and `OR` therefore pass through unchanged. Existing whitespace
normalization remains authoritative.

Background menus and multi-selection have no partial-name editor/actions.
Existing `名前をコピー` still copies complete basenames in visible selection
order; existing file-operation `コピー` remains unchanged. Mouse invocation
retargets an unselected item; keyboard context invocation uses currentIndex,
not the mouse position. Explicit retarget selection now sets currentIndex with
`NoUpdate`, avoiding an unintended second Ctrl-toggle by QListView.

### 49.3 Focused verification and limits

`tests/test_browser_context_filename.py` uses an offscreen BrowserWindow,
real QMenu/QWidgetAction/QLineEdit controls with fake menu invocation, synthetic
QTest input, mocked image-header probes and temporary configuration. It covers
canonical basenames, partial plain-text copy, literal search/MRU/edit lifecycle,
read-only shortcut isolation, drag selection, disabled empty-selection actions,
long Unicode horizontal scrolling, Ctrl-held retargeting, visible multi-name
order, background menus and keyboard targeting. Existing file-operation menu
test doubles now derive from QMenu so they can own real embedded widgets;
their file-operation assertions remain in place.

Two additional tests enter an unmodified QMenu event loop and use synthetic
mouse clicks on the actual copy/search actions, checking selection survival
through focus loss and menu closure. The test harness explicitly releases
offscreen clipboard MIME ownership before Qt teardown. No production timing
sleep or clipboard cleanup workaround was introduced.

Final verification: **86 passed** across the filename-menu, Browser file
operations, Browser search-history and Browser-window suites; **15 passed per
scale** at 100/125/150/200% DPI in separate processes; **2 passed** for the
existing Viewer-return search lifecycle/invalidation cases. Syntax parsing and
`git diff --check` passed. These results describe this feature, not the older
ZIP/fullscreen follow-up.

No real application, native input, private image content, external GUI,
portable rebuild, commit or push was used. Offscreen results do not establish
native Windows menu/compositor or clipboard interoperability behavior.

## 50. PDF loupe immediate entry and separate artifact lifetime (2026-09-12)

This follow-up consulted this document's existing section 44 comparison of
`ViewerForm.MagnifierCanvas`, `bwMagnifierMaker_DoWork`, `pbView_Paint` and
`GetMagnifierRectangle` at repository https://github.com/himamon/ZipPlaFork,
fixed revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.
**No fresh ZipPlaFork source inspection, copy, translation or port occurred.**
The existing AGPL-3.0-or-later provenance and Copyright (C) 2016 Rio's Toolbox
notices in `licenses/ZipPlaFork` remain unchanged.

Four-way assessment and measurements are in `PDF_LOUPE_ENTRY_PERFORMANCE.md`.
Starting NivisViewer waited for an enlarged fallback and changed normal PDF
cache specs. The previously documented ZipPla page-offset composition is
retained as existing geometry, without claiming that its PDF path supplies an
immediate fallback. The selected Hybrid keeps NivisViewer coordinates, controls,
quality and PDFium service, adds direct painting of retained normal pixels, and
uses an independent bounded final-artifact lifetime. New region/tile rendering
remains deferred. Newly written counterparts are the PDF loupe methods in
`app/viewer_widget.py`, Window integration and `app/pdf_loupe.py`; none is newly
derived ZipPlaFork code. No new dependency or license text is required.

## 51. Browser download completion and preview recovery (2026-09-14)

### Primary source inspected for this change

Repository: https://github.com/himamon/ZipPlaFork

Fixed revision: `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`.
The existing local reference at `../ZipPlaViewer` was inspected read-only.
Its HEAD is `b59e43966ae14bc5207cce54e9075c6099a30272`, **not** the fixed
revision. `git cat-file -t` confirmed that the fixed commit exists locally;
`git diff <fixed-revision> -- source/ZipPla/CatalogForm.cs
source/ZipPla/CatalogForm.Designer.cs` was empty, including worktree changes.
Thus the inspected versions of these two files match the fixed source.
No checkout, global Git configuration change, or source modification occurred.

- `source/ZipPla/CatalogForm.Designer.cs:3254-3263`,
  `CatalogForm.InitializeComponent`: subscribes to Changed/Created/Deleted/
  Renamed with FileName, DirectoryName, Size, LastWrite and LastAccess filters.
- `source/ZipPla/CatalogForm.cs:25493-25610`, `fileSystemWatcherStopper`,
  `fileSystemWatcher_Created`, `fileSystemWatcher_Changed`,
  `fileSystemWatcher_Renamed`: checks current location/loading identity,
  passes `thumbnailChanged: true` for a write, and handles rename separately.
- Same file:25688-25766, `renameItem`: keeps a successful thumbnail only when
  type and thumbnail metadata remain compatible; otherwise requests reload.
- Same file:25769-25904, both `addOrReloadItem` overloads: selects explicit
  reload behavior even for an existing item, and calls
  `ReloadOneThumbnailForSubThread` via `Task.Run`, retaining `startingGuid`.

These observations come from fresh primary-source reading, not only section 37.
AGPL-3.0-or-later provenance applies to the adopted event-to-retry process.
Copyright notices checked at the fixed commit: `license/About.txt`,
`Copyright ©  2016 Rio's Toolbox`; `Properties/AssemblyInfo.cs`,
`Copyright © 2016-2017 Rio's Toolbox`. Existing `licenses/ZipPlaFork/AGPL.txt`
and `About.txt` are retained. No dependency, binary or C# source is added.

### Diagnosis and four-way comparison

The Windows Qt directory watcher already delivered real create and size-change
notifications in the existing offscreen test (18 watcher tests passed before
editing). Replacing the watcher is therefore not supported by this evidence.
The broken downstream contracts were:

1. Model and provider ready/failure cache identities used float modification
   time alone. A growing file with unchanged mtime could retain old pixels or
   a failed decode. Nanosecond differences could also collapse into one float.
2. A notification with unchanged listing metadata skipped reconciliation,
   preventing recovery from a transient read/sharing failure.
3. Cancelled loaders could add failure records or save old pixels after a new
   generation. Disk persistence/stat-based page counts could label old work
   with the file's newer size/mtime instead of the scanned version.
4. The scanner unconditionally removed `.part` and `.crdownload`, contrary to
   the requested visible-temporary-file behavior.

| Option | Assessment |
| --- | --- |
| ZipPlaFork | Explicit Changed → reload/retry and rename type checks cover the needed principle. Per-event item mutation and WinForms array ownership do not fit the current Qt model. |
| Existing NivisViewer | Retain the generation-fenced Qt watcher, worker scanner, sorting/filter pipeline and selection/viewport restoration. Mtime-only preview reuse and unchanged-metadata early exit need correction. |
| Hybrid (selected) | Adopt explicit event-driven retry from ZipPlaFork, but coalesce notifications and reconcile through the existing NivisViewer model. Use scanned content identity for ready/failure caches and preserve compatible successful thumbnails. |
| New design | Native per-path notification journal, content hashing or permanent polling could detect additional metadata-preserving changes, at substantially larger ownership/I/O scope. Not required for the reproduced download transitions. |

### NivisViewer mapping

- `app/browser_scanner.py::scan_entry_from_dir_entry`: temporary extensions
  follow ordinary unsupported-file visibility; internal NivisViewer operation
  artifacts remain excluded. Renaming reclassifies the new path normally.
- `app/browser_model.py::BrowserItem.thumbnail_revision`, thumbnail retention
  and known-page-count reuse: kind, size, nanosecond mtime (float fallback only
  for manually constructed items), creation time. Access time is excluded.
- `app/thumbnail_provider.py`: memory candidates, completed-result identity,
  failure/quiet caches use that revision. `begin_generation(retry_failed=True)`
  cancels old work before clearing failures; cancelled decode completion cannot
  repopulate the failure cache or proceed to normal disk persistence.
- `app/browser_window.py::_on_scan_completed`: a filesystem reconciliation
  also retries failures when listing metadata is equal; compatible ready
  pixels, search, sort and the existing view-state restoration remain in use.
  The notification quiet interval is 350 ms instead of 80 ms. Continuous
  notifications postpone the scan; completion settles after a quiet interval.
- `app/thumbnail_disk_cache.py::_source_for_item`: worker-side cache reads,
  writes and page-count updates reject a scanned size/mtime that no longer
  matches the source. Old decoded pixels cannot acquire a newer fingerprint.

No GUI-thread decode, archive extraction, new timer polling, file enumeration
in the widget, or Viewer pipeline change was introduced.

### Verification and limits

`tests/test_browser_download_refresh.py` adds 10 checks: actual Qt notifications
and visible pixel convergence for temporary rename, growth with fixed mtime,
in-place rewrite, atomic replacement, and incomplete → valid PNG; same-mtime
failure recovery; cancelled success/failure completion; stale disk/count
publication; and eight 100 ms notifications coalesced into one scan/retry with
no subsequent idle scan. All fixtures are synthetic, including Japanese names.
Temporary rename also asserts that the temporary row is visible before rename.

Focused commands (existing `.venv311/Scripts/python.exe`, offscreen Qt):

```text
-m pytest tests/test_browser_download_refresh.py tests/test_browser_model.py tests/test_thumbnail_provider.py tests/test_browser_directory_watcher.py -q
-m pytest tests/test_thumbnail_disk_cache.py tests/test_browser_scanner.py tests/test_browser_sort.py tests/test_browser_thumbnail_resampling.py tests/test_browser_thumbnail_scheduler.py -q
-m pytest tests/test_browser_navigation_window.py tests/test_browser_navigation.py tests/test_browser_search_history.py tests/test_browser_file_operations.py -q
```

The first validation produced 55, 125 and 78 passes (258 total). After the
page-count guard adjustment, the first two affected groups passed together
(180 passed); the unchanged navigation/search/file-operation group remains
78 passed. Python syntax compilation and `git diff --check` also passed.
Initial new-test failures
exposed the temporary suffix exclusion; no pre-existing failure or Qt abort was
observed in these focused groups. This is not a claim that the full suite passed.

Required offscreen Viewer evaluation used 9 synthetic 2400×3600 JPEG pages in a
ZIP, 1280×900 viewport and 256 MiB cache via
`scripts/benchmark_viewer_navigation.py`. Status completed, terminal errors 0.
Request-to-paint: initial 20.583 ms; sequential 1.376 ms; reverse 1.319 ms;
direction reversal 1.609 ms; ping-pong median/max 1.342/10.281 ms;
rapid final 2.113 ms. This supplementary synthetic measurement is not a native
Windows responsiveness claim. No real app, private media or native input was used.

Limits: delivery still depends on filesystem notifications; no polling fallback
was added for lost network-share notifications. A successful image changed while
preserving every compared metadata value is not content-hash detected. During
continuous notifications the old listing remains until a 350 ms quiet interval.
Transient unchanged-metadata failures retry on a later event, not indefinitely.
The consultation review remains the next step; no native check is requested here.

## 2026-09-19 TODO19: saved-thumbnail startup maintenance

Reference: himamon/ZipPlaFork, fixed revision
07955f5267e2fb92d6fc6e40fde2507d8fb07b3b, AGPL-3.0-or-later.
CatalogForm.cs active SetBackgroundMode at 10914–11194 reorders visible work
and adapts worker count; the 10703 alternative is under #if FALSE. Designer's
ThreadCount=1 is not its runtime policy. Source comparison only: no ZipPla code
was copied, translated or transplanted in this change.

Fresh-process synthetic saved-cache startup revealed a different barrier:
NivisViewer ran whole-cache daily maintenance before its first thumbnail read.
With 10,000 indexed cached mixed-format entries, the pass took about 1.1 s.
A bounded 1-versus-2 Browser-worker comparison did not remove its cache lock
barrier. Keep the current Viewer lane and visible-first scheduler; reuse the
existing Browser idle cleanup after initial scan/thumbnail work, retrying if
Viewer interaction temporarily rejects submission. This is the selected Hybrid
of preserving current resource contracts while removing an observed startup
barrier, not an adoption of CPU-scaled ZipPla concurrency.

The JPEG letterbox prototype and its proposed identity change are NOT adopted.
No decoder, cache identity/schema, worker-count or format-priority change ships
in this phase. Detailed comparison of ZipPla, unchanged Nivis, Hybrid and new
separate maintenance/read designs, limitations and fresh-process results are in
BROWSER_WARM_STARTUP_INVESTIGATION.md. Further concurrency/structural work waits
for review; the overall TODO19 issue is not declared universally resolved.

## 2026-09-22 Browser first-thumbnail scan hot path

Reference: `himamon/ZipPlaFork` at fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later,
`source/ZipPla/CatalogForm.cs`: `bwMakePreviewPrepare_DoWork` and
`getDirectoryItemsInfo` enumerate and prepare the folder before catalog
publication; `ThumbViewer.PaintPart` subsequently requests visible rows and
roughly one row of margin through `ThumbViewerItem.LoadAsync`'s single
`SemaphoreSlim(1, 1)`. It does not publish the first thumbnail during folder
enumeration. Its `BackgroundMultiWorker` job is not evidence for parallel
thumbnail decoding in this path. No ZipPlaFork code or processing structure
was copied or ported for this change; existing copyright and AGPL notices in
`licenses/ZipPlaFork/About.txt` and `licenses/ZipPlaFork/AGPL.txt` remain.

NivisViewer's `app/browser_scanner.py`, `scan_directory` prepares the full
sorted `BrowserItem` list before the model publishes initial rows, then
`app/browser_window.py` requests bounded visible thumbnails after first paint.
The unchanged alternative retained repeated `Path` construction for each
accepted entry. A small Nivis-specific hot-path change in
`scan_entry_from_dir_entry` now extracts the filename suffix once, parses
display/rating metadata from the filename rather than its full path, and
normalizes the resulting scan path with `os.path.abspath`. Hidden/system and
unsupported policies, metadata fields, sort policy, cancellation/generation
checks, and thumbnail identity remain on the existing paths. Trailing-dot
suffix handling matches `Path.suffix`. No worker count or paint ordering
changed.

Fresh-process Windows offscreen synthetic saved-cache A/B (existing 1k and
10k mixed-format fixtures, same fixture and profile for each pair; old method
loaded from `HEAD` into the probe, no app/native input):

| Entries / cache | Old initial model / first ready | New initial model / first ready | Observation |
| --- | ---: | ---: | --- |
| 1k / saved | 88.5 / 164.7 ms | 89.3 / 164.3 ms | No material gain. |
| 1k / new profile | 26.2 / 239.0 ms | 22.8 / 241.2 ms | Thumbnail generation dominates. |
| 10k / saved | 316.3 / 407.1 ms | 259.6 / 350.6 ms | About 56 ms earlier first saved thumbnail. |
| 10k / saved, repeated pair | 298.4 / 389.3 ms | 255.7 / 345.0 ms | About 44 ms earlier. |
| 10k / new profile | 235.0 / 463.6 ms | 177.2 / 409.8 ms | About 54 ms earlier despite generation. |

All saved runs reported disk hits and no regenerated thumbnails. Cold runs used
temporary profiles and generated 24 thumbnails; they were not cold OS-file-cache
runs. The 10k effect tracks initial model publication rather than cache work.
The prior stage trace placed list paint near 341 ms and visible request near
356 ms, with deferred tree sync about 1.4 ms. Two post-paint zero timers may
add scheduling delay, but that trace does not establish a safe benefit from
changing paint/request order; they remain unchanged. These measurements are
synthetic offscreen timing, not native Windows perceptual validation.

## 2026-09-22 capacity-based cold Viewer wheel admission

The fixed ZipPlaFork reference is `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later,
`source/ZipPla/ViewerForm.cs` (`SetBackgroundMode`,
`bmwLoadEachPage.SetWorksOrder`) and its wheel/shortcut navigation. The
locally inspected `ViewerForm.cs` is byte-identical to that revision's Git
object. Its worker order follows the current page, but an unready frontier
can ignore later rapid navigation; it has no Nivis-style requested/displayed
atomic contract. This change copies no ZipPlaFork code or structure. The
existing notices at `licenses/ZipPlaFork/AGPL.txt` and `About.txt` remain.

| Design | Decode can keep up | Decode is slower than input | Decision |
| --- | --- | --- | --- |
| ZipPlaFork | Work order follows the current ready frontier. | An unready frontier can drop transit input. | Preserve NivisViewer's latest-target semantics. |
| Previous NivisViewer | After the first cold target, later wheel packets restart a trailing timer even when the worker is free. | Staging coalesces requests. | Retain generation/request-ID and ready-frame contracts. |
| Hybrid (selected) | Every cold wheel target goes directly to the runtime; a free foreground slot starts it promptly. Ready hits publish and paint synchronously. | The single foreground slot owns a bounded current job; obsolete queued requests are replaced, while compatible already-started same-book work finishes into cache before the latest current runs. | Use actual worker capacity rather than a cadence threshold; latest request alone may publish. |
| Independent decode job per packet | Starts each target promptly. | Builds obsolete work and can starve the final target. | Rejected. |

The wheel policy no longer uses the earlier proposed 14/64 ms periodic gate.
Synthetic 8 ms and 20 ms policy tests now both pass every cold request to the
runtime. The runtime, not the input timer, coalesces under backpressure.
`has_unfinished_tasks()` is not used as a capacity signal because it also
counts background work and completed jobs awaiting GUI delivery. Ready wheel
hits now use the same immediate runtime request path, publish their cached
frame synchronously, and let background dispatch continue. These are offscreen behavioral checks,
not native Windows perceptual latency measurements.

### Ready-wheel prefetch continuation

The fixed ZipPlaFork `ViewerForm.cs` `bmwLoadEachPage_EachRunWorkerCompleted`
(5371), `SetNewResizedImage` (5451), `ReduceUsingMemory` (5470), and
`SetBackgroundMode` (5558) keep one recentered background worker running
until its memory condition stops it. NivisViewer retains its own book-scoped
runtime, request/epoch fences, atomic ready-frame commit, decoded-source plus
frame accounting, and current-over-background priority; no ZipPlaFork code or
processing structure was copied in this change.

| Design | Ready wheel with uncached upcoming folder pages | Evaluation |
| --- | --- | --- |
| ZipPlaFork | Recenter order and continue one worker until memory stops it. | Useful continuous behavior, but its publication contract differs from NivisViewer. |
| Previous NivisViewer | Publish ready frame, suspend dispatch, restart a short wheel timer; an available worker can remain idle. | Protects rapid transit from speculative decode but delays useful prefetch. |
| Hybrid (selected) | Publish ready frame via the existing `request()` path and continue recentered prefetch in the available single worker slot. A later cold current replaces unstarted work and follows a compatible already-started decode. | Preserves bounded work, latest target, budget admission, and stale-result fences without a wheel delay. |
| New multiworker design | Decode multiple speculative pages concurrently. | No evidence it improves this bottleneck; extra contention and memory risk. |

In a small offscreen image-folder probe (12 JPEGs, about 240 KB encoded each),
idle opening populated all 12 ready pages under the 128 MiB hard / 112 MiB
active soft setting. The previous ready-wheel path set
`dispatch_suspended=True` with six ready pages and an idle worker despite
ample headroom; after the wheel stopped all 12 became ready. A separate
7 MiB hard / 5 MiB soft probe stopped at nine ready pages with three capacity
skips, then repopulated all 12 after limits expanded. Those measurements
separate the wheel gate from a genuine budget stop; they do not measure native
Windows presentation latency or user images.

### Started-work retention during wheel navigation

The fixed source is `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later,
`source/ZipPla/ViewerForm.cs` `SetBackgroundMode` and
`bmwLoadEachPage_EachRunWorkerCompleted`, together with
`source/ZipPla/GenerarClasses.cs` `SetWorksOrder`: a changed work order
reprioritizes unstarted work while the running page finishes. NivisViewer's
corresponding code is `app/zip_raster_book_runtime.py` `request` /
`_adopt_request` / `_drive`, called for wheel navigation from
`app/viewer_window.py`. This adopts the behavior, not source text or the
WinForms worker structure. Existing ZipPlaFork copyright/AGPL notices and
license text in `licenses/ZipPlaFork/AGPL.txt` and `About.txt` remain.

| Design | Started compatible decode after a different cold wheel target | Assessment |
| --- | --- | --- |
| ZipPlaFork | Finish started work, then use changed work order. | Avoids throwing away a nearly finished read; ready-frontier navigation semantics differ. |
| Previous NivisViewer | Cancel that work and start the newest current after cancellation. | Final-target priority is strong, but repeated input can discard useful reads. |
| Hybrid (selected) | Keep one started job only if the book, epoch, render spec and topology remain compatible; replace unstarted work; after completion, schedule the latest current first. Publish only matching latest request. | Retains useful cache artifacts without a tick backlog; may delay the final page by one remaining decode. |
| Independent multiworker design | Start a second job alongside the old one. | Deferred: worker/decoder/source concurrency and memory reservations need separate evaluation. |

Non-wheel cold navigation retains previous preemption. Source switch,
shutdown, incompatible render and memory-limit shrink still cancel running
work. In the review's warm-OS-cache render benchmark with sixteen 750×1000
synthetic pages, one/two concurrent jobs took median 195.30/116.18 ms for
JPEG, 247.35/142.42 ms for PNG and 290.65/155.06 ms for WebP. These are
throughput observations, not evidence for enabling two runtime workers or
native Windows paint latency. The separate Folder runtime experiment with
20 ms input and 25 ms artificial read delay retained five cache pages instead
of one when started work finished, but both modes published only the final
page. Retention alone does not guarantee visible intermediate pages; a ready
wheel frame can also commit before Qt paints, so the Window now explicitly
requests one synchronous paint of a matching committed ready wheel frame.

### Folder-only two-worker trial

The source comparison remains `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` (AGPL-3.0-or-later),
`source/ZipPla/ViewerForm.cs` `SetBackgroundMode` and
`bmwLoadEachPage_EachRunWorkerCompleted`, and
`source/ZipPla/GenerarClasses.cs` `SetWorksOrder`. The corresponding
NivisViewer implementation is `app/zip_raster_book_runtime.py` scheduling,
`app/image_work_coordinator.py` global lanes, and
`app/image_source.py` Folder metadata cache. No ZipPlaFork code was copied
for this trial; its license and notices remain under `licenses/ZipPlaFork/`.

| Design | Assessment for image folders |
| --- | --- |
| ZipPlaFork | One running background worker is simple and bounds memory, but a cold target can wait for that worker. |
| Previous NivisViewer | One current-first runtime worker and one shared Viewer lane provide the same bounded behavior with latest-request and epoch fences. |
| Hybrid (selected) | Keep the existing cache, work order and publication contract; allow two Folder jobs, with a global general Viewer lane plus one Folder-only supplemental lane. Give cold current the next free slot, retain compatible started wheel jobs, replace unstarted requests and reserve memory through GUI result handling. ZIP/archive and Browser concurrency stay on their existing lanes. |
| New design | A new scheduler or unbounded per-page jobs could overlap more work but would require a larger lifetime, memory and priority redesign without evidence of a better foreground result. |

The standalone Folder constructor accepts `max_active_jobs=1` for A/B checks;
`BookSession` uses two for Folder and one for ZIP/archive. The coordinator
limits Folder Viewer execution to two across windows. In-flight reservations
cover source and frame estimates, including a conservative allowance for
unknown image dimensions; a worker still checks the exact display-unit cost
after header probe. The Folder metadata cache now locks only short map
accesses, keeping file reads and decoding outside the lock.

A small offscreen A/B used 12 synthetic 750×1000 JPEG files (about 240 KB
encoded each), a 128 MiB hard / 112 MiB active soft target, and warm OS
cache. After the first frame, all 12 pages became ready in 93–125 ms with
one worker and 47–94 ms with two across repeated runs; observed concurrent jobs
were one and two, respectively. Retained cache was about 33.68 MiB. Cached
forward, reverse, ping-pong and rapid request sequences completed in about
0.4–1.1 ms per sequence in these probes; that is request cost, not paint latency.
With 50 ms artificial reads occupying background slots, a cold final target
took about 69–72 ms with one worker and 69–76 ms with two. Thus the trial
improves warm-up throughput under this condition but does not promise lower
foreground latency or eliminate skipped visible frames. Unknown-size large
background units can be deferred or skipped under the conservative allowance;
the next request or budget change retries capacity skips. No personal images
or native-window perceptual measurements were used.

### One-worker wheel readiness and queued paint (current direction)

The Folder-only two-worker trial above is deferred. Normal `BookSession`
construction now selects one Folder job, and the coordinator's Folder-only
supplemental lane is disabled by default. Its explicit test override and
reservation lifecycle fixes remain isolated for later comparison; they do not
add a user setting or concurrent Folder execution in the normal application.

The fixed reference is `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`, AGPL-3.0-or-later:
`source/ZipPla/GenerarClasses.cs` `BackgroundMultiWorker.SetWorksOrder`
(around line 247) replaces the pending order while work runs;
`source/ZipPla/ViewerForm.cs` `NextPage` (around 1903) and `PreviousPage`
(around 1951) avoid advancing across missing resized content, and
`movePageNatural` (around 9279) stops a multi-step advance at an unready
frontier. `pbPaintInvalidate` (around 6644) posts `Invalidate(false)`.
NivisViewer translates those behaviors in
`app/zip_raster_book_runtime.py` request adoption,
`app/viewer_window.py` wheel admission, and
`app/viewer_widget.py` queued `update()`. No source text was copied; the
existing ZipPlaFork AGPL text and notices remain in `licenses/ZipPlaFork/`.

| Design | Navigation under a cold wheel burst | Paint and tradeoff |
| --- | --- | --- |
| ZipPlaFork | One worker finishes current work; `SetWorksOrder` changes what follows. `NextPage` and `PreviousPage` inspect readiness, with a guarded single-page backward exception. | `Invalidate(false)` queues a paint. Its page and spread representation differs from NivisViewer. |
| Previous NivisViewer | Every cold notch advanced the requested model position, even while the first missing page was loading; a compatible started job still finished into cache. | Ready wheel transit forced `repaint()`, which could synchronously paint during input handling. |
| Hybrid (selected) | One global Viewer job. A cold wheel notch can request one adjacent display unit; further same-direction notches while it is unready are consumed without replay. Reverse input can return toward the last complete frame. Ready hits and direct seeks retain their existing destination semantics. Started compatible work finishes; incompatible epoch/spec/book work still cancels. | Atomic ready commit remains immediate, while `update()` posts paint. Continuous warm-up begins after commit, independent of physical paint; paint acknowledgement retains its cache-ownership and side-effect role. |
| New design | A separate input backlog or speculative multiworker window could keep a distant target. | More queue and state complexity, contrary to the bounded single-worker trial. |

For multi-notch slider wheel packets, each notch passes through the same
readiness guard, so a burst stops at the first cold display unit. Spread
navigation uses the complete unit identity, and LTR/RTL both use logical
next/previous rather than screen direction. A failed image commits a complete
error frame, releasing the guard so the next wheel input can escape it. The
guard applies to wheel navigation only; page selection and direct seek keep
their destination behavior. Cached turns can commit on each input even if Qt
coalesces multiple pending paints into one paint event. This change does not
promise that every intermediate cached frame becomes visible.

The same small 12-page synthetic Folder probe used for the preceding
one-worker comparison was rerun offscreen with one worker, 20 ms scheduled
wheel packets and `processEvents()` between packets. Before this change, its
25 ms artificial-read case committed pages 1, 3 and 9; afterward it committed
1 through 7 and consumed the remaining notches while their frontier was
unready. Both runs eventually cached all 12 pages with no cancellation.
At 12 ms artificial read delay and with every page already cached, the new
path committed all requested pages 1 through 9. Cached commits can outnumber
paint events because `update()` permits Qt to coalesce paints. The probe
records decode starts/completions, ready-cache counts, presentation commits
and paint events separately; it does not measure native Windows latency.
