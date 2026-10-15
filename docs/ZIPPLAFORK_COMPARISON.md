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

| Reference structure | NivisViewer mapping |
| --- | --- |
| `CatalogForm.Designer.cs`, `CatalogForm`: distinct distance presets and line-count presets | `app/browser_wheel_scroll.py`, `BrowserWheelScrollAccumulator`; `app/explorer_list_view.py`, `ExplorerListView.wheelEvent`: preserve system and row modes, add independent logical-pixel and viewport-percentage modes |


| Fixed source / observed behavior | Adopted mapping |
| --- | --- |
| `Program.cs:1352`, `Program.SetTagsToToolStripMenuItems`: all/none/mixed initial state, both mouse buttons toggle, right button keeps dropdown open | `browser_tag_dialogs.py:TagSelectionMenu`, explicit Qt mouse handling and colored registry swatches |
| `Program.cs:1918–1954`, `PrefixEscapedToolStripMenuItem.ToggleCheck`: unchecked -> checked; mixed -> unchecked; checked -> mixed only if initially mixed, otherwise unchecked | `next_tag_state`, `TagCheckBox.nextCheckState`, `TagSelectionMenu._toggle` (behavioral translation) |
| `CatalogForm.cs:12693–12857`, `cmsRightClickPrepareAndShow`: uncheck-all, tag list, editor, register unknown tags from selected filenames; disable conflicting operations while draft changed | Real tag submenu, uncheck-all, manager, unknown-tag registration draft; other file-operation actions disabled while tag edits pending |
| `CatalogForm.cs:13578`, `renameForTextBoxRatingTagPageSequence`, and `cmsRightClick_Closed:19815`: apply tag draft on root menu close; keyboard close cancels; mixed preserves each file's membership and unknown tags survive | Root menu return applies through existing rename batch; left click closes, right click stays; Escape discards. `edited_tags` remains authoritative for preservation. Root hide also hides child menu. |
| `CatalogForm.cs:6846–6926`, `drawTags`: reverse **filename** tag order from bottom right, grow left, wrap upward; reserve bottom-left icon area, clip to thumbnail | `BrowserItemDelegate._paint_tags`; registry supplies color/existence only, not draw order. Maintain Nivis upper rating band and reserve the largest existing type badge. |
| `CatalogForm.cs:6659–6662`, draw margins top=1/bottom=0; label size follows measured text | Replace Qt line-height+4 with actual glyph bounding height+4. Two logical pixels each side guard hinted/fractional-DPI glyphs; no font shrink. One-pixel guard failed edge tests, so was not retained. |


| Source file / class / method | Adopted behavior | NivisViewer mapping |
| --- | --- | --- |
| `source/ZipPla/ZipPlaInfo.cs`, `ZipPlaInfo`, `TagArray`, `CanBeTag`, `GetPathOfCurrentInfo` (lines 23–38, 77–80, 140–147, 262–306) | Comma-separated `t` filename membership, physical rename, permitted tag characters | `zippla_filename_metadata.py`, `browser_tags.py`, `rating_rename_service.py` |
| `source/ZipPla/Program.cs`, `SetTagsToToolStripMenuItems` (1352–1385); `CatalogForm.cs`, `renameForTextBoxRatingTagPageSequence` (13602–13766) | All/none/mixed selection; preserve mixed membership and unregistered tags | `ItemTagsDialog`, `edited_tags`, `BrowserWindow.set_tags_for_paths`, shared rating rename batch |
| `source/ZipPla/TagEditForm.cs`, `loadTags`, `saveTags`, validation and color/order controls (90, 121–230, 300–430); `ZipTag.cs`, `ZipTagConfig` (170–183) | Separate registered names/colors/order; registry rename/delete never rewrite existing file tags | `TagManagerDialog`, `ConfigManager.browser_tag_registry` |
| `source/ZipPla/CatalogForm.cs`, `drawTags` (6846–6910) | Only registered exact names receive colored labels; re-registering the name restores labels | `visible_tags`, `BrowserItemDelegate._paint_tags` |


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


| ZipPlaFork由来の処理 | 移植元 | NivisViewer側 |
| --- | --- | --- |
| persistent archive/index、entryをpage jobが一度だけ取得してdecoderへ渡す | `PackedImageLoader.cs:283-330`, `:1781-1917` | `app/image_source.py`, `_read_entry_qbytearray`, `ZipImageSource.open_compatible_jpeg_at_most` |
| entry→decode→display-readyを1 page jobで完結 | `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | `app/zippla_compatible_raster_path.py`, `_ZipPlaCompatibleRasterJob.run` |
| current→next→previousを1件ずつdispatch | `ViewerForm.cs:5558-5605`, `SetBackgroundMode` / `priorityLevel` | `ZipPlaCompatibleRasterPath.request`, `_drive` |
| completion 1回でdisplay artifact公開 | `GenerarClasses.cs:266-287`, `ViewerForm.cs:5371-5468` | `ZipPlaCompatibleRasterPath._on_job_completed`, `ViewerWidget.commit_display_ready_single` |
| page単位の近傍artifact保持 | `PreFilteredImageArray`, `ResizedImageArray` | path-local最大3枚QPixmap ring。対応外機能は従来Aへfallback |


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


| ZipPlaFork source | Ported processing structure | NivisViewer destination |
|---|---|---|
| `PackedImageLoader.cs:283`, constructor; `:1197`, ZIP entry table; `:1781` / `:1856`, entry stream ownership; `:2601`, disposal | One runtime is created for one already-indexed persistent `ZipImageSource` and retired before its book source is closed. | `app/book_session.py`, `BookSession._replace_viewer_runtime`, `_retire_viewer_runtime`, `_release_retired_viewer_runtime`; `app/zip_raster_book_runtime.py`, `ZipRasterBookRuntime` |
| `ViewerForm.cs:3177-3550`, `bmwLoadEachPage_DoWork` | One display-unit runnable performs all logical pages in the unit: entry read, decode, EXIF-aware source creation, adjustments, wide split, rotation, filter/resize, and display-ready `QImage`. | `app/zip_raster_book_runtime.py`, `_ZipRasterUnitJob.run`, `_render_unit`, `_decode_page`, `_split_ranges`; existing `viewer_render.render_qimage` is the Qt/Pillow render adapter used inside that same job. |
| `GenerarClasses.cs:247-339`, `SetWorksOrder` / completion selection; `ViewerForm.cs:5371-5415`, completion; `:5558`, `SetBackgroundMode`; `:5586`, `priorityLevel` | Fixed numerical current/next/previous bands define the unstarted order. At most one job is active; navigation does not cancel it, its completed bitmap remains cacheable, and the first unfinished page from the newest order starts at the completion boundary without waiting for paint. | `ZipRasterBookRuntime.request`, `_adopt_request`, `_active_job_is_artifact_compatible`, `_drive`, `_submit`, `_on_job_completed`; `ViewerWindow._zip_runtime_request` |
| `ViewerForm.cs:5371`, worker completion; `:5451`, `SetNewResizedImage`; `:6438`, `showCurrentPage` | The sole queued GUI completion creates `QPixmap` objects and publishes only a terminal complete single/spread/split frame. The preceding complete frame remains owned by the Widget until that transaction. | `ZipRasterBookRuntime._on_job_completed`; `ViewerWidget.commit_display_ready_frame`; `ViewerWindow._on_zip_runtime_frame_ready` |
| `ViewerForm.cs:5471`, `ReduceUsingMemory` | Source and display components belong to one cached frame/page lifecycle. One work-order-aware byte ledger evicts the farthest non-current display unit. | `app/zip_raster_book_runtime.py`, `_ZipRasterFrameStore`, `set_retention_order`, `can_admit_prefetch`, `put`, `_prune`, `_retention_rank`; `ZipRasterBookRuntime.set_cache_limits`, `request`, `_drive` |


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


| ZipPlaFork source / method | Adopted structure | NivisViewer destination |
|---|---|---|
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, `:298-303`) | Latest work order selects useful work without invalidating already completed artifacts. | `ZipRasterBookRuntime.request`, `_drive`; `_ZipRasterFrameStore.set_retention_order` |
| `source/ZipPla/ViewerForm.cs`, `priorityLevel` (`:5586-5605`) | Current, forward/reverse neighbors, then remaining pages define one importance order. | `ViewerWindow._zip_runtime_request`; `ZipRasterRequest.navigation_direction`; `_ZipRasterFrameStore._retention_rank` |
| `source/ZipPla/ViewerForm.cs`, `ReduceUsingMemory` (`:5471-5524`) | Completed source/display artifact is disposed from the least important end only when memory must be reduced. | `_ZipRasterFrameStore.put`, `_prune`, `_eviction_candidate`, `_frame_bytes`, `set_limits` |


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


| ZipPlaFork fixed-revision source | Adopted processing principle | NivisViewer implementation |
|---|---|---|
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`, `:266-303`) | One active job selected from a replaced current-centered order | `RasterBookRuntime.request`, `_drive`, `_submit`, `_cancel_active_job` |
| `source/ZipPla/ViewerForm.cs`, `bmwLoadEachPage_DoWork` (`:3177-3550`) | Read/decode/resize/display artifact in one page work item | `_ZipRasterUnitJob.run`, `_decode_page`, `_render_unit` (shared by Folder and ZIP despite the historical private name) |
| `ViewerForm.cs`, `SetNewResizedImage` (`:5451-5468`) | Publish only a completed resized artifact | `RasterBookRuntime._on_job_completed` and `ViewerWidget.commit_display_ready_frame` |
| `ViewerForm.cs`, `priorityLevel` (`:5586-5605`) and `ReduceUsingMemory` (`:5471-5524`) | Current-centered retention and farthest-useful eviction | source/frame stores, combined budget and prefetch admission |


| Fixed-revision source | Adopted principle | NivisViewer implementation |
|---|---|---|
| `GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` | Replace the unstarted order immediately around the latest current page | `RasterBookRuntime.stage`, `_adopt_request`, `_drive` |
| `ViewerForm.cs`, `NextPage` / `PreviousPage` ready-frontier checks | Do not start decode for every transit page of a cold burst | `NavigationAdmissionPolicy`, input-kind routing and `RasterBookRuntime.stage` |
| `ViewerForm.cs`, `SetNewResizedImage` / `showCurrentPage` | Publish only completed resized artifacts and reuse ready output directly | frame-store hit, `commit_display_ready_frame`, last-painted protection |


| Fixed-revision source / method | Adopted principle | NivisViewer implementation |
|---|---|---|
| `SettingForm.cs`, memory selector; `ViewerForm.GetUserMemoryUBound` / `SetMemoryUBound` | A user memory mode must resolve to the same real budget that controls background population | `app/viewer_memory_policy.py`; `ConfigManager.viewer_memory_mode`; `ViewerWindow._load_prefetch_settings` |
| `ViewerForm.SetBackgroundMode`, `priorityLevel`; `GenerarClasses.BackgroundMultiWorker.SetWorksOrder` | Keep a book-wide current-centered replaceable order instead of a three-unit submitted frontier | `ViewerWindow._zip_runtime_request`; `RasterBookRuntime._adopt_request`, `_drive` |
| `ViewerForm.ReduceUsingMemory` | Let memory pressure, current priority and completed-artifact usefulness decide the stopping/eviction point | combined source/frame ledger, `set_cache_limits`, prefetch admission and work-order-aware stores |


| ZipPlaFork source / method | Fixed-revision behavior | Adopted NivisViewer principle |
|---|---|---|
| `source/ZipPla/ViewerForm.cs`, `GetUserMemoryUBound` (`:5442-5449`) | Caps the requested Viewer memory by physical RAM and process address-space constraints. | Resolve one explicit Viewer hard ceiling from the saved mode and machine snapshot rather than deriving residency from a page count. |
| `ViewerForm.SetMemoryUBound` (`:5424-5439`) | Recalculates a usable bound from process working set, available physical memory and active/inactive coefficients of 0.7/0.3. | Keep separate active/inactive population targets and react to physical-memory pressure, while using stable modern policy state rather than copying the exact formula. |
| `ViewerForm.SetNewResizedImage` (`:5451-5468`) | Recomputes the bound, makes room with `ReduceUsingMemory`, publishes a completed `VirtualBitmapEx` only if it can be admitted, otherwise disposes and requeues it. | Perform non-mutating admission before decode where possible and commit replacement/eviction only for a successful, still-relevant completed artifact. |
| `ViewerForm.ReduceUsingMemory` (`:5471-5524`) | Walks the low-priority tail of the latest work order, disposes resized and retained original ownership together, and uses a small count only as a minimum-neighborhood guarantee. | Evict from the least useful retention rank under byte pressure; current, spread partner and the last painted frame remain protected.  Page count is not a maximum-residency contract. |
| `ViewerForm.SetBackgroundMode` (`:5558-5582`) and `priorityLevel` (`:5586-5605`) | Builds an all-page order around current/next/previous/remaining bands, installs it, reduces memory, and runs the Viewer worker with thread count one. | Represent the whole book as current-centered work, re-prioritize it on navigation and run one active display-unit job. |
| `source/ZipPla/GenerarClasses.cs`, `BackgroundMultiWorker.SetWorksOrder` (`:247-264`) and completion selection (`:266-303`) | Replaces the permutation while work is active; at a completion boundary it skips already-started entries and selects the first unfinished item from the newest order. | Replace unstarted background work around the latest current unit, while keeping NivisViewer's cooperative cancellation and request/epoch stale fences. |


| Fixed-revision source / method | Principle evaluated | NivisViewer result |
|---|---|---|
| `ViewerForm.cs`, `bmwLoadEachPage_RunWorkerStarting`, `SetBackgroundMode`, `priorityLevel`; `GenerarClasses.cs`, `SetWorksOrder` / completion selection | One Viewer lane receives current-centered work and chooses the next useful item at a completion boundary without a paint-idle gap. | **Superseded Hybrid predecessor:** `RasterBookRuntime.release_startup_runway` and a request-created `RasterWarmupPlanner` supplied a four-forward/one-reverse prefix. Section 21 retains the priority principle but replaces request-scoped planner ownership with one persistent book-scoped owner. |
| `PackedImageLoader.cs`, constructor, `getZipArchiveEntries`, `OpenImageStream` / `OpenInnerImageStream` | One book-scoped archive handle/index and entry-local page work. | **Already adopted/maintained:** `ZipImageSource.__init__`, `list_images`, entry-read methods and `ZipRasterBookRuntime` keep structured lifetime, cancellation and stale fences. No new archive copy is added. |
| `ViewerForm.cs`, `OpenFile`, `clearResizedImageArray` | Replacement preparation and old ownership disposal before new work. | **Not adopted:** old complete output is retained; `BookSession` retires work immediately but moves bulk cache destruction after successful replacement paint. |
| No ZipPlaFork counterpart | Async archive listing, PresentationState, PageList virtualization, post-paint side effects, migration-only settings and callback-drained rapid switching. | **NivisViewer/New design maintained:** these are modern Qt/UX/lifetime structures, not translations of WinForms code. |


| Fixed-revision source / class / method | Derived processing principle | NivisViewer counterpart |
|---|---|---|
| `source/ZipPla/ViewerForm.Designer.cs:1628-1636`, `bmwLoadEachPage` | One active Viewer page worker. | `app/zip_raster_book_runtime.py`, `RasterBookRuntime._submit`, `_drive` and active-job ownership. |
| `source/ZipPla/ViewerForm.cs:2919-3007`, `bmwLoadEachPage_RunWorkerStarting`; `:5558-5605`, `SetBackgroundMode` / `priorityLevel` | Book-wide page arrays and a current-centered replaceable work order. | `app/raster_warmup_planner.py`, persistent `RasterWarmupPlanner`, priority band and continuous all-book iterator; runtime `_adopt_request`. |
| `source/ZipPla/ViewerForm.cs:3177-3602`, `bmwLoadEachPage_DoWork` | One page job yields a terminal display-ready artifact or error. | Existing `_ZipRasterUnitJob.run` / runtime decode-render path; Nivis extends the job boundary to an atomic complete display unit. |
| `source/ZipPla/ViewerForm.cs:5371-5415`, `bmwLoadEachPage_EachRunWorkerCompleted`; `source/ZipPla/GenerarClasses.cs:247-264,266-339`, `SetWorksOrder` / completion selection | Current change is observed before the completion handler chooses the next unfinished job; one old active result remains reusable. | `RasterWarmupPlanner.recenter`, `RasterBookRuntime._active_job_is_artifact_compatible`, `_on_job_completed`, `_result_is_artifact_compatible` and `_drive`. |
| `source/ZipPla/ViewerForm.cs:5451-5524`, `SetNewResizedImage` / `ReduceUsingMemory` | Publish completed artifact only and remove lower-priority retained work under memory pressure. | `_ZipRasterFrameStore`, `_ZipRasterSourceStore`, `_admission_rank`, combined-budget enforcement and atomic cache insertion after successful QPixmap creation. |
| `source/ZipPla/ViewerForm.cs:6438-6487,6768-6879,6990-7280`, `showCurrentPage` / paint path | Paint is separate from worker continuation; upstream loading/display semantics were also evaluated. | Only the non-gating separation is adopted. `ViewerPresentationState` and `PresentationSurface` replace the upstream logical/display and half-gray behavior. |
| `source/ZipPla/PackedImageLoader.cs:283-330,1197-1243,1781-1867`, loader/index/entry stream | Book-scoped archive/index lifetime and entry-local read. | Existing `ZipImageSource` and `ZipRasterBookRuntime`; unchanged by this planner/surface replacement. |


## Original ZipPla 1.7.2.9 Browser thumbnail residency

The Browser completion/residency and direct-redisplay structure also derives
from <https://github.com/himamon/ZipPlaFork>, at the separate fixed import
revision `5e942ef0006decf01c21d9dbe037dba50e763420` (commit message
`ZipPla 1.7.2.9`). This identifies the repository import; it does not certify
binary identity with an official distribution. License: AGPL-3.0-or-later.
Copyright: Rio's Toolbox, 2016; source assembly copyright 2016-2017.
The full license and upstream notice remain in `licenses/ZipPlaFork/AGPL.txt`
and `licenses/ZipPlaFork/About.txt`.

| Fixed source / processing structure | NivisViewer correspondence |
| --- | --- |
| `source/ZipPla/CatalogForm.cs:8730`, `SetBackgroundMode` (`:10988-11168` and following work ordering): prioritize visible items, then include remaining current-list items | `BrowserWorkflowController` and `ThumbnailWarmupCursor` preserve visible priority with at most four background requests outstanding. A finite prefetch setting bounds generation; unlimited iterates the current list. Initial paint never waits for the list to finish. |
| `CatalogForm.cs:9190-9207`, `bmwMakePreview_EachRunWorkerCompleted`; `:30301-30381`, `ThumbViewerItem.Image` / ownership: retain completed images, including unseen items | `BrowserThumbnailProvider` owns completed artifacts; `BrowserItemModel` retains shared completed-image references beyond the viewport. Original item-owned residency is translated to a central Qt identity/byte store with implicit QImage sharing. No per-completion whole-list model publication is needed for unseen artifacts. |
| `CatalogForm.cs:31007` and following, `ThumbViewer.DrawItem`; `:32506-32518`, `PaintPart`: draw completed images directly and do not dispose offscreen images | `BrowserItemDelegate`, managed `BrowserDisplaySurfaceCache`, and model thumbnail roles draw retained artifacts directly. Scrolling does not clear completed model references or prepared surfaces. The RAM-only pre-paint pull remains a fallback for a newly exposed completed artifact. |
| `CatalogForm.cs:32385` and following, `ThumbViewer.Clear`; `ThumbViewerItem.Dispose`: release images when the listing is replaced | Browser directory switch, source update, cancellation/generation fences, and source eviction release matching model/surface ownership. Rendering geometry/DPI invalidation retains NivisViewer's identity contracts. |

The later fixed Fork revision `07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`
has a different offscreen-clear/`LoadAsync` policy and is not the origin of
this Browser residency adaptation. The original source has no observed
aggregate Catalog thumbnail byte cap. NivisViewer adds a shared budget for
unique cache/model/surface pixels, auto growth with completed-image usage,
OS-pressure shrink, manual limits, and a 65,536 cached-entry guard. Unseen
completed images remain normal residents; distance alone does not evict them.
Read/visible priority only selects victims when the actual budget is exceeded.
The original WinForms renderer, synchronous image APIs, and worker scheduling
are not reproduced: decoding stays off the GUI thread, completion is bounded,
and rank/ref accounting is incremental on scroll and completion.

## Browser first admission and generation

The same fixed import revision `5e942ef0006decf01c21d9dbe037dba50e763420`
of `himamon/ZipPlaFork` remains the comparison source, with the license,
copyright, full license text and notice identified above.

| Fixed source / processing structure | Evaluation and NivisViewer correspondence |
| --- | --- |
| `source/ZipPla/CatalogForm.cs:1874,11238-11264`, `bmwMakePreview_DoWork`: multiple preview workers with CPU-dependent capacity | **Hybrid adopted:** `browser_generation_budget.browser_generation_workers` translates the ordinary CPU/2 allowance into the shared generation pool and process-wide admission ceiling. GUI-published physical and commit headroom constrain the aggregate scratch estimate. Viewer keeps its separate execution lane; PDF and XCF retain their native serialization contracts. Full-power CPU-1 scaling and process-priority changes are not translated. |
| `source/ZipPla/GPSizeThumbnail.cs:390-471`: per-item saved-preview validation and decoding without a central database decode lock | **Hybrid adopted:** retain NivisViewer SQLite metadata and strict source/cover/policy validation; two dedicated saved-read workers decode immutable saved files outside the shared lock. Read leases, post-decode source revalidation and row/epoch revalidation protect deletion, replacement and cancellation. The JPEG XR/file-stream representation is not copied. |
| No ZipPlaFork counterpart | **NivisViewer/New design maintained:** implementation-family 3/4 reuse requires matching crop, quality, alpha, matte and encoding policy. Uncertain legacy rendering is provisional and refreshed; valid reuse does not require a duplicate write. Current saved variants retire only proven superseded legacy variants of the same source/cover and no larger resolution. Qt request generations, source revisions and explicit runnable lifetime remain authoritative. |

The alternatives were evaluated separately: unchanged NivisViewer serial
admission blocked saved reads behind expensive generation; copying ZipPla's
CPU-scaled worker count would omit aggregate scratch-memory admission and Qt
lifetime protection; a wholly new cache format would add migration cost without
solving generation latency. The selected Hybrid keeps the existing quality,
memory-residency and UI contracts and changes execution/lifetime boundaries.
Scratch reservations estimate transient native memory rather than imposing a
hard process RSS limit; large unknown container/vector decoders can exceed the
estimate. Already executing native work finishes before its reservation is
released. Interactive Viewer use pauses new Browser submissions. Persistent
capacity is independent from these RAM/execution limits: new/missing disk
settings use unlimited capacity and no unused-age deletion. Existing numeric
capacity and age settings remain effective. The capacity option stores zero
for unlimited; normal total-size LRU pruning runs only for positive limits.
Actual low disk space or write failure stops new writes and preserves reads.
Source/policy invalidation, compatible variant handling and explicit clearing
remain separate. Index/file layout and save location are unchanged.

Completion waits deliver only the provider's queued handoff/completion calls;
they do not re-enter unrelated GUI timers or painting. Thumbnail runnables
retain native ownership until the pool has fully returned, including its
post-run epilogue. A completion signal alone does not release that ownership.
Retained runnable bookkeeping is bounded. Handoffs carry plain request receipts,
not auto-deleted Qt object pointers. OS samples use immutable private native
bindings and a GUI-owned publisher; generation workers consume the published
sample and pause admission if it becomes stale. The publisher retains its GUI
thread binding wrapper for its application lifetime; completion delivery is
skipped when no Qt application exists. The
existing Qt WebP decoder in `image_source._read_webp_qimage` now releases its
codec handler before closing its input buffer, preserving pixels, alpha and
EXIF fallback while protecting concurrent Browser/Viewer execution.

### Saved-read overhead and generation admission revision

Reference remains the original ZipPla 1.7.2.9 import at
`5e942ef0006decf01c21d9dbe037dba50e763420`, AGPL-3.0-or-later,
Copyright Rio's Toolbox, 2016; source assembly copyright 2016-2017.
The full license and notice paths at the start of this document are preserved.
The later Fork's single-thumbnail semaphore is a separate implementation.

| Fixed original source / processing | Translation and correspondence |
| --- | --- |
| `CatalogForm.cs:1874,8996-9080,11240-11264`; `GenerarClasses.cs:247-338`: CPU-dependent work slots, ordered dispatch, GUI completion/next dispatch | `ImageWorkCoordinator._browser_generation_pool`, `BrowserGenerationBudget.max_active`: ordinary CPU/2 slots shared across Browser windows, with the existing read/metadata/Viewer separation. `_ThumbnailWorker.run` defers memory-blocked jobs to `BrowserThumbnailProvider._waiting_generation`; priority-ordered GUI admission does not occupy native decode slots while waiting. |
| `PackedImageLoader.cs:283-330`: default `onMemory=None`, file-backed archive input; `GPSizeThumbnail.cs:796-1169`: scoped images/streams and progressive resize resource release | NivisViewer retains worker-scoped loader/codec lifetimes and existing reduced JPEG decode. The archive is not copied wholesale to RAM by the admission mechanism. Conservative header estimates remain 20 bytes/pixel, unknown paths 256 MiB; these are reservations, not measured RSS or hard decoder limits. |
| No original aggregate scratch/OS-pressure gate in the reviewed Catalog generation path | New admission keeps half the lesser physical/commit surplus after an OS reserve (10% of physical RAM, between 512 MiB and 2 GiB). This replaces the former always-active 512 MiB ceiling. Active estimated bytes remain charged as well, conservatively allowing for lag and allocation uncertainty. Stale samples defer; unavailable monitoring permits one generation at a time. Temporary pressure does not permanently fail the item; cancellation, source changes and Viewer pause can remove waiting requests. |

**ZipPla:** ordinary CPU scaling is useful, but its lack of an aggregate scratch
gate is not adopted as a safety guarantee for modern large native decoders.
**Earlier NivisViewer:** lane isolation and lifetime fences remain useful; fixed
two-generation slots and the 512 MiB ceiling unnecessarily serialized ordinary
large images even with ample RAM. **Hybrid adopted:** CPU slots plus published
physical/commit admission, preserving independent saved reads and Viewer work.
**New design:** a metadata/decode split or process-based decode was considered;
additional serialization, ownership and cancellation machinery was not justified
by the measured local read overhead.

`ThumbnailRenderSpec` now owns cached encoding/tokens/legacy variants for its
immutable lifetime; mutable source or cover identities are never memoized.
`ThumbnailDiskCache._lookup_read` selects and leases its first immutable file
in one short shared interval, then combines lease release with the post-decode
row/epoch check. Source/cover signatures and the second SQL row verification
remain. Codecs and source inspection stay outside the shared lock. Redundant
enable calls take an optimistic no-op path; actual reads still fence state under
both shared intervals. Standalone completion ranks all residents only when
eviction is required. Read workers remain two; no all-list completion barrier
or GUI decode was introduced. Native runnable ownership through pool quiescence
and the retained GUI QThread binding wrapper remain unchanged.

### Earlier resident first exposure and nearby display preparation

The reference is the original ZipPla 1.7.2.9 import in
`himamon/ZipPlaFork`, revision `5e942ef0006decf01c21d9dbe037dba50e763420`,
AGPL-3.0-or-later, Copyright Rio's Toolbox 2016 / assembly 2016-2017.
The existing full license and About notice are retained at the paths above.

| Original source / processing | NivisViewer translation |
| --- | --- |
| `source/ZipPla/CatalogForm.cs:9190-9207`, `bmwMakePreview_EachRunWorkerCompleted`: assign completed Bitmap directly to `item.Image`, including unseen items | `BrowserThumbnailProvider.resident_thumbnail_ready` publishes an identity receipt after a retained completion. `BrowserThumbnailPresentation` borrows existing RAM pixels and calls `BrowserItemModel.set_thumbnail_image` immediately; unseen publication uses shared QImage references and the existing pixel ledger, without per-row view invalidation. |
| `CatalogForm.cs:32555-32634`, `preRenderScroll`; `:32636` onward `tryRenderWithPrerender`; `:32705` onward `OnMouseWheel`: prepare a bounded newly exposed strip before scrolling | `BrowserThumbnailPresentation` prepares image-only surfaces for a bounded band around the viewport, in GUI slices of at most four units / a soft two-millisecond budget, yielding between slices. `BrowserItemDelegate.prepare_thumbnail_image` uses the real cell/DPR/crop geometry; rational pixel-snap phases cover partial-row scrolling. Selection, hover, title and badges remain live delegate rendering rather than a saved full-screen canvas. |

**ZipPla:** item-ready pixels and bounded exposure preparation are useful; its
WinForms strip bitmaps and scrolling API are not reproduced. **Earlier NivisViewer:**
visible RAM restoration and paint-driven preparation remain safe fallbacks, but
unseen background completion did not immediately populate model references.
**Hybrid adopted:** separate resident publication from `dataChanged`, and prepare
nearby image surfaces before exposure, using the existing Qt virtualized view.
**New design:** an entirely new presentation/model authority was not justified;
the current generation/source/token identity and memory ownership remain in place.

Warmup borrows RAM only: no source decode or disk read is requested. Preparing a
surface and publishing an unseen model reference do not mark the source displayed;
an actual paint marks it even on a surface hit. All source/model/surface references
share the existing budget. Real source eviction drops corresponding model and
surface references. Quiet publication rechecks ownership after its model-reference
callback so a synchronous eviction cannot recreate an orphan image. Queue contexts fence generation, model revision, render token,
geometry, layout direction and DPR; a model replacement is reconciled from RAM
incrementally, rather than rescanned per completion or per scroll. Viewer pause
suspends surface work. Completed artifacts remain resident outside the band until
actual budget/identity invalidation. Finite acquisition horizons, unlimited-list
acquisition, disk defaults and RAM limits are unchanged. A large jump outside the
prepared band can still prepare surfaces during its first paint; resident pixels
are already model-visible. No whole-list preparation barrier delays initial paint.

Offscreen first-exposure checks distinguish image/model residency from surface
preparation and presentation timing. They do not prove that Windows compositor
presentation or the reported transient icon has been eliminated on a real display.

### Worker-completed display artifacts and preparation before scrolling

This revision supersedes the GUI raster warmup described immediately above.
The fixed reference remains the original ZipPla 1.7.2.9 import in
`https://github.com/himamon/ZipPlaFork`, revision
`5e942ef0006decf01c21d9dbe037dba50e763420`,
`source/ZipPla/CatalogForm.cs`, Git blob
`85a9346c52d49bce18008249060833f3af4c42d6`.
The translated structure is AGPL-3.0-or-later derived, Copyright Rio's Toolbox
2016 / assembly 2016-2017. Existing license texts and notices remain unchanged.

| Original processing | Current translation |
| --- | --- |
| `CatalogForm.cs:8996-9080`, `:5343-5390`, `:5538-5609`: worker obtains and clips/resizes the display-sized image | `_ThumbnailWorker.run` completes `BrowserThumbnailDisplayPlan` images before publishing a result. `browser_thumbnail_surface.py` contains widget-independent QImage raster work and the existing crop/fit/pixel snapping geometry. Geometry is memoized by shape and source dimensions; identical physical variants are rendered once. Ties-to-even may require up to three dimensions on each axis, rather than an unbounded image per scroll position. |
| `:9190-9207`: assign a finished bitmap to an unseen item | `BrowserThumbnailProvider._display_artifacts` keeps the completed images with their resident source. GUI surface entries are shared references to that artifact. Publication precedes model-ready notification and does not require a per-item widget or an all-list completion barrier. |
| `:32555-32634`, especially `:32615/:32624` before `:32632`; `:32705-32718`: prepare the new exposure before moving | `ExplorerListView.scrollPreparing`, `_PreparingScrollBar`, and `BrowserThumbnailPresentation.prepare_scroll` resolve the future exposure and exact cell/DPR geometry before Python value changes and native slider actions commit. Direct C++ scroll changes have a fallback before `scrollContentsBy` performs the viewport move. Only cells intersecting the new exposure strip are prepared; large jumps are bounded by one future viewport. Finished source artifacts also serve any remaining paint after successive input. |
| `:32481-32503`, `:32636-32666`: reuse completed work, render any missing part normally | `BrowserItemDelegate` consumes the prepared image/target metadata and draws the completed QImage one-to-one. Selection, hover, frames, text and badges remain live Qt drawing. Missing or provisional images retain their normal paint fallback; work is never awaited by the input handler. |

**ZipPla** supplies the useful completion and prepare-before-move order; its
WinForms strip canvases, fixed 480 bound and pending-one-strip restriction are
not carried over. **Earlier NivisViewer** preserved virtualization and resource
identity, but repeated GUI phase jobs could fall behind continuous input.
**Hybrid adopted:** finish display pixels on existing worker lanes, retain them
as budgeted source artifacts, and prepare a small image-only exposure plan before
the move. **New design considered:** a single phase-independent bitmap or a full
viewport tile authority would reduce some indexing, but would change fractional
edge snapping/crop or introduce a second canvas and live-state invalidation.
The existing exact pixel geometry and the Qt virtualized view are retained.

The source raster remains the reusable disk/render variant; completed display
images are not new disk variants. Source, model, completed display and GUI aliases
are counted by the same unique-pixel ledger. GUI index clearing does not discard
the authoritative completed artifact. Final source eviction releases it and its
aliases. Reservation precedes publication and rechecks generation, plan and source
ownership; GUI entry registration also preserves the reentrant cancellation guard.
The memory tradeoff is explicit: keeping more finished images costs RAM. Existing
auto/manual limits, the default auto maximum of 2 GiB and OS pressure trimming
remain the authority; additional images do not bypass them.

Resident geometry changes use at most two RAM-only display jobs on the existing
read lane and its native ownership fence. Pending overlap is preserved on scroll;
direction changes update priority even within the same row interval. GUI slices
perform bounded reference publication and dispatch, without image decode, archive
extraction or raster preparation. A new geometry or an unfinished source can still
use the normal paint fallback while workers progress. There is no input-time wait
for disk, a worker, an entire viewport or an entire listing.

Qt's native action boundary is documented in
[QAbstractSlider.actionTriggered](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QAbstractSlider.html#PySide6.QtWidgets.QAbstractSlider.actionTriggered).
Native system wheel, pixel input, dragging and slider actions retain Qt movement
semantics; custom angle-wheel accumulation, clipping and reversal remain unchanged.
Offscreen measurements distinguish missing model pixels from unfinished display
pixels and record delivered input intervals alongside handler/paint time. They
do not establish real Windows compositor latency or elimination of an observed
transient icon on a physical display.

### Display retention under a finite RAM grant

This revision supersedes the multiple pixel-snap variants described above.
The worker-completed display and prepare-before-move order are retained.
The two image tiers now have separate eviction lifetimes within the same unique
pixel budget. Under pressure, distant display images and their GUI aliases
yield before reusable source thumbnails. Preparing a derivative cannot evict a
source merely to admit that derivative. Scrolling alone does not clear either
tier. Current images, two upcoming resident viewports and a reverse-direction row are protected
ahead of the remaining reverse viewport. A hard source-cache limit still applies
when the source images themselves no longer fit.

**ZipPla:** retaining one completed item bitmap minimizes duplicate RAM, but
replacing the reusable source with it would change NivisViewer's size/quality
reuse and fractional-DPI geometry. **Previous NivisViewer:** source and display
artifacts shared a budget but were evicted together; eager display completion
could therefore evict a source-only listing that otherwise fit. **Hybrid chosen:**
keep the existing disk/render source with independent derivative admission and
reclamation. **New design adopted for display geometry:** round physical image
dimensions independently of placement, yielding one worker-produced final image
per item and display plan. Fractional-DPI scrolling moves that image without
changing its dimensions or crop. Compared with separately rounding both absolute
edges, an edge can differ by one physical pixel; one-to-one painting, fit/crop,
aspect ratio and the existing source quality are preserved. Viewport tiles were
rejected because they add live-state invalidation and another canvas owner.

`BrowserThumbnailProvider` maintains incremental display-priority buckets and
releases GUI aliases explicitly without invalidating model/source pixels.
`BrowserThumbnailPresentation` protects the adjacent display ranges, skips distant
work that cannot be retained, and resumes worker dispatch on capacity notifications
instead of polling blocked workers every millisecond. A normal pending disk save
also registers its resume notification under the save lock, preventing an idle
background acquisition queue after that save completes. Acquisition horizons,
disk layout, manual limits and the automatic 2 GiB ceiling are preserved.

### Saved-cache acquisition during continuous Browser scrolling

The preceding resident-image checks do not cover thumbnails that exist only on
disk. Browser warmup previously stopped during fast input, while the visible-only
request plan could cancel queued background reads inside the configured horizon.
Completed display images therefore stopped arriving before exposure even though
the disk cache already contained them.

The chosen **Hybrid** keeps the Qt model, virtual list, revision fences and joint
RAM budget, but lets saved-cache reads continue during input. A miss defers
original-image generation until idle; a visible request can promote that same
probe without waiting for idle. Deferred misses are reopened rather than marked
as permanent failures. The active retention window protects queued background
requests, and a bounded dispatch batch refills free slots after foreground
submission or read completion. Notifications share one subsequent UI turn
(a non-restarting one-shot timer); they do not run a dispatch batch inline for
every completed image. Unlimited mode uses its bounded near window while
input is active; it does not submit the entire listing.

Saved reads and display derivatives share half the logical CPU count, bounded
to two through eight workers, with a base queue of twice that many outstanding
speculative read requests. For a larger next viewport, the queue
can cover up to four worker waves (at most 32 requests), without increasing the
worker count or acquisition horizon. Original-image
generation retains its separate scratch admission and four-request background
limit. A disabled or failed writer does not justify treating an existing saved
thumbnail as a generation job.

Idle usage-date updates resolve source paths before acquiring the shared
database/read-file lock. Windows path resolution can inspect multiple ancestors;
holding that lock for a whole usage batch prevented read workers from progressing
even when all requested thumbnails already existed on disk. SQL updates remain
serialized and retain the same source-token identities.

Scrolling and the later visible-request pass reuse the same retention calculation
when the viewport and memory grant are unchanged. Selection changes still force
a refresh. The already bounded selected-row set also serves the memory scope;
per-item Qt selection queries are needed only while orphan previews require
model-only retention, not for ordinary provider-owned thumbnails.
Absolute-path normalization uses a bounded 8,192-entry lexical-key cache; relative
paths continue to use the current directory. This caches neither file state nor
image data and does not change source-revision validation or image retention.

**ZipPla:** the already documented item-owned final bitmap and before-scroll
preparation remain the reference. **Previous NivisViewer:** retain its modern
resource lifetime and virtualization, but replace conflicting stop/cancel
decisions. **New design:** a separate viewport tile renderer would add another
pixel owner and live-state invalidation; it is not needed to fix late disk
acquisition. This change prepares final item images before exposure, not an
entire viewport strip bitmap. Literal strip painting still differs from
`CatalogForm.preRenderScroll` / `PaintPart` in ZipPla 1.7.2.9 at the previously
recorded fixed revision. No additional source code is transplanted here.

### Saved-read throughput and refill before paint

Continuous scrolling also exposed serialization inside saved-cache reads.
Readers now borrow bounded, reusable read-only SQLite connections. Metadata
queries and immutable-file opening run outside the shared writer lock; file
leases, source signatures, write epochs and final index validation remain in
place. A shared commit revision detects an in-process writer racing the final
query and repeats that validation under the writer lock. Idle connections close
with the cache, and outstanding readers close on return after invalidation.

PNG/WebP cache pixels decode directly into QImage storage, retaining a Pillow
fallback when a Qt codec is unavailable. This removes the Python pixel-buffer
round trip and extra image copy. Use a worker-owned `QImageReader` over a
`QBuffer` containing bytes from the leased file handle. The `QImage.fromData`
Python-bytes overload serialized decoding with Python input handlers in the
tested PySide runtime; `QImageReader.read()` allows input to progress during
native decoding. This keeps file leases and codec fallback intact without
reducing read concurrency or delaying publication. Native RGB32 storage can use
four bytes per pixel instead of three; the existing RAM ledger charges the actual image size.
Saved file formats, quality selection and disk layout are unchanged.

Native runnable ownership is collected by the existing timer or at its hard
bound, rather than checking/joining all pools for every submitted image.
Scroll input restores prepared RAM pixels and schedules acquisition; it does
not create/start a saved-read wave on the input call stack. Submitting even a
bounded wave there delayed the first paint through worker contention. The
scheduled request pass continues acquisition during scrolling. Its visible
worker submissions use a two-millisecond time budget, then resume through a
zero-delay event-loop continuation. This prevents a high-DPI viewport of cold
RAM entries from registering every worker in one long input-blocking callback.
A newly scheduled viewport request supersedes the remaining old rows; generation,
path and revision checks fence each continuation. Ready pixels are still published
immediately, and no fixed input debounce or new worker thread is introduced.
Each completed read may replenish one saved-only slot immediately. This does not
run a full dispatch slice, rebuild the plan, or generate an original image in a
completion callback. The ordinary coalesced timer still owns scans and generation.
The next-viewport queue remains bounded even with unlimited warmup, and results
still pass through the GUI owner's generation and revision checks.
Recentring excludes matching in-flight requests from the refill queue. Previously
these duplicated submissions consumed refill slots before genuinely new rows;
cancellation and rejection now explicitly reopen the affected row for retry.

This remains the Hybrid described above: retain Qt virtualization and modern
resource ownership, with final display pixels prepared ahead of exposure. No
additional ZipPla code is copied, and a separate viewport bitmap renderer is
unnecessary for these read-lane and scheduling delays.

### ZIP WebP geometry probing

ZIP/CBZ WebP geometry no longer enters `QImageReader.size()` alongside parallel
WebP pixel decodes. Folder WebP metadata already avoids that Qt path. The ZIP
adapter now uses `webp_metadata.probe_webp_size` to inspect RIFF, VP8/VP8L/VP8X
geometry and available EXIF orientation within its existing bounded header read.
Ordinary dimensions need only the first header block. EXIF beyond the one-MiB
probe budget leaves geometry unknown until the normal decode publishes it;
the probe neither guesses an unrotated size nor decompresses an entire entry.
The format reference is the [WebP container specification](https://developers.google.com/speed/webp/docs/riff_container).

The NivisViewer source/runtime separation is retained. Replacing the scheduler
with ZipPla's structure would not remove this Qt metadata call, and a further
Hybrid scheduling bridge would add unrelated lifetime machinery. The selected
new header parser preserves bounded ZIP work; reusing the folder Pillow WebP
reader would materialize the entire encoded entry. Pixel decoding, cancellation,
generation checks and display policy remain in their existing owners. No ZipPla
code or additional dependency is introduced by this change.
