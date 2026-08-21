# Architecture

## 現在の実装

Sprint 13では起動と配布境界を次のように分離しました。

```text
ApplicationBootstrap
├─ CommandLineOptions
├─ AppPaths
├─ SingleInstanceBroker
├─ Logging
├─ ApplicationController
└─ WindowsFileRegistrationService
```

`AppPaths`はPyInstaller bundle内の読み取り専用resourceと、exe側のmutable profileを分離します。`portable.flag`があるone-folder配布ではexeの隣に`config.json`と`data/`を置きます。書き込み検査に失敗した場合は別の保存先へ勝手に移らず、ConfigManagerを読み取り専用、MetadataStoreとディスクサムネイルキャッシュを無効状態として基本閲覧を継続します。

単一インスタンスはユーザーとportable配置をハッシュ化した名前のQLocalServer／QLocalSocketを使い、最大1 MiBの長さprefix付きUTF-8 JSONだけを受け取ります。セカンダリはConfigManager、MetadataStore、サムネイルDB、PdfiumServiceを開く前に要求を転送してACK後に終了します。生存確認に失敗したendpointだけをstale候補として除去します。

Windows関連付けはHKCUのNivisViewer所有ProgID、Applications、Capabilities、RegisteredApplications、OpenWithProgidsだけを明示操作時に登録します。拡張子の既定値、HKLM、UserChoiceには書き込みません。ログはprofile内だけにローテーション保存し、telemetryは行いません。frozen smokeと負荷ツールは一時profileを使い、通常ユーザーデータへ触れません。

Sprint 11では、利用者環境のWinRARまたは7-Zipを利用するRAR／7z／CBR／CB7閲覧を追加しました。書庫一覧のprepareはBookSessionのworker、各ページの抽出とデコードはImageCache worker、表紙生成はBrowserThumbnailProvider workerで実行します。既存のZIP／CBZ経路とファイル操作は維持し、ApplicationControllerが外部書庫backendの設定と寿命も管理します。

```text
ApplicationController
├─ ArchiveBackendRegistry
│  ├─ WindowsFileAssociationResolver
│  ├─ WinRARBackend
│  │  ├─ WinRARLocator
│  │  ├─ WinRARProcessRunner
│  │  └─ WinRARListingParser
│  └─ SevenZipBackend
│     ├─ SevenZipLocator
│     ├─ SevenZipProcessRunner
│     └─ SevenZipListingParser
├─ FileOperationCoordinator
│  ├─ FileOperationService
│  ├─ FileOperationExecutor
│  ├─ WindowsRecycleBin
│  └─ FilenameValidator
├─ MetadataStore
├─ BrowserWindow
│  ├─ BrowserNavigationHistory
│  ├─ BrowserDirectoryScanner
│  ├─ FolderTree (QFileSystemModel / QTreeView)
│  ├─ FolderTreeSyncController
│  ├─ FolderBookmarkModel
│  ├─ SidebarLayoutController
│  ├─ NavigationToolbar
│  ├─ DisplaySettings
│  ├─ AddressBar
│  ├─ BookmarkModel
│  ├─ HistoryModel
│  ├─ BrowserItemModel
│  │  └─ BrowserSortPolicy
│  ├─ BrowserItemDelegate
│  ├─ BrowserThumbnailScheduler
│  ├─ BrowserThumbnailProvider
│  │  └─ ThumbnailDiskCache
│  └─ SettingsDialog
└─ ViewerWindow [0..n]
   ├─ BookSession
   │  ├─ ImageSource
   │  │  └─ SevenZipImageSource
   │  ├─ PageModel
   │  └─ ImageCache
   ├─ Viewer command dispatcher
   ├─ FullscreenChromeController
   ├─ ViewerPageNavigationController
   ├─ ViewerPageSlider
   └─ ViewerWidget
      ├─ ViewerCanvasPointerController
      └─ MouseGestureRecognizer
```

- `main.py`: `QApplication`を生成し、起動引数を`ApplicationController`へ渡すエントリーポイント
- `ApplicationController`: 共通の`ConfigManager`と単一`MetadataStore`、ArchiveBackendRegistry、単一FileOperationCoordinator、単一BrowserWindow、ViewerWindow群、最後にアクティブだったViewerWindow、ウィンドウ選択、隣接書庫探索、前面表示、ウィンドウ間同期、Viewer使用中確認、終了判定を管理
- `ArchiveBackendRegistry`: 外部書庫拡張子ごとにWinRAR／7-Zip backendを選び、設定変更時の再検出、限定的な代替試行、アプリ終了時のprocess cancellationを管理
- `WindowsFileAssociationResolver`: `AssocQueryStringW`のUnicode APIを二段階バッファで呼び、認識済み実行ファイルの発見だけを行う。関連付け先を起動せず、未知アプリ、UNC実行ファイル、OpenWith／Explorerを拒否
- `WinRARLocator`: 明示指定、対象拡張子の関連付け、64bit/32bit Program Files、PATHの順でWinRARを探し、同一フォルダの公式コンソールCLIへ変換して短いバージョン確認に成功した絶対パスだけを採用
- `WinRARBackend`: 確認済みのbare listと単一エントリstdout出力、WinRAR終了コードの構造化エラー変換を隔離
- `WinRARProcessRunner`: SevenZipProcessRunnerと同じ非対話・出力上限・timeout・cancel・process停止境界を共有
- `WinRARListingParser`: ロケール依存見出しのないUTF-8 bare listを解析し、日本語とサブフォルダを保持
- `SevenZipLocator`: 明示指定、アプリ配下候補、64bit/32bit Program Files、PATHの順で候補を組み立て、短い情報取得コマンドに成功した絶対パスだけをセッション内でキャッシュ
- `SevenZipBackend`: 7-Zip固有の一覧・単一エントリ取得と構造化エラー変換を隔離
- `SevenZipProcessRunner`: shellを使わない引数配列、stdin無効、stdout/stderr上限、timeout、cancel、terminate/kill、Windowsコンソール非表示、最大2プロセスを一元管理
- `SevenZipListingParser`: `-slt`技術情報を純粋関数で解析し、書庫ヘッダと最大100,000件の内部項目を分離
- `FileOperationCoordinator`: 直列workerへの要求、進捗と完了通知、成功したrename/moveのMetadataStore追従を管理
- `FileOperationService`: QtとGUIに依存せず、絶対パスの検証、重複・親子選択の整理、名前変更、コピー、移動、ごみ箱、新規フォルダ、項目単位の構造化結果を管理
- `FileOperationExecutor`: 最大1スレッドの`QThreadPool`でファイル操作を直列実行し、安全な項目境界でキャンセルを確認
- `WindowsRecycleBin`: `SHFileOperationW`を隔離し、Unicodeパスを確認なしのShellごみ箱操作へ渡す。永久削除へのフォールバックは持たない
- `FilenameValidator`: Windows禁止文字、制御文字、予約名、末尾空白・ピリオド、長さを純粋関数で検証し、衝突回避名を生成
- `MetadataStore`: SQLiteスキーマ、パス正規化、閲覧履歴、読書位置、Browserブックマーク、レート、タグを管理
- `BrowserWindow`: フォルダ履歴、ナビゲーションバー、パス入力、フォルダツリー、ブックマーク／履歴タブ、項目選択、サムネイル一覧、ステータス表示を管理。本のページ移動やBookSession、SQLは持たない
- `BrowserNavigationHistory`: Qtに依存せず、訪問フォルダ、選択項目、縦横スクロール位置、戻る／進むの分岐をセッション内で管理
- `BrowserDirectoryScanner`: 最大2スレッドの専用`QThreadPool`で`os.scandir()`を実行し、128件単位の軽量な結果だけをGUIスレッドへ渡す
- `BookmarkModel` / `HistoryModel`: MetadataStoreの公開APIと変更通知をQt Model/Viewへ公開し、BrowserWindowからSQLを分離
- `FolderBookmarkModel`: MetadataStoreの`item_type=folder`だけをお気に入りフォルダとして公開し、存在確認をGUIスレッド外で行う
- `SidebarLayoutController`: お気に入り、フォルダツリー、履歴のWidgetを再利用し、上下分割、逆順、タブ、単独表示を切り替える
- `FolderTreeSyncController`: 現在フォルダへの有限回・generation付き同期と、自動展開／ユーザー展開の区別を管理する
- `BrowserItemDiscovery`: サブフォルダ、ZIP/CBZ/RAR/CBR/7z/CB7、対応画像を列挙し、列挙時のmtimeとファイルサイズをBrowserItemへ保存する。画像のデコードや書庫の展開は行わない
- `BrowserItemModel`: BrowserItemの表示名、絶対パス、種類、Viewerで開けるか、preview生成可能か、preview種別・状態、元項目のmtime、ファイルサイズ、表示アイコンをQt Model/Viewへ公開し、BrowserSortPolicyで保持リストを並べ替える
- `BrowserSortPolicy`: QtやMetadataStoreに依存せず、自然順、更新日時、種類、サイズ、昇降順、フォルダ優先を一元管理
- `BrowserThumbnailScheduler`: viewport、gridSize、スクロール位置から可視行、選択行、通常時は前後1画面の先読み行を計画
- `BrowserThumbnailProvider`: 最大2スレッドの専用`QThreadPool`で画像、画像フォルダ、ZIP/CBZ/RAR/CBR/7z/CB7のサムネイルを生成し、メモリLRUキャッシュを管理
- `ThumbnailDiskCache`: SQLiteインデックスとWebPまたはPNGファイルによるポータブルな永続サムネイルキャッシュ
- `SettingsDialog`: Viewerの開き方、見開き表示、Browserのサムネイルとディスクキャッシュ、マウス操作割り当て、外部書庫backend選択、WinRAR／7-Zipの自動検出／明示パスを編集
- `ViewerWindow`: 閲覧メニュー、ダイアログ、入力、ViewerWidgetへの描画、全画面などウィンドウ固有UIを管理し、キー・メニュー・マウス入力を共通コマンドへdispatch
- `ViewerPageNavigationController`: 通常／全画面slider、canvas click、メニューからの論理1ページ移動と固定表示単位移動を一元化し、本を開く責務を持たない
- `ViewerPageSlider`: focused page indexを0始まりで表示し、angleDelta／pixelDeltaを累積してwheelを1ノッチ1論理ページへ変換し、処理済みeventをconsumeする
- `FullscreenChromeController`: 全画面時の上下端検出、BottomRevealStrip、menu／slider／statusのoverlay表示、自動非表示、通常配置への復元を担当
- `BookSession`: 現在のパス、ImageSource、PageModel、ImageCache、読み込み世代、外部書庫の非同期prepare→commitに加え、book単位のViewer runtimeとViewerPageListRuntimeの生成・退役・callback drain後のsource解放を管理
- `ConfigManager`: 実行ファイル基準の`config.json`を読み書きし、メタデータDBやキャッシュの配置基準も提供するポータブル設定管理
- `ImageSource`: フォルダ、単体画像の親フォルダ、ZIP/CBZ、外部backend書庫を共通化する画像供給層
- `PageModel`: focused page identity、logical page anchor、固定DisplayUnit、sliding single-page navigationを管理する非GUIモデル
- `ImageCache`: PDF、RAR／7z、未移行custom source向けの旧非同期decode／LRU境界。Folder／単体画像のmain ViewerとZIP main Viewerはbook-scoped raster runtimeを使うため、このcacheへ表示sourceを二重保持しない
- `ViewerWidget`: 渡された画像の描画、拡大縮小、パン、クリックやホイール入力、追加ボタン検出、ジェスチャー軌跡オーバーレイを担当
- `ViewerCanvasPointerController`: 左クリックをダブルクリック間隔まで保留し、single click、pan、double click、modifier、overlay、drop、gesture、BookSession generationを排他的に判定
- `MouseGestureRecognizer`: QtやGUI状態に依存せず、移動量をU/D/L/Rへ量子化して連続方向を圧縮
- `ViewerPageListModel`: Viewerのページ一覧を仮想rowとして公開し、未filter時はrowとpage indexを同一視して全件Widgetや逆引き表を生成しない
- `ViewerPageListRuntime`: 表示中rowと小さなmarginだけを最新work orderとして保持し、book専用source session、低優先度1-job、target-size QImage cache、byte budget、stale/cancel/shutdownを所有する。QPixmap/QIcon化だけはaccepted resultを受けたViewerWindowのGUI threadで行う
- `RasterBookRuntime`: FolderとZIPが共有するbook-scoped main Viewer engine。current中心のbook-wideな置換可能work orderと1-job lane、decode→transform→layout frame生成、preview／full decoded-source tier、layout依存frame store、実decoded byteとframe byteを合わせたbudget、ready hit bypass、cancel／stale／paint-gated prefetch／callback-drained shutdownを所有する。JPEG preview要求はdisplay unit、fit mode、physical viewport、DPR、rotation、spread／split slotからworker側で必要pixel数を決める。寸法未索引のbackground unitはworkerでheaderを確認し、unit全体のsource＋frame costをpixel decode前に確定する
- `FolderRasterBookRuntime`: `FolderImageSource`へ共通runtime契約を適用する薄いsource境界。通常のFolder表示と画像pathから開いた親Folder bookのmain表示では、`ImageCache -> ViewerRenderTask -> prepared display`を通らない。JPEG previewはPillowのnative draft縮小を使い、orientation／adjustment／rotationを縮小sourceへ適用する。各file handleはdecode完了時に閉じ、不要な原寸detach copyを作らず、decoded QImageだけをbook lifetimeへ保持する
- `ZipRasterBookRuntime`: `ZipImageSource`へ共通runtime契約を適用する薄いsource境界。persistent archive sessionとentry cancelはZIP sourceが所有し、通常JPEG entryは1つの`QByteArray`へ読み込んでseekable `QBuffer`／`QImageReader`へ渡す。寸法未索引のJPEGだけでなくPNG／WebP等も、backgroundでは逐次entry deviceと`QImageReader`によるheader-only probeをpixel allocationより先に行う。Folderと同じpreview／full source tier、frame artifact、atomic commit、paint acknowledgementを使う

Raster bookのnavigationは`NavigationAdmissionPolicy`でdiscrete、wheel、初回key、key repeat、slider scrub、内部refreshを分離する。discreteと先頭／低速wheelはcoldでも待たずに`request`し、同方向の2回目の高速wheelでburstが確定した場合だけ`stage`へ切り替える。`stage`は最新current／方向／保持順位を即時反映して旧prefetchを止めるがdecodeを開始せず、device event timestampから得た短いcadence境界で最終targetだけを`request`する。key repeatは初回pressを即処理し、repeat中はworkerの瞬間的なidleに左右されずstageしてreleaseで確定する。ready frameは全input kindでtimer／worker／QPixmap再生成なしにcommitし、last-painted frameはreplacement paintまで保護する。decoded sourceがevictされてもlayout済みQPixmapは独立してready hitを維持し、拡大鏡が必要なsourceだけを同じruntimeで再取得する。slider／statusと`ViewerPresentationState`はatomic commit時に同期し、PageList scroll、thumbnail work、history／progress persistenceはmatching paint acknowledgement後へcoalesceする。

Raster bookのbackground warm-upは、最初のmatching frameがpaintされるまではcurrentだけを処理し、その後はcurrent、進行方向の直近、反対側の直近、距離順の残りというbook-wide orderを1 jobずつ進む。`RasterBookTopology`はbook／layout revisionごとに一度だけ構築し、各navigationの`RasterWarmupPlan`はcurrent前後をlazyに列挙する。このため10,000ページ級でも入力ごとの全件sort／全件queue再構築を行わない。新しいnavigationは未開始cursorを即時に置換し、実行中の不要なbackground jobはcancel／stale化する。ZIP／Folderでは旧prefetch preset／件数を参照せず、combined byte targetだけが停止位置を決める。custom件数はPDF、RAR／7z、未移行source向けに残す。magnifier、actual size、manual zoom、pixel modeなど原寸pixelを必要とするfull-source要求は例外としてcurrent-onlyであり、近傍の原寸decodeでmemoryを使い切らない。

`viewer_memory_mode`はprefetch件数から独立した共有設定で、`auto`、`minimal`（128 MiB）、256 MiBから32 GiBまでの固定bucketを文字列として保存する。固定modeは対応するbyte値をhard limitとしてそのまま使う。`auto`はmode設定時だけ、Windowsのtotal／available physical memory、process working set、現在のcache bytesをsnapshotし、OS reserve（2 GiBとtotal memoryの20%の大きい方）を残せる最大bucketへ解決する。hard limitはbook切替や短期的なavailable変動では揺らさない。background targetはactive時にhard limitの87.5%、inactive時に50%とし、task-local decoder buffer、QPixmap upload、Qt native allocationの余白を確保する。低頻度のpressure sampleはsoft targetだけを即時縮小し、回復はヒステリシス付きの段階増加とする。余裕が戻ればpaint済みのlazy cursorを再開できる。snapshot取得不能時のAutoは512 MiBへ安全に戻る。

Raster runtimeではpreview／full decoded sourceとdisplay-ready frameを1つのcombined byte ledgerへ計上し、旧`ImageCache.cache_size`、source page cap、frame unit capを適用しない。保持可能件数に最大値はなく、実byte costとsoft／hard targetだけが保持量を決める。current／complete spread／last-painted frameと直後・直前の表示単位は最低保証として保護するが、これは最大件数ではない。evictionはlazy work-order rank、方向、layout／DPR／rotation variant、full tier、再生成cost、recencyを共通順位へ写像し、sourceとframeのうち価値が低いものから解放する。満杯時は低優先artifactから回収できる容量を非破壊でworkerへ予約し、decode成功、最新request一致、actual source/frame cost確定後に全QPixmap uploadを成功させてから入替を確定する。cancel／stale／failure／upload失敗／exact admission拒否は既存ready cacheを失わない。通常backgroundはsoft targetで停止し、currentと直近の最低保証だけはhard limit側まで低優先artifactと入れ替えられる。1個のoversized background unitはskipして後続の小さいunitを継続し、同じplan内では再試行しない。targetを増やすとpaint済みcursorをtimerなしで再開し、進行中jobは新targetを受け取る。より高優先のcapacity skipが再び収容可能になった場合だけ再優先し、単なる段階回復ではdecodeを捨てない。targetを縮小するとnon-current jobをcancelして遠方からevictする。PageList thumbnailは別runtimeと小さい専用budgetを持ち、main Viewerのcombined ledgerへ二重計上しない。

Raster JPEGの通常fitでは、原寸sourceを常設の正解とせず、完成frameに必要なphysical pixel数へ品質mode別の余裕（`standard` 1.0倍、`smooth` 1.25倍、`moire_reduction`／`high_quality` 2.0倍）を加えたpreviewをdecodeする。90／270度rotationはdecoder座標へ必要寸法を逆写像し、spreadはunit全体ではなく各slotの寸法を使う。previewとfullは同じpageで共存でき、通常fitは最小の十分なpreview、actual-size／manual zoom／magnifier／pixel modeなど原寸pixelの意味が必要な要求だけはfull tierを選ぶ。Folderのprefetch admissionはexact表示targetではなくPillow/libjpegのnative 1/2／1/4／1/8 tierを予算化する。寸法未索引のbackground workはGUI threadでは有界な仮見積りだけを使い、workerがdisplay unit内の未cache pageをすべてheader probeして、保持source rasterとcomplete layout frameの合計をpixel decode前にexact admissionする。これは片軸fit JPEGだけでなくPNG／WebP等の非JPEGにも適用する。budgetを超える巨大keyは現requestのcapacity skipとして記録し、後続の小さいkeyのwarm-upを継続する。新requestまたはbudget増加でcapacity skipだけを再試行し、broken/start failureをloopさせない。full tierを必要とする表示中はcurrent-only work orderとし、近傍を原寸prefetchしない。magnifierを取り消した時点ではnormal preview requestを同期的にadoptして進行中full jobをstale/cancelし、保持済みframeへ戻る。fullを得ても通常fit用previewと完成QPixmapを直ちに捨てないため、lens終了後は再decodeせずready frameへ戻れる。NivisViewer固有のhigh-DPI target、complete-spread atomic commit、`ViewerPresentationState`、旧frame保持、paint acknowledgementはこのpixel削減より外側の契約として維持する。

既知の残差として、寸法が遅延判明した横長pageはdecoded sourceを再利用できるが、single／spread／wide-splitのdisplay-unit topologyが変われば対応するdisplay-ready frameを再生成する。topology自体もそのrevisionで一度だけ再構築する。PageList runtimeは生成時にmain budgetの1/4を8～64 MiBへclampした独立budgetを受けるが、開いているPageList runtimeのlive resize APIはまだなく、memory mode変更はmain Viewer cache／Raster runtimeへ即時反映されても既存PageList runtimeへは反映されない。

BrowserWindowのフォルダツリーはQt標準の`QFileSystemModel`と`QTreeView`を使い、フォルダだけを表示します。モデルの空ルートからWindowsのドライブへアクセスでき、フォルダ選択は短いタイマーでまとめてから一覧を更新します。右側は`QListView`と`BrowserItemModel`によるModel/View構成です。

### BrowserWindowのフォルダナビゲーション

フォルダツリー、一覧上のフォルダ、戻る、進む、上へ、パス入力、ブックマーク、履歴項目の場所表示、ApplicationControllerからの選択同期、起動時復元は、すべて`BrowserWindow.navigate_to()`を通ります。成功した移動だけが現在位置と`last_browser_path`を更新します。更新失敗時は現在の一覧を空にせず、ステータスバーへ短く通知します。

`BrowserNavigationHistory`は既定で最大150件を保持します。`A → B → C`からBへ戻った後にDへ移動するとCへの進む履歴を破棄します。同じWindowsパスの連続訪問は、大文字小文字、区切り、末尾区切りを正規化したキーで重複させません。戻る／進む自体は履歴を追加しません。履歴スタック、選択、スクロール位置は当面セッション内だけに保持し、アプリ再起動後は`last_browser_path`だけを復元します。

別フォルダへ移動する直前に、選択項目の絶対パスと一覧の縦横スクロール位置を現在の履歴項目へ保存します。戻る／進むではモデル更新直後とQtのレイアウト完了後の一度だけ復元し、削除済み項目は選択なしで継続します。スクロール値は現在の有効範囲へクランプします。プログラムによる選択復元は`open_item()`を呼ばないためViewerWindowを開きません。

ナビゲーションバーは戻る、進む、上へ、更新、編集可能なパス欄を持ちます。相対パスは現在のフォルダを基準に絶対化します。対応画像またはZIP/CBZを入力した場合は親フォルダを表示して対象項目を選択し、非対応・不存在・アクセス不能では現在位置を維持します。更新は同じ履歴項目を非同期再列挙し、正常完了時だけ一覧を一括交換するため履歴を増やしません。ディスクキャッシュのキーに元項目のサイズとmtimeが含まれるため、未変更項目は既存キャッシュを利用できます。

FolderTreeのユーザー選択は短いタイマー後に通常訪問として記録します。`navigate_to()`からのツリー同期中は専用フラグで`currentChanged`を無視し、履歴の二重追加と無限再帰を防ぎます。ツリーのユーザー移動待ちと、QFileSystemModelの読み込み待ちを別の状態として管理します。

BrowserWindowでは`BackButton / XButton1`をフォルダ履歴の戻る、`ForwardButton / XButton2`を進むとして押下時だけ処理します。サムネイル一覧、ツリー、ブックマーク、履歴などの主要子ウィジェットへevent filterを設定し、解放イベントでは再実行しません。パス欄のBackspaceは通常の文字編集として扱います。ViewerWindowでは従来どおりXButtonを前／次の本へ割り当て、Browserの履歴処理と共有しません。

### BrowserWindowの並び替えと表示密度

```text
BrowserWindow
├─ BrowserNavigationHistory
├─ BrowserItemModel
│  └─ BrowserSortPolicy
├─ NavigationToolbar
└─ DisplaySettings
```

並び替えキーは`name`、`modified_time`、`item_type`、`file_size`です。名前はnatsortによる大文字小文字を過度に区別しない自然順とし、同一判定時は絶対パスで安定化します。更新日時とサイズが同じ項目、および同じ種類の項目は名前の自然順を二次順序にします。種類はフォルダ、書庫、PDF、画像、その他の順です。未対応形式も設定に従って`other`として保持し、Viewer open可否とBrowser preview可否を別の能力として扱います。

更新日時は列挙時に取得した元ファイルまたは元フォルダ自身の`st_mtime_ns`だけを使います。サイズはファイル自身の`st_size`で、フォルダを再帰走査しません。stat失敗時は`None`として安全な既定値で比較します。MetadataStoreの`metadata_updated_at`、サムネイル生成日時、キャッシュ時刻は並び替えへ混ぜません。更新操作は再列挙するため最新のファイルシステム情報を取得しますが、並び替えだけで`os.stat()`を繰り返しません。

`browser_folders_first`が有効なら昇順・降順やキーにかかわらずフォルダを先頭グループへ固定し、各グループ内部へ現在の並び替え条件を適用します。ZIPとCBZはフォルダ扱いせず、同一の書庫カテゴリです。無効時はすべての項目を選択キーだけで並べます。

表示密度はExtra Compact（96px級）、Compact、Standard、Comfortable、Largeの5段階で、`QListView`の`gridSize`、`spacing`、フォント、タイトル行数を変更します。`browser_item_spacing_mode=custom`ではセル間隔を0～32 logical px、セル内余白を0～12 logical pxで指定できます。spacing／paddingはサムネイル画像サイズと独立しているため、変更してもthumbnail generation、smart crop解析、Shell icon cacheを変更しません。`thumbnail_size`のcache tokenが変わる場合だけ新しい世代を開始します。

並び替え、フォルダ優先、密度、サムネイルサイズの変更前には、主選択、複数選択、表示基準項目、縦横スクロール位置を絶対パスで記録します。モデル更新とQtのレイアウト完了後に存在するパスだけを復元します。この処理はViewerのopen経路もBrowserNavigationHistoryのvisit経路も通りません。非同期サムネイル結果も従来どおりパスで照合するため、行番号が変わっても別項目へ混入しません。

BrowserItemModelが軽量なファイルシステム属性とソート方針を分離して保持する構成へ、Sprint 9で非同期のバッチ追加APIを接続しました。BrowserWindowはscan結果の世代確認、設定変更、パス基準の表示状態復元を担当します。

### 非同期・増分フォルダ列挙

```text
BrowserWindow
├─ BrowserNavigationHistory
├─ BrowserDirectoryScanner
├─ BrowserItemModel
├─ BrowserSortPolicy
├─ BrowserThumbnailScheduler
└─ BrowserThumbnailProvider
```

`BrowserDirectoryScanner`はGUIとは別の専用`QThreadPool`で対象パスの存在・種別・アクセスを確認し、`os.scandir()`を実行します。同時実行数は最大2で、1件ごとの通知は行わず既定128件の`BrowserScanBatch`として返します。ワーカーが扱うのは文字列、整数、dataclass、キャンセル用`threading.Event`だけであり、QObject、QWidget、QAbstractItemModelを変更しません。結果は通常フォルダ、空フォルダ、不存在、フォルダ以外、アクセス拒否、その他のI/Oエラー、キャンセルに分類し、すべてscan generationと対象パスを保持します。statは列挙時にDirEntryから取得し、並び替えでは再取得しません。

アドレス欄は入力の前後空白と外側引用符を除去し、`BrowserWindow.navigate_to()`は字句的な絶対パス化だけを行います。どちらも対象パスへの`stat()`、`exists()`、`is_dir()`、`resolve()`を実行しません。アドレス欄からの通常フォルダ移動、FolderTree、戻る／進む、上へ、初期フォルダ復元も同じ非同期scan入口を通します。GUIスレッドはgeneration発行、scannerへの要求登録、読み込み中表示だけを担当し、検証失敗時は既存一覧、履歴、選択、`last_browser_path`を維持します。

フォルダ移動と更新ごとにscan generationを進め、前のEventへキャンセル要求を出します。ワーカーは各項目とバッチ境界で確認します。ブロッキングI/Oや実行中タスクを強制終了せず、旧generationのバッチ、完了、エラーをBrowserWindowで破棄します。BrowserWindow終了時は新規受付と後着適用を止めますが、scanner完了を同期waitしません。thumbnail generationは別の番号であり、フォルダcommitまたはサムネイルサイズ変更時だけ進みます。

通常の移動は最初の有効バッチを受信した時点でcommitします。空フォルダは正常完了時にcommitします。commit時に`current_path`、履歴、`last_browser_path`、ツリー、パス欄を揃え、それ以前に失敗した移動は現在一覧と履歴を変更しません。最初の128件は即時表示し、後続バッチは最大120msの単発タイマーでまとめてから限定resetすることで、短時間に全件が届く場合の再ソート回数と画面移動を抑えます。重複パスを除外し、現在のBrowserSortPolicyへ収束させ、完了時に最終ソートします。各項目へ個別Widgetは作らず、`QListView + BrowserItemModel`を維持します。

F5更新は安全性を優先し、受信バッチを一時リストへ蓄積して正常完了時だけ一括交換します。アクセス失敗または途中キャンセルでは現在の有効な一覧、選択、履歴を維持します。正常交換時はパス基準の主選択・複数選択・表示基準項目を復元し、未変更mtime・サイズ・thumbnail_sizeのキャッシュを再利用します。

戻る／進む、上へ、パス入力、ブックマーク、Viewer同期からの移動も同じscan入口を通ります。復元対象が最初のバッチにない場合はrestore_locationをscan generationへ紐づけて保持し、対象パスが後続バッチに到着した時点で選択します。完了時に存在しなければ一度だけ選択なしへ確定し、無制限なタイマー再試行は行いません。選択復元はactivated/open経路を通りません。

### 安全なファイル操作

```text
ApplicationController
├─ FileOperationCoordinator
├─ MetadataStore
├─ BrowserWindow
└─ ViewerWindow [0..n]

FileOperationCoordinator
├─ FileOperationService
├─ FileOperationExecutor
├─ WindowsRecycleBin
└─ FilenameValidator
```

ファイル操作はフォルダタブのサムネイル一覧を対象とします。操作対象はQModelIndexや表示行ではなく、開始時に取得した絶対パスとしてworkerへ渡します。Ctrl／Shiftによる複数選択とCtrl+Aを利用でき、重複パス、および親フォルダとその子が同時に含まれる入力はFileOperationServiceが一度だけ処理します。ブックマークと履歴タブの項目へ直接rename、move、recycleを行うUIはSprint 10では提供しません。

名前変更、コピー、移動、ごみ箱、新規フォルダ作成は最大1スレッドのFileOperationExecutorで直列実行します。workerはQtモデルやWidgetを変更せず、開始、処理済み件数、総件数、項目単位の成否、キャンセル状態をdataclassでGUIスレッドへ返します。単一の巨大ファイルを強制停止せず、ファイル間とディレクトリ項目間の安全な境界でキャンセルを確認します。BrowserWindow終了時は新しい結果を適用せず、実行中workerを無制限にwaitしません。

ファイルコピーは`ChunkedFileCopier`が4 MiB単位で読み、byte進捗を返します。出力は移動先と同じ親の一時名へ完成させ、flush／metadata反映後にrenameまたは`os.replace()`で公開します。クロスボリュームMOVEはdestination完成確認後だけsourceを削除し、source不存在を事後条件として検証します。キャンセルまたは失敗時はNivisViewerが作成した未公開一時物だけを回収し、元項目と既存destinationを保護します。子の追加・削除で変化する親フォルダmtimeは通常のOS動作として許容し、MetadataStoreの更新日時を元ファイルmtimeへ書き戻しません。

同名項目は自動上書きしません。現在は`FileOperationPlanner`が衝突をまとめ、1つの`ConflictResolutionDialog`でskip、両方残す、replace、folder merge、同種への一括適用、全体cancelを選択します。拡張子を維持した「`book - コピー.zip`」「`book - コピー (2).zip`」形式の別名生成も同じ計画・queue経路を通ります。

通常のDeleteとコンテキストメニューの「ごみ箱へ移動」はWindows Shellの`SHFileOperationW`へ`FOF_ALLOWUNDO`、`FOF_NOCONFIRMATION`、`FOF_NOERRORUI`を指定します。NivisViewerが対象件数または単一名を一度確認するため、OS確認ダイアログを重ねません。Shell API失敗、キャンセル、API利用不能、操作後も元パスが残る場合は失敗として返し、`os.remove()`や`shutil.rmtree()`による永久削除へ切り替えません。Shift+Deleteと完全削除APIは未実装です。

renameとmoveの成功後だけMetadataStoreのパスをSQLiteトランザクションで追従させます。フォルダ操作では配下のlibrary_itemsをprefix置換し、reading_history、browser_bookmarks、rating、tags、commentは同じlibrary_itemとの関連を維持します。新パスに既存項目がある場合は、最新の読書履歴、open_countの合算、タグの和集合、既存側優先のrating/commentという安全な統合を行い、UNIQUE制約を破壊しません。トランザクション失敗時はロールバックします。コピーではメタデータを複製せず、ごみ箱移動では履歴やブックマークを即時削除しません。

BrowserNavigationHistoryもrename/move成功後だけフォルダパスと選択パスをprefix置換し、連続する同一フォルダ履歴を重複させません。操作後の一覧はSprint 9の非同期scannerで、履歴を追加せず再読込します。rename、現在フォルダへのcopy/move、新規フォルダは新パスを選択し、ごみ箱移動は削除位置に近い項目を選択します。scan generationとthumbnail generationを維持するため、旧scan結果や旧パスのサムネイル結果を新項目へ適用しません。旧ディスクキャッシュは即時移管せずLRU回収に任せ、新パスで通常のfingerprint検索を行います。

コピー以外の変更対象が開いているFolderImageSource、単体画像、その親フォルダ、ZIP／CBZ、または操作対象フォルダ配下にある場合、ApplicationControllerが影響するViewerWindowだけを抽出します。BrowserWindowは操作前に一度確認し、ユーザーが続行を選んだ場合だけ対象Viewerを通常のclose経路で解放してからworkerを開始します。キャンセルではViewerを閉じず、無関係なViewerとBrowserWindowは維持します。コピーは元項目を変更しないためViewerを閉じません。

ドラッグ＆ドロップ、永久削除、OS標準の高度なUndo、ZIP／CBZ内部エントリの変更は後続課題です。

### 可視範囲優先サムネイル

BrowserThumbnailSchedulerは`ScrollPerPixel`のQListViewについて、viewport、gridSize、スクロール値、モデル件数から可視行を定数時間で近似します。優先順位はVISIBLE、SELECTED、PREFETCHです。可視範囲を先に要求し、その前後各1画面だけを先読みします。1万件でも要求数は可視範囲と限定先読みに収まり、全行の`visualRect()`走査や全件要求を行いません。

ThumbnailProviderはQThreadPoolの優先度を使い、未開始の低優先度要求を可視要求が追い越せるようにします。同一パス・サイズ・generationのpendingは重複させず、queued要求は優先度を引き上げられます。高速スクロール中はselection以外をdisk-hit-onlyのPREFETCHとして扱い、未開始の旧PREFETCHを`tryTake()`可能な範囲で除外します。最後のスクロールから180ms後に可視範囲を再計算して前後1画面の先読みを再開します。PREFETCH missはdecodeも永続保存も行わず、VISIBLE／SELECTEDへ昇格した時だけ適切なDPR版を生成します。通常画像の実行中デコードは強制停止せず、外部7-Zip処理にはcancel tokenを渡します。完了結果はパスとthumbnail generationで安全にキャッシュ・照合します。

増分バッチ、ウィンドウリサイズ、並び替え、表示密度変更は30msの単発タイマーへサムネイル再計画をまとめます。並び替えと密度だけではthumbnail generationやキャッシュキーを変更しません。サムネイルサイズ変更時だけ新generationを開始し、旧サイズ結果を表示へ適用しません。この構造は将来のページング、仮想化、方向別先読みへの接続点です。

サムネイルの完全デコードとZIP内画像の読み出しはGUIスレッドでは行いません。BrowserWindowは現在見えている範囲を優先して要求します。同じ項目の重複要求を抑止し、フォルダ切替ごとに世代番号を更新します。ワーカー結果の世代が現在と異なる場合、またはBrowserWindowが閉じられている場合は結果を破棄します。壊れた画像・書庫は項目種別の標準アイコンのまま表示し、一覧全体を停止しません。

### サムネイルキャッシュ階層

BrowserThumbnailProviderは次の順でサムネイルを取得します。

```text
メモリLRU
  ↓ miss
ThumbnailDiskCache
  ↓ miss
非同期デコード・縮小
  ├─ メモリへ保存
  └─ 有効時だけディスクへ保存
```

保存先はConfigManagerの基準ディレクトリから決定し、`data/thumbnail_cache/index.sqlite3`と`data/thumbnail_cache/files/`を使用します。画像や書庫のあるフォルダには書き込みません。PillowでWebPを利用できる場合はquality 80のWebP、利用できない場合は透過を保てるPNGを使用します。

キャッシュキーは正規化した絶対パス、項目種類、元ファイルのサイズと`mtime_ns`、サムネイルサイズ、キャッシュ形式バージョンを含みます。画像フォルダは、実際にサムネイルへ使用した表紙画像の絶対パス、サイズ、`mtime_ns`も記録します。

SSDへの書き込みを抑えるため、ディスクヒットでは画像を再生成・再保存しません。ヒット時の最終利用時刻はメモリへ蓄積し、明示的なflushまたは終了時にまとめてSQLiteへ反映します。重複生成と失敗項目の無制限な再試行を抑止し、整理は既定で一定数の新規保存後にまとめて実施します。画像ファイルは一時ファイルへ保存してから`os.replace()`で原子的に置換します。

容量超過時は最終利用時刻が古いエントリから削除し、上限の約90％まで減らします。存在しないファイルのDBレコードとDBにない孤立ファイルも整理します。DB破損や書き込み不能時はディスクキャッシュのみを無効化し、メモリキャッシュと一覧表示を継続します。

各ViewerWindowは独立したBookSessionを1つ持つため、本、現在ページ、ImageSource、PageModel、ImageCache、読み込み世代、ズーム、全画面、スライドショーはウィンドウ間で独立します。非同期画像読み込みでは`ImageCache.generation`で古い結果を破棄し、ViewerWindowでも表示要求IDを確認します。本を切り替えた時点でキャッシュ世代を更新し、旧ImageSourceを参照するタスクが残っている場合は、BookSessionがソースを遅延破棄します。

### 外部書庫backend

```text
ApplicationController
├─ ArchiveBackendRegistry
│  ├─ WindowsFileAssociationResolver
│  ├─ WinRARBackend
│  │  ├─ WinRARLocator
│  │  ├─ WinRARProcessRunner
│  │  └─ WinRARListingParser
│  └─ SevenZipBackend
│     ├─ SevenZipLocator
│     ├─ SevenZipProcessRunner
│     └─ SevenZipListingParser
├─ BrowserWindow
├─ ThumbnailProvider
└─ ViewerWindow [0..n]
   └─ SevenZipImageSource
```

RAR／CBR／7z／CB7は`ArchiveBackend`抽象を介して外部WinRARまたは7-Zipへ委譲します。両バイナリを同梱せず、自動ダウンロードも行いません。設定のbackend preferenceと明示パスを最優先し、autoでは対象拡張子のWindows関連付け、標準インストール先、PATHの順に選びます。`.cbr`は未関連付けなら`.rar`、`.cb7`は未関連付けなら`.7z`を照会します。選択は拡張子単位でキャッシュするため、RAR系をWinRAR、7z系を7-Zipとする混在も可能です。

関連付けは`AssocQueryStringW`の`ASSOCSTR_EXECUTABLE`と`open`動詞を利用したbackend発見にだけ使います。結果をShellExecuteせず、未知アプリ、OpenWith、Explorer、UNC上または不存在の実行ファイルは起動しません。`7zFM.exe`／`7zG.exe`は同一フォルダの`7z.exe`、次に`7zz.exe`へ変換できた場合だけ採用します。設定変更と再検出で関連付けおよび選択キャッシュを破棄します。

WinRAR関連付けは`WinRAR.exe`自体を書庫処理として起動せず、同一インストールフォルダの公式`UnRAR.exe`、次に`Rar.exe`へ変換します。ローカルWinRAR 7.13の同梱ヘルプと実行結果で、一覧を`lb -scfr -- <archive>`のUTF-8 bare list、単一ページを`p -inul -- <archive> <entry>`のstdoutとして取得できることを確認しています。stdinはDEVNULLで、パスワードを保存も引数指定もしません。終了値11を`password_required`、3／12／13を破損、255をcancelとして扱います。確認済みコンソールCLIはRAR形式専用なので7z／CB7で形式非対応となった場合は`unsupported_archive`へ変換し、autoかつ利用可能な7-Zipがあるときだけ1回代替します。

`SevenZipLocator`は設定の明示パス、アプリ配下の将来用候補、64bit Program Files、32bit Program Files、PATHの順に候補を作り、情報取得コマンドが短いtimeout内で成功した実行ファイルの絶対パスだけを採用します。結果はセッション内でキャッシュし、backend設定変更時に破棄します。検出失敗は外部書庫機能だけを利用不可とし、画像、フォルダ、ZIP／CBZは継続します。

外部CLIコマンドは`subprocess.Popen`へ引数リストとして渡し、`shell=False`、`stdin=DEVNULL`、Windowsの`CREATE_NO_WINDOW`、UTF-8出力指定を使用します。stdoutとstderrには別々の上限を設け、UTF-8不正列は置換して解析します。timeoutまたはcancel時は短いterminate待機後に必要な場合だけkillし、プロセス枠を必ず解放します。ViewerページとBrowserサムネイルを通したWinRAR／7-Zip合計の同時process数は共有runner全体で最大2です。

一覧は`l -slt -sccUTF-8 -p- -spd -- <archive>`の技術情報形式を使い、純粋関数で書庫ヘッダと内部項目を分離します。未知フィールドや同一キーを安全に扱い、項目数は100,000件で制限します。画像ページ選択では既存の対応画像拡張子を共有し、ディレクトリ、`__MACOSX`、`.DS_Store`、`Thumbs.db`、隠し・テンポラリ相当、制御文字入り項目を除外して内部パスの自然順にします。重複する正規化名は最初の項目だけを採用し、抽出には元のエントリ名を保持します。入れ子書庫は自動展開しません。

単一ページは`x -so -sccUTF-8 -p- -spd -- <archive> <entry>`でワイルドカード解釈を無効にしてstdoutへだけ抽出し、`BytesIO`から既存Pillowデコード経路へ渡します。書庫全体、一時フォルダ、書庫の隣、カレントディレクトリへは展開せず、元書庫へ書き込みません。単一エントリは一覧の展開後サイズと受信中の出力の両方を1GiBで制限します。ディスクサムネイルキーは従来どおり元書庫の絶対パス、サイズ、mtime、thumbnail size、実装format versionを含むため、書庫変更で自動的にmissします。

外部書庫のSource prepareはBookSession workerで行い、成功後だけ現在Sourceへcommitします。失敗、新generation、cancelでは既存の正常な本、履歴、読書位置を維持します。ページ抽出はImageCache worker、表紙抽出はThumbnailProvider workerで実行します。Source、Viewer、Browserの終了やgeneration変更はcancel tokenへ伝播し、後着結果を破棄します。Qt modelやWidgetをworkerから変更せず、GUIスレッドで外部process終了を長時間waitしません。

パスワード付き書庫は`-p-`で対話プロンプトを抑止し、検出時に`password_required`へ分類します。パスワード入力・保存は未実装です。solid書庫は一覧情報をSourceへ保持しますが、全体の事前展開は行わないため後方ページが遅い場合があります。マルチボリュームRARは通常`.rar`と`.part1.rar`を候補にし、`.part2.rar`以降と`.r00`等をBrowserおよび前後の本候補から除外します。7z分割`.7z.001`は正式対応外です。

BrowserWindowはbackend利用不可でも外部書庫項目を一覧へ残し、標準の書庫アイコンへフォールバックします。サムネイル失敗はモーダル通知せず、Viewerで明示的に開いた失敗は対象Viewerのステータスへ短く表示します。RAR／7z系ファイルはSprint 10のファイル操作では通常の不透明なファイルであり、内部項目のrename、delete、追加、再圧縮は提供しません。元書庫へ書き込まず、書庫全体や一時ディレクトリへ展開せず、stdoutから単一ページだけを取得します。PDFは外部書庫backendとは別の`PdfImageSource`として接続します。

### PDFレンダリング

```text
ApplicationController
└─ PdfiumService（アプリ全体で1個）
   ├─ 単一priority queue／専用worker
   ├─ PdfiumBackend（pypdfium2 v5、遅延import）
   ├─ BrowserThumbnailProvider
   └─ ViewerWindow [0..n]
      └─ BookSession
         └─ PdfImageSource
```

PDFium APIはApplicationControllerが所有する1つの`PdfiumService`だけを通し、異なるPDFや複数Viewer、Browserサムネイルを含めて直列実行します。`PdfDocument`、`PdfPage`、`PdfBitmap`は専用workerの外へ渡しません。ページとbitmapはレンダーごとに明示的にcloseし、GUIへはPDFiumから独立したpixel bytesだけを返します。Sourceのcloseは同じqueueへ文書closeを登録し、終了時の`close_all`もworker内で実行します。

`PdfImageSource`は文書IDと不変のページ情報だけを保持します。ページの論理寸法はPDFポイントから96 logical DPIへ変換し、PDF固有回転を反映するため、PageModelはレンダリングせず横長判定と見開き構成を決定できます。PDFの`actual_size`は100%を96 logical DPI相当とし、device pixel ratioは画面上の論理サイズではなくレンダー解像度だけへ反映します。

ViewerのPDFレンダー要求は表示矩形、DPR、表示モードから目標pixel数を作り、64px単位で切り上げます。最大1辺32,768px、最大64,000,000画素へ制限します。現在ページを最優先とし、前後ページとBrowserサムネイルは低優先にします。同一文書・ページ・サイズ・回転・用途・注釈設定のpending要求は共有し、より高い優先度へ昇格できます。リサイズと連続ズーム中は既存pixmapを一時拡縮し、停止から180ms後に高品質画像へ差し替えます。ImageCache generationとBookSession generationにより別ページ・別文書の後着結果を破棄します。

Browser scannerはPDFium依存の有無に関係なく`.pdf`を`pdf`項目として列挙します。表紙サムネイルは第1ページだけを同じPdfiumServiceへ低優先で要求し、既存のメモリ／ディスクキャッシュへ保存します。パスワード、破損、0ページ、backend不在では静かに標準アイコンへ戻ります。明示openはBookSessionの非同期prepareを使用し、成功時だけ現在Source、履歴、読書位置へcommitします。

PDFは元ファイルへ書き込まず、隣へ画像・サイドカー・キャッシュを作りません。注釈は`draw_annots=True`で表示しますが、編集、フォーム入力、テキスト選択／検索、リンク、目次UI、JavaScript、XFAは初期範囲外です。パスワード付きPDFは`password_required`として検出し、パスワードを入力・保存・記録しません。pypdfium2がない場合もアプリと他形式を継続し、PDF機能だけを`backend_unavailable`にします。

### メタデータと元ファイルの分離

ApplicationControllerは`<config.jsonの配置ディレクトリ>/data/metadata.sqlite3`に接続するMetadataStoreを1つだけ所有し、BrowserWindowとすべてのViewerWindowへ共有注入します。各ウィンドウは独自接続を生成しません。DBは`PRAGMA user_version`でスキーマバージョンを保持し、`library_items`、`reading_history`、`browser_bookmarks`、`tags`、`item_tags`を管理します。

NivisViewerのメタデータは元画像、画像フォルダ、ZIP/CBZ/RAR/CBR/7z/CB7/PDFから完全に分離します。レート、タグ、ブックマーク、履歴の変更先はSQLiteだけであり、元ファイル名、内容、画像メタデータ、書庫、PDF、ADS、サイドカーファイル、元ファイルと元フォルダのmtimeを変更しません。`library_items.source_mtime_ns`は元項目を確認した時点の値、`metadata_updated_at`はNivisViewer内のレートやタグの更新時刻であり、別の概念です。BrowserWindowの更新日時表示とソートは従来どおり元項目の`st_mtime_ns`だけを使います。

パスは絶対化、区切りの正規化、Windowsの大文字小文字を考慮した正規化キーで重複を抑えます。表示用パスは元の表記を別に保持し、存在しなくなった項目も自動削除しません。欠損項目はモデルが補助表示と無効色で示し、開こうとした場合はBrowserWindowのステータスバーへ短く通知します。

本を正常に開けた時点だけ履歴を更新します。単体画像を開いた場合の履歴対象は親の画像フォルダです。ページ移動はメモリ上の最新位置を置き換えるだけで、ページごとのSQLite commitは行いません。本の切替、ViewerWindow終了、ApplicationController終了時にflushします。通常の再オープンでは保存位置を有効範囲へクランプして復元し、単体画像を明示した場合は選択画像を優先します。`restore_last_reading_position`により将来この復元を無効化できます。

BrowserWindowのブックマークと履歴は`BookmarkModel`、`HistoryModel`を介したModel/View構成です。履歴は最終閲覧日時の新しい順で最大500件を表示します。ブックマークは元項目へのショートカットであり、登録・削除によって元項目を変更しません。BrowserWindowはMetadataStoreの公開APIだけを使用し、SQLを直接扱いません。

レートとタグはSprint 6ではMetadataStore APIだけを提供します。タグは前後空白と空文字を除去し、casefoldした名前で重複を防ぎながら日本語の表示名を保持します。ZipPlaの`{zpi$...}`形式は解析も書き戻しも行いません。将来対応する場合も、自動的にファイル名へ埋め込まず、ユーザーが明示するインポート／エクスポートの接続点に限定します。

旧`config.json`の`recent_paths`と`reading_positions`は初回だけMetadataStoreへ移行し、成功時に`metadata_migration_v1_completed`を保存します。旧データとViewer内のページブックマークは削除しません。DB破損時は日時付き名称への退避と再作成を試み、書き込み不能時はメタデータ機能だけを無効化して基本閲覧を継続します。

### ViewerWindowの選択

`ApplicationController.open_path()`は`open_viewer_behavior`に従います。

- `reuse_active`: アクティブなViewerWindowを再利用し、なければ作成
- `always_new`: 本を開くたびにViewerWindowを作成
- `reuse_or_create`: 最後にアクティブだったViewerWindow、または既存の最後のViewerWindowを再利用し、なければ作成
- 不正な値: `reuse_or_create`として扱う

ViewerWindow内の通常のファイル選択、最近使った項目、ドロップ操作は、設定値にかかわらず操作元のViewerWindowを再利用します。

BrowserWindowから画像を開く場合は画像自身のパス、ZIP/CBZは書庫パスをApplicationControllerへ渡します。画像パスは既存のImageSource規則により親フォルダを本として開き、指定画像から開始します。フォルダ項目の明示的な起動はそのフォルダへ移動し、ViewerWindowを開きません。

ViewerWindowで本が変わると`book_changed`シグナルがApplicationControllerへ届きます。変更したViewerWindowが最後にアクティブだったViewerWindowの場合だけ、BrowserWindowが対象の親フォルダを表示し、項目を選択してスクロールします。親フォルダが現在と同じ場合は選択だけを更新して履歴を増やさず、異なる場合は実際のフォルダ移動として履歴へ追加します。非アクティブViewerWindowの変更ではBrowserWindowを移動しません。この選択同期はBrowserWindowの明示的な「開く」処理を呼ばないため、再オープンのループは発生しません。前後の書庫移動も同じ経路で追従しますが、BrowserWindowへフォーカスは移しません。

BrowserWindowから本を開いたときは、ApplicationControllerが`open_viewer_behavior`に従ってViewerWindowを選択または作成します。`bring_viewer_to_front_on_open`が有効な場合だけ`show()`、`raise_()`、`activateWindow()`を一度実行し、常時最前面のフラグは設定しません。

`open_viewer_behavior`、前面表示、書庫移動ループはSettingsDialogから変更できます。通常の「開く」はこの設定に従い、BrowserWindowの「新しいViewerWindowで開く」は設定に関係なく新規ウィンドウを作成します。

### Viewerコマンドとマウス入力

`app/viewer_commands.py`がページ移動、書庫移動、全画面、Viewer終了、表示切替、フィット、ズームの安定したコマンド識別子を定義します。`ViewerWindow.dispatch_command()`だけが識別子と実処理を対応付け、キー、メニュー、マウス追加ボタン、マウスジェスチャーは可能な範囲でこの経路を共有します。未知の識別子は実行しません。

ViewerWidgetは`BackButton / XButton1`と`ForwardButton / XButton2`を押下時だけ通知し、解放時には再通知しません。既定では前／次の本へ移動します。ApplicationControllerは現在の本と同じ親フォルダから、対応画像を含む直下フォルダ、ZIP/CBZ、対応画像のまとまりを重複排除して自然順で列挙します。形式判定は`BOOK_FILE_EXTENSIONS`へ集約し、移動時は`loop_book_navigation`を尊重します。移動先Viewerを前面へ出し直しません。

右ボタン押下後、`MouseGestureRecognizer`は設定された最小距離以上の移動を主軸方向のU/D/L/Rへ量子化します。同じ方向の連続入力を圧縮し、最大8方向で打ち切ります。右ボタン解放時に方向列があれば対応コマンドを一度だけ実行し、方向列がなければ通常のコンテキストメニューを開きます。未割り当て方向列と未知コマンドは何も行いません。Esc、ウィンドウ非アクティブ化、Viewer終了では認識状態と軌跡を破棄します。Qtのマウスグラブによりウィンドウ外の解放も通常は同じrelease経路へ戻ります。

軌跡はViewerWidgetの最終オーバーレイとして半透明の線を描くだけで、表示完了またはキャンセル時に消去します。画像、ImageCache、PageModelには書き込みません。ジェスチャー有効化、軌跡、最小距離、XButton、D/Uの割り当てはSettingsDialogから変更でき、ConfigManagerの変更通知により既存ViewerWindowへ即時反映されます。

書庫移動後の`book_changed`は既存の同期経路を通ります。操作対象が最後にアクティブだったViewerWindowの場合だけBrowserWindowの選択が追従するため、別の非アクティブViewerWindowからの変更でBrowser選択を奪いません。`D → close_viewer`も対象ViewerWindowの通常のclose経路だけを通り、アプリ終了の判定はApplicationControllerに残します。

### 見開き密着表示

ViewerWidgetの描画矩形は`calculate_spread_layout()`で計算します。2ページの論理見開きで`join_spread_pages`が有効な場合だけ実効gapを0にし、1枚目の右端と2枚目の左端を同じ中央境界へ配置します。

- RTL／LTRでPageModelから渡された左右順を維持する
- 2ページへ同一倍率を適用する
- `fit_window`、`actual_size`、`manual_zoom`で同じ規則を使う
- 高さが異なる場合は見開き全体の中央に対して縦方向中央揃えする
- パンオフセットを見開き全体へ一度だけ適用する
- 単ページ、表紙単独、横長単独、単一画像を分割した表示には適用しない
- 中央へ仕切り線を描画しない

### 設定の所有と保存

共有設定はApplicationControllerが所有するConfigManagerへ変更時に反映します。ConfigManagerは変更キーを`settings_changed`シグナルで配信し、開いているBrowserWindowとViewerWindowが必要な項目だけを即時反映します。ViewerWindowを閉じる際にローカルな設定スナップショットを一括保存しないため、古い状態のウィンドウを後から閉じても共有設定は巻き戻りません。

共有設定には表示モード、綴じ方向、フィットモード、余白、表紙・横長画像の扱い、背景色、`viewer_memory_mode`、prefetch有効化と方向優先、`open_viewer_behavior`、書庫移動ループ、前面表示設定、マウスジェスチャーと追加ボタンの割り当て、`archive_backend_preference`、`winrar_executable`、`seven_zip_executable`などが含まれます。Viewer memory modeの変更は開いているRaster bookにもbyte-exactに反映される。旧free-form MiB値しかないconfigは最も近い固定bucketへ一度だけ移行し、それ以外の未知値は`auto`へ正規化する。

ジオメトリ、ウィンドウ状態、全画面、回転角度はウィンドウ固有です。複数ウィンドウの完全な復元はまだ行わず、最後にアクティブだったViewerWindowの値を次回作成時の標準状態として保存します。現在の本、ズーム、パン、スライドショーの実行状態は各ViewerWindow内で独立し、ウィンドウ群としての復元対象にはしていません。読書位置だけは本ごとの履歴としてMetadataStoreへ保存します。

BrowserWindowは`last_browser_path`、`browser_sidebar_visible`、`browser_sidebar_width`、`browser_window_geometry`、並び替えキー・順序、フォルダ優先、表示密度を保存します。サムネイルサイズは共有の`thumbnail_size`を使用し、96～384ピクセルへ正規化します。Compact／Standard／Comfortable／Largeプリセットは、サムネイルサイズ、フォント、セル間隔、タイトル行数を一括で選択します。個別設定も保持でき、プリセットと一致しない組み合わせはカスタム状態として扱います。ページ間隔は0～100、ディスクキャッシュ容量は128～4096MBへ正規化します。設定とキャッシュをユーザーの画像フォルダへ書き込みません。

### Browser固定セルとサムネイル要求

Browser一覧は`QListView`のIconModeと固定`gridSize`、`BrowserItemDelegate`を使用します。デリゲートの`sizeHint()`は項目内容に依存せず、サムネイル領域とタイトル領域の大きさを表示密度ごとに固定します。`thumbnail_size`は画像枠の長辺のlogical pixelであり、選択した`thumbnail_frame_ratio`から枠の幅と高さを決定します。後着したQImageは項目固有のroleだけを更新するためセル配置を変更しません。

サムネイル要求は可視範囲を最優先にし、その前後1画面だけを先読みします。スクロール量と時間から高速スクロールを検出した間はmiss時の生成を抑止し、停止から180ms後に再開します。フォルダを開いた時点で全項目を要求しません。モデルは正規化パスから行番号への索引を持ち、サムネイル後着時の項目検索を項目数に依存しない処理にします。

### DPR-awareサムネイル描画と複数解像度キャッシュ

`ThumbnailRenderPolicy`は一覧のlogical display sizeとキャッシュのphysical pixel sizeを分離します。必要な物理解像度はlogical frameの各辺へ、BrowserWindowが置かれたscreenのDPRと品質余裕（容量優先1.0、自動√2、高画質2.0）を掛け、上方向へ丸めて決めます。レイアウトへDPRを掛けず、キャッシュ生成だけへ一度適用します。長辺bucketは96、128、160、192、256、320、384、512、768、1024、1536、2048pxで、設定した256～2048pxの最大辺を上限にします。

キャッシュキーはphysical frame width／height、logical frame ratio、crop mode、smart crop version、quality mode、encoder format／quality、render policy versionとsource fingerprintを含みます。書庫ではsource fingerprintとentry pathもcrop rectの識別に含めます。旧versionはmissとして扱い、同一source／ratio／crop／policyの複数解像度を共存させます。要求以上の最小解像度を選び、なければ元画像または書庫内元画像から不足bucketだけを生成します。小さいキャッシュから大きいキャッシュは作りません。

小さい互換キャッシュしかない場合は項目を`low_resolution_placeholder`として一時表示し、同じgenerationで高解像度版を要求します。到着時は該当項目のQImage roleだけを置換し、選択、current、scroll、一覧layoutを変更しません。5%のbucket丸め許容を除き、小さいキャッシュと小さい元画像を最終表示用に拡大しません。高DPI版は低DPI画面で1回縮小して再利用できます。

生成はworker内でPillow LANCZOSを使い、元画像（smart cropでは元画像のcrop領域）から最終physical sizeへ原則1回だけ縮小します。smart crop解析用の最大256px proxyは表示画像の中間生成物にはしません。ディスクは不透明画像をWebP quality 90、実際に透過を持つ画像をlossless WebP、WebP非対応環境をPNGで保存します。cache schemaとrender implementation versionを更新し、旧キャッシュを誤利用しません。

delegateはQIconへの事前縮小を挟まず、DPR 1.0のcache QImageを明示destination rectへ直接描画します。destinationの四辺をphysical pixel空間でroundしてlogical座標へ戻し、`SmoothPixmapTransform`を有効にします。screen変更またはDPI変更では可視項目だけを新しいrender targetで再要求し、既存の十分大きいcacheを縮小再利用します。通常はログを出さず、`nivisviewer.thumbnail` loggerのDEBUG時だけlogical frame、DPR、cache/display pixel、upscale率、resize回数、source/proxy/cache size、encoder policyを記録します。

`thumbnail_crop_mode`は次の3方式です。

- `letterbox`: 全体をアスペクト比維持で収め、余白はデリゲートがpalette由来の色で枠内だけに描画する。
- `center_crop`: 枠を満たす最小倍率で中央を切り抜く。
- `smart_crop`: 最大256pxのproxyで透明／低分散外周を除き、grayscale edge energyと中央への弱いbiasから決定的なcrop rectを求める。信頼差が小さい場合は中央cropへ戻る。

smart crop rectは元画像に対する正規化座標として、source path、archive entry、size、mtime、比率、algorithm versionをキーにメモリキャッシュします。サムネイルサイズだけが変わった場合は同じrectを使い、比率または元画像が変わった場合は再解析します。生成済みサムネイル自体はportable disk cacheにも保存されるため、次回起動時のcache hitでは解析しません。

Windowsでは`ShellAssociatedIconProvider`が`SHGetFileInfoW`と`SHGFI_USEFILEATTRIBUTES`相当の指定で、実ファイルを開かず拡張子ごとのExplorer関連付けアイコンを取得します。folder、画像、ZIP/CBZ、RAR/CBR、7z/CB7、PDFを拡張子単位でキャッシュし、非Windowsまたは取得失敗時は`QFileIconProvider`へ戻ります。アイコンはCompact 16px、Standard 18px、Comfortable 20px、Large 22pxで画像枠左下へoverlayし、キャッシュ画像へ焼き込みません。

選択時はQt既定の不透明selection fillを使用しません。サムネイルは通常の明るさを保ち、セル内部の2px selection枠、タイトル領域の半透明色、current itemだけの内側focus indicatorを描画します。hoverは弱い1px枠です。`BrowserItemDelegate`はselected/current/hover/focusのパスやindexを保持せず、描画ごとの`QStyleOptionViewItem`だけを参照します。BrowserWindowはcurrent変更時に旧・新双方の`visualRect`を更新し、selection、focus、hover、model reset、layout変更、行追加削除、scan切替でも必要範囲を再描画します。

### Sprint 15のサイドバー、ツリー同期、全画面UI

```text
BrowserWindow
├─ BrowserItemDelegate
├─ FolderBookmarkModel
├─ SidebarLayoutController
├─ FolderTreeSyncController
└─ FileOperationCoordinator

ViewerWindow
└─ FullscreenChromeController
```

お気に入りフォルダは新しい保存形式を作らず、既存MetadataStoreの`browser_bookmarks`を`item_type=folder`で再利用します。任意ラベル、重複防止、並べ替え順、日本語・UNC・欠損パスを扱い、欠損確認は非同期です。従来のファイル／書庫ブックマークは同じパネルの「本」タブへ残し、データとUI導線を維持します。お気に入りとフォルダツリーの右クリック先は既存FileOperationCoordinatorへ渡されるため、コピー／移動の衝突規則、Viewer使用中確認、部分失敗、成功時だけのMetadataStore追従を共有します。

サイドバーは`favorites_top_tree_bottom`、`tree_top_favorites_bottom`、`tabs`、`favorites_only`、`tree_only`を切り替えます。同じModel/View Widgetを再利用し、お気に入りの主選択と現在フォルダを維持します。上下splitterのサイズは共有configへ保存し、40～4000 logical pxへクランプします。

FolderTreeSyncControllerはフォルダ移動のcommit後に起動し、`off`、`select_current`、`focus_current`を扱います。QFileSystemModelの遅延読み込みに対して有限回の再試行と独立generationを持ち、旧要求を適用しません。プログラム選択中はツリーの`currentChanged`から再navigateしないためBrowser履歴を増やしません。自動で展開したancestorとユーザーが手動展開した枝を別集合で追跡し、focus_currentで閉じるのは現在ancestorではない自動展開枝だけです。

FullscreenChromeControllerはfullscreen、UI非表示設定、上下overlay、pointer領域、popup／modal、slider、mouse button、timer generation、window-local cursorを単一の`reconcile_state()`で調停します。通常menu barとstatus barはQMainWindow配下のまま全画面中は常に隠し、fullscreen専用menu/statusをoverlayへ表示するため、後続の`menuBar()`／`statusBar()`呼び出しで通常UIが再生成・残留しません。上端は既定8 logical px（4～32）、下端は操作しやすい既定28 logical px（12～64）です。下端全幅の透明な`BottomRevealStrip`をbottom overlayと重ね、strip、overlay、その全child、slider handle、popup／modalをactive hover unionとして扱うためdead zoneを作りません。領域外では次のevent loop（設定範囲0～3000ms、既定0ms）で隠します。cursorはViewerWindow配下だけへBlankCursorを設定し、600～1000msのidle、mouse move、edge UI、popup、slider、fullscreen解除、window終了を同じControllerで管理します。

### Sprint 16のExplorer型操作と高密度タイトル

`BrowserGridMetrics`はthumbnail frame、title、selection、cell、delegate sizeHint、QListView gridSize、item spacing、cell padding、thumbnail-title gapをlogical pixelで一元計算します。ファイル名はhidden／one_line／two_linesを選択でき、高さはfont metricsの0／1／2行分と明示paddingだけです。セル下端に追加marginを置きません。one_lineは拡張子を残しやすいmiddle elideを使い、完全名はtooltipとstatusで確認できます。

`ExplorerSelectionController`と`ExplorerListView`は通常の項目左ドラッグをファイルdragにし、空白からのrubber bandはShift押下時だけ許可します。単クリック、Ctrl追加／解除、Shift anchor範囲はQtのExtendedSelectionを維持し、drag開始にはOSのstartDragDistanceとstartDragTimeを使います。`FileDragController`は`text/uri-list`とUTF-8 JSONの`application/x-nivisviewer-paths+json`を生成し、絶対ローカルpath、重複排除、256件上限を適用します。

Browser一覧のfolder item、folder tree、favorite itemへのdropは既存`FileOperationCoordinator`へcopy／move要求を渡します。同一Windows volumeまたは同一UNC shareはmove、異なる／不明volumeはcopy、Ctrlはcopy、Shiftはmoveです。自分自身、子孫、同じfolderへのmoveを開始前に拒否します。Browser空白の対応fileはViewerで開き、単一folderは非同期folder probe後にnavigateします。favorite空白のfolder dropもworkerで種類確認してからMetadataStoreへ登録するため、GUI threadで同期statせず履歴を増やしません。favorite内部dragはsort_order更新です。

`ExternalDropOpenController`はViewerWindowへのlocal file URLを検証し、対応画像、書庫、PDF、folder候補だけを重複排除して扱います。複数dropでは1件目をdrop先Viewerへ、残りを別Viewerへ要求し、永続`open_viewer_behavior`を変更しません。HTTP／HTTPSや相対command文字列は受理しません。

FullscreenChromeControllerの既定hide delayは0msです。cursorがoverlayとedge triggerから離れた次のevent loopで隠しますが、popup／menu、slider drag、mouse button、modal、overlay hover中は維持します。見開き、綴じ方向、fit、page、resize、screen、設定、overlay構築の変更時は古いtimerを止め、現在pointer位置から表示状態を再評価します。

### ThumbnailCacheRetentionPolicy

ディスクcacheはsource fingerprint、archive entry、ratio、crop、smart crop、encoder、render policyをrender variantとして、1 variantにつき最大2解像度、同一source／entry全体で最大4派生を保持します。3個目／5個目の保存時は現在保存中とBrowser memory／pendingで保護された要求を残し、inactive familyと最終利用が古いentryを先に削除します。全bucketは生成せず、可視要求の解像度だけをon-demand保存します。

cleanupは欠損record、孤立file、任意の未使用期間、per-variant、per-item、global LRUの各制限を適用し、容量超過時は90%まで減らします。cache hitのaccess時刻は従来どおり遅延flushです。未使用期間は0（無効）または7～3650日で、起動後のworker、前回から24時間経過、設定変更、手動「今すぐ整理」でGUI外実行します。短期間設定は再生成とSSD書き込みを増やす可能性があるため設定画面に警告します。

### Sprint 17の信頼性境界

`BrowserVisibilityPolicy`はhidden、system、unsupportedを別々に扱います。scanner workerは列挙時にWindows file attributesまたはdot-prefixを分類し、`BrowserItem`へflag、拡張子、`openable_by_nivisviewer`を渡します。unsupportedはShell関連付けiconとファイル名を表示しますがthumbnail decodeを要求せず、既定アプリ起動はcontext menuの明示操作だけです。対応形式のthumbnail失敗は項目を残し、tooltipと小さな警告表示、再試行接続点を提供します。

`ThumbnailPersistencePolicy`はVISIBLE／SELECTEDだけを高DPI生成・disk保存対象にします。PREFETCHは既存disk cache hitを利用できますが、miss時は生成を省略します。session counterは優先度別要求、memory/disk hit、生成、disk保存、bucket、ratio/crop/codec、起動後増加量を増分管理します。SQLiteの使用量、entry数、最終cleanupはcache mutation時にworker側で更新した集計値をSettingsDialogへ返し、画面表示のたびに全DB走査しません。

`FolderTreeFocusController`相当の責務は`FolderTreeSyncController`が持ちます。`focus_current`ではgeneration確認後に現在folderを`PositionAtCenter`へ配置し、設定された0～12のcontext depthでtree rootをrebaseします。current pathやBrowser historyは変更せず、context menuの「ツリーのルートを戻す」で全体表示へ戻せます。次のnavigationでは設定に従って再rebaseします。

`ExternalDropOpenController`はViewerWindow、ViewerWidget、central widget、通常control、fullscreen overlayのevent chainでlocal URLだけを受理します。子Widgetのdrag/dropはViewerWindowの共通処理へ転送し、複数pathの順序、重複排除、1件目reuse、2件目以降newを維持します。folder判定はworkerで行い、unsupported、HTTP、text commandはopen経路へ渡しません。

`SettingsDialog`は各tabの内容だけを`QScrollArea(widgetResizable=True)`へ入れ、OK／Cancel／Applyの`QDialogButtonBox`をroot layout下部へ固定します。初回表示時はcurrent screenのavailable geometryの88%以内へ収めます。全画面UI／cursor、visibility、tree focus、thumbnail max edgeを含むcontrolはConfigManagerのdefault、normalizer、load、Apply、settings_changedの同じkeyへround-tripします。

### Sprint 18追補のViewer入力境界

```text
ViewerWindow
├─ FullscreenChromeController
│  └─ BottomRevealStrip
├─ ViewerPageNavigationController
├─ ViewerPageSlider
└─ ViewerWidget
   └─ ViewerCanvasPointerController

PageModel
├─ focused page identity
├─ logical page anchor
├─ fixed DisplayUnit navigation
└─ sliding single-page navigation
```

通常配置と全画面overlayは同じ`ViewerPageSlider`インスタンスを使います。slider wheelは専用Widget内でacceptされ、上方向を`previous_single_page`、下方向を`next_single_page`へ変換します。sliderから`ApplicationController.open_path()`、`open_adjacent_book()`、Browser navigationを呼ぶ経路はありません。プログラムによるfocused index同期には`QSignalBlocker`を使い、`valueChanged`の再入を防ぎます。

矢印、Space、Backspace、Viewer canvas上の通常wheel、メニューの次／前は`next_display_unit`／`previous_display_unit`として従来の固定表示単位を進みます。canvasのraw key press/repeat/releaseとwheelのdevice timestampはpage移動の意味とは別のinput kindとしてWindowへ渡し、QShortcutへ縮退して初回とrepeatを混同しません。slider wheelと設定既定のcanvas左クリックだけが論理1ページ移動です。見開きでは`[2,3] → [3,4] → [4,5]`のsliding spreadを許可し、RTLは画面上の並びだけを反転します。表紙単独は`[0] → [1,2]`、anchorまたは次ページが横長なら該当規則に従って単独表示し、末尾では現在の本の範囲にclampします。画像寸法の後着はfocused identityとsliding anchorを維持します。

Viewer canvasのsingle clickはOSのdouble-click intervalまで保留します。drag threshold以上は、fit状態で実際にpanできない場合も`Panning`としてclickを破棄します。double clickはpending single clickを取消して全画面切替だけを実行します。modifier、overlay／edge trigger、popup／modal、drop、mouse gesture、focus out、Esc、Viewer終了、BookSession generationまたはfocused identity変更でもpending clickを破棄します。`viewer_canvas_left_click_action`は`next_single_page`、`next_display_unit`、`none`を選択でき、Applyは共有ConfigManager経由で既存Viewerへ即時反映されます。

### Sprint 17追補の入力状態とpage identity

```text
BrowserListView
└─ BrowserPointerController
   ├─ Idle
   ├─ PressedOnItem
   ├─ PressedOnEmpty
   ├─ FileDragging
   ├─ RubberBandSelecting
   └─ Cancelled

BookOpenRequest
├─ requested page identity
├─ source index resolution
└─ DisplayUnit containment

FolderTree
└─ FolderTreePointerController
   ├─ ClickConfirmedNavigation
   ├─ DragHoverOnly
   └─ ProgrammaticSync

FolderBookmarkView
└─ FavoriteRowMetrics
```

`ExplorerListView`の左入力は`BrowserPointerController`だけが所有します。項目pressでは絶対path、選択path snapshot、current path、anchor、modifierを保存し、drag開始直前にpathを現在のmodelへ再解決します。標準`QListView.startDrag()`は使わず、独自file dragを開始したmoveとその後のreleaseを標準mouse処理へ渡しません。plain blank dragも標準rubber-bandへ渡さず、Shift+blankだけを専用rubber-band経路として扱います。これにより標準selection、標準drag、独自QDragが同じ入力列で二重実行されません。

Shift／Ctrlはpressからreleaseまでdrag threshold未満だった場合だけ、範囲選択／追加解除として解釈します。file drag成立後のCtrl＝copy、Shift＝moveはdrop時のmodifierから決めるため、選択modifierとdrop action modifierを時間的に分離します。選択済み項目のplain pressでは複数選択を保持し、dragせず同一項目上でreleaseした場合だけ単一選択へ畳みます。sort、model reset、増分scan後も保存rowや古い`QModelIndex`ではなくpathから対象を復元します。

Browser一覧のrowはViewerのpage indexとして使用しません。`FolderListingSnapshot`は画像だけの順序付き絶対path、選択path、scan generation、sort identity、fingerprintを保持します。`FolderImageSource`と`PageModel`は`page_identity()`、`index_for_identity()`、`index_for_path()`、`path_for_index()`でWindows正規化pathを解決します。snapshotが選択pathを含まない場合はSource側の通常列挙へ戻ります。

PageModelは表示単位先頭とは別にfocused page identityを保持します。lazy size確定前はBrowserで指定されたpageを最初のdecode対象とし、寸法後着によるDisplayUnit再構成後はfocused identityを含むunitへ再配置します。`go_to_index(i)`も算術的な偶数／奇数補正ではなく、既知のwide単独ページを含む実際のDisplayUnit列から`i`を含むunitを求めます。status、slider、読書位置、ページ一覧の主選択はfocused pageを参照するため、wide判定後もクリックしたpathと一致します。

FolderTreeは`currentChanged`、selection、hover、expanded、directoryLoadedからnavigateしません。左buttonのpressとreleaseが同じpath、drag threshold未満、非disclosure、非programmatic syncの場合だけ`navigationConfirmed`を発行します。file drag hoverはoverlay highlightだけを描き、drop成立時だけ既存`FileOperationCoordinator`へtarget pathを渡します。展開矢印は展開／折りたたみだけを行います。

お気に入りはpress時にnavigateせず、同一項目上でdrag threshold未満のreleaseが成立した時点で即座にnavigateします。double-click判定のためsingle clickを遅延させず、double-click event自体は新しいscanを発行しません。対象は`QModelIndex`の長期保持やMetadataStore再照会をせず、`FolderBookmarkModel`が保持するabsolute pathを直接使います。drag、drop、Ctrl／Shiftが成立した入力列ではnavigateしません。`FavoriteRowMetrics`の行高は`max(fontMetrics.height(), icon_size) + padding_y * 2`だけで決まり、wrapなし、elideあり、行間隔と14～24pxのfolder iconを独立設定します。

### Sprint 18の汎用ファイルプレビューとBrowser中央ドロップ

```text
BrowserThumbnailProvider
└─ PreviewProviderRegistry
   ├─ TextPreviewProvider
   ├─ WindowsShellPreviewService
   │  └─ IShellItemImageFactory
   ├─ FFmpegThumbnailBackend (任意)
   └─ ShellAssociatedIconProvider

Explorer external drop
└─ BrowserMainDropController
   └─ PendingBrowserFocusRequest
      └─ BrowserWindow scan generation
```

`BrowserItem`は`can_open`、`can_generate_preview`、`preview_kind`、拡張子、folder／supported属性、preview statusを分離します。テキストや動画などViewerで開けない項目でもBrowser previewは生成できます。`PreviewResultKind`は`READY`、`PENDING`、`NO_CONTENT`、`NOT_APPLICABLE`、`UNAVAILABLE`、`FAILED`、`CANCELLED`を区別し、警告バッジとtooltipは実際の`FAILED`だけに表示します。画像のないフォルダ、空テキスト、利用できないShell provider／codec／FFmpeg、PREFETCH miss、キャンセルは標準関連付けアイコンへ静かに戻り、同じgeneration内でdecodeや書庫探索を繰り返しません。

`TextPreviewProvider`はBrowser workerで最大64 KiBだけを読み、BOM付きUTF-8、UTF-16 LE／BE、UTF-32 LE／BE、UTF-8、CP932の順で判定します。NULや過剰な制御文字を含む入力はbinaryとして扱います。内容はHTMLとして解釈せず、先頭行だけを固定paper frameへ`QPainter`で描画します。text render versionを永続cache fingerprintへ含め、PREFETCHではファイルを読みません。

`WindowsShellPreviewService`は専用STA threadで`IShellItemImageFactory::GetImage`を一度に1要求だけ実行します。PREFETCHは`THUMBNAILONLY | INCACHEONLY`、可視／選択要求はShell抽出を許可します。返された`HBITMAP`は独立した`QImage`へcopyし、HBITMAP、COM interface、DCをnative側で解放します。shutdownは新規要求を拒否し、未開始要求をcancelしてsentinelを送り、短いbounded joinを行います。通常終了は`STOPPED`、応答しないnative callは強制停止せず`STUCK`と診断します。Shell由来サムネイルはNivisViewerのディスクcacheへ保存せず、メモリcacheとWindows自身のcacheだけを利用します。

動画はWindows Shell cache、Windows Shell抽出、任意FFmpeg、関連付けアイコンの順に段階的に戻ります。FFmpegは自動取得・自動同梱せず、明示path、アプリ配下候補、PATHから既存実行ファイルだけを検出します。呼び出しは引数配列、`shell=False`、stdin無効、Windows console非表示、出力上限、timeout、cancel、terminate／killを持ちます。Shell由来は永続化せず、FFmpegで可視／選択要求から生成したframeだけを既存ポータブルcacheへ保存できます。

Browser一覧中央への外部local-file dropはViewer openやファイル移動を既定動作にしません。`BrowserMainDropController`がworkerでfile／folderを判定し、folderならそこへ移動、fileなら親folderへ移動して同一親のdrop項目をpathで複数選択し、primaryをcurrent・中央表示にします。異なる親が混在する場合は先頭親groupだけを使い、件数をstatusへ通知します。scan中は`PendingBrowserFocusRequest`がfolder、paths、primary、scan generation、request IDを保持し、各batchと正常完了でpathを再解決します。他のnavigation、新しいdrop、終了、旧generationでは要求を破棄します。内部NivisViewer dragをfolder／tree／favoriteへ落とす既存copy／move経路は維持します。

中央dropは`ExplorerListView`だけでなく、実際のnative drop targetである`viewport()`へevent filterを設定します。IconMode等の設定でviewportが再構成された後に`acceptDrops`とfilterを再適用します。`application/x-nivisviewer-paths+json`かつNivisViewer自身がsourceのdragだけを内部操作とし、Explorerの`text/uri-list`は項目上／空白上を問わずfocus-onlyへ送ります。HTTP／HTTPSは受理しません。

`browser_external_drop_behavior=focus_only`が既定です。`focus_and_open`ではfocus確定後、primaryがNivisViewer対応項目の場合だけ既存`open_viewer_behavior`に従って開きます。自動選択は一覧へkeyboard focusを移さず、Viewerを前面へ出し直しません。

Viewer画像領域の通常の左クリックは`next_one_page()`へ接続し、論理ページを1ページだけ進めます。矢印キー、ホイール、メニューの前／次ページは従来どおり`PageModel.next()`／`previous()`による表示単位移動で、見開きでは通常2ページずつ進みます。

### Sprint 19のExplorer型ファイル操作

```text
ApplicationController
└─ FileOperationQueue（アプリ共通・直列）
   ├─ FileOperationPlanner（Qt非依存の事前計画）
   │  └─ FileOperationPlan / FileConflict
   ├─ ConflictResolutionDialog（Model/View）
   └─ FileOperationService
      └─ ChunkedFileCopier
```

Sprint 19追補では次の境界を追加します。

```text
FileOperationQueue
└─ FileOperationService
   └─ MovePostconditionVerifier

PreviewProviderRegistry
└─ VideoThumbnailPolicy
   ├─ Shell placeholder
   ├─ one-third representative frame
   └─ smart representative frame

BrowserWindow
└─ BrowserMainDropController
   ├─ viewport event filter
   ├─ external/internal MIME split
   └─ PendingBrowserFocusRequest
```

MOVE成功はdestinationが存在し、かつsourceが存在しない場合だけです。同一volumeは`rename`／`replace`後にもこの事後条件を確認し、`EXDEV`だけを一時出力経由のcopy＋deleteへ戻します。destination公開後にsourceが残った場合は`SOURCE_REMOVAL_FAILED`または`DESTINATION_PUBLISHED_SOURCE_REMAINS`で部分成功とし、成功件数、MetadataStore relocate、cut bufferから除外しません。結果はdestination/sourceの存在、公開／削除状態、copy bytes、expected bytes、残留source pathを保持します。

動画は既定`smart`で再生時間の1/3、1/2、2/3から少数候補を評価し、黒／白／単色／低分散／低edge energyのframeを避けます。`one_third`は再生時間の約1/3を使い、終端直前を避けます。Shell画像は設定した場合だけmemory-only placeholderとし、FFmpeg結果を最終表示します。ShellとFFmpegのどちらもrotation、SAR／DAR補正後に共通のthumbnail ratio、letterbox／center crop／smart crop、物理cache sizeへ通します。PREFETCH missではFFmpegを起動しません。

### FavoriteNavigation

```text
FavoriteNavigation
├─ release-confirmed click
├─ immediate navigate request
├─ no synchronous validation
├─ interactive scan priority
├─ first-batch commit
└─ deferred tree synchronization
```

mouse press、release、navigation確定、generation、scanner登録／worker、path確認、scandir、first batch、GUI到着、model反映、first paint、tree sync、thumbnail要求は、DEBUGまたは`NIVISVIEWER_DEBUG_TIMING=1`時だけ同じperformance trace IDへ記録します。通常ログには出しません。

favorite、アドレス入力、戻る／進む、treeなどの明示移動は`INTERACTIVE_NAVIGATION`としてscanner queueへ登録します。GUI threadでは存在・種類・アクセスを検証せず、scannerが分類します。最新navigation generationが常に勝ち、旧batch／error／completeは破棄します。同じpathへのfavorite移動は再scan、履歴追加、tree再同期を行いません。

通常folderは最初の有効batch、空folderは正常completeでcommitします。favorite release直後はcached pathをアドレス欄へ置き、読み込み中を表示しますが、履歴と`last_browser_path`はcommitまで更新しません。失敗時は前の一覧、履歴、アドレスへ戻します。first batchをmodelへ反映して一覧が一度paintされた後、独立generationのtree syncをqueued実行し、その後に可視／選択／限定prefetchのthumbnail要求を始めます。

コピー／移動はGUIスレッドでファイルを列挙せず、worker上の`FileOperationPlanner`が絶対パス化、重複と親子選択の整理、同一／子孫移動の拒否、ファイル・フォルダ件数とbyte数、空き容量、衝突を先に確定します。再解析ポイントとsymlinkは再帰しません。状態は`PREPARING`、`WAITING_FOR_CONFLICTS`、`READY`、`RUNNING`、`CANCELLING`、`COMPLETED`、`FAILED`、`CANCELLED`を区別し、衝突待ちを含めてアプリ全体で一度に1操作だけ進めます。

衝突はfile/file、directory/directory、file/directory、directory/file、case-only、same-path、移動先欠損／読み取り専用、無効名に分類します。既定はskipです。`ConflictResolutionDialog`は全衝突を1つのscroll可能なtableへ表示し、skip、両方残す、replace、folder merge、同種への一括適用、全体cancelを選べます。replace対象をViewerが使用中の場合は、実行前に該当Viewerだけを閉じてhandle解放を待ちます。copy元を閲覧中のViewerは閉じません。

通常ファイルは4 MiB単位の`ChunkedFileCopier`で読み、byte進捗、速度、ETAを最大約75 ms間隔で通知します。出力は移動先と同じ親の一時名へ完成させ、metadataを反映してからrename／`os.replace`で公開します。replace失敗またはcancelでは既存の移動先を保護し、一時物を除去します。folder mergeは同名folder全体を先に削除せず、子項目ごとに同じ規則を適用します。同一volume moveはrenameを優先し、`EXDEV`だけをcopy後deleteへ切り替えます。copy完了後の元削除だけが失敗した場合はpartial successとして元を残します。

`FileOperationQueue`はBrowserのpaste、指定先、tree／favorite dropを同じpreflightへ通します。`FileOperationPanel`は非モーダルに項目／byte進捗とcancelを表示し、成功時は自動的に隠れ、失敗情報は残します。Browserを閉じてもViewerが残る場合、または最後のウィンドウで「続行」を選んだ場合は最小の操作パネルを表示します。最後のウィンドウを閉じる際は続行、cancelして終了、終了中止を選択でき、shutdownは冪等でworkerを危険に強制停止しません。

成功したcopy／moveの移動先だけを`DestinationHistoryStore`へ最大15件保存します。この履歴はconfig内のポータブルデータで、メニュー表示時に同期statを行いません。MetadataStore、Browser履歴、選択復元、cache無効化は成功項目だけへ適用します。

将来のUndoは、現在の短期表示履歴をそのまま逆実行するのではなく、実行前後のidentityと成功した原子的手順だけを記録する専用journalを`FileOperationQueue`へ接続します。pause／resume、再試行、ネットワーク転送再開も今回のcancel境界とは分離して後続実装とします。

### Viewer最優先の画像作業調整

ApplicationControllerは`ImageWorkCoordinator`を1つ所有し、すべてのViewerWindowのImageCacheとBrowserThumbnailProviderへ共有注入します。通常の最大画像worker数は2で、1枠をViewer専用、1枠をBrowser専用とします。Viewer専用枠をBrowserへ貸し出さないため、Browser background decodeだけで全枠を占有しません。Browser枠は最大1件だけ実行します。

優先度は`VIEWER_CURRENT`、`VIEWER_SPREAD_PARTNER`、`VIEWER_INTERACTIVE_RERENDER`、`VIEWER_NEXT`、`VIEWER_PREVIOUS`、`BROWSER_VISIBLE`、`BROWSER_SELECTED`、`BROWSER_PREFETCH`の順です。同じ要求はImageCacheまたはThumbnailProviderで重複抑止し、未開始の要求がcurrent／visibleへ変わった場合はqueueから取り出して昇格します。実行中のdecodeは強制停止せず、generationとimage IDで後着結果を破棄します。

ViewerWindowは明示openの冒頭で`interactive_open_started`を通知します。ApplicationControllerはfirst frame gateを開始し、新規Browser decodeを保留して未開始要求を取り消します。ImageCacheは論理currentだけを最初に登録し、ViewerWidgetがそのpixmapを実際にpaintした時点で`first_frame_ready`を通知します。その後、見開き相方、前後ページとBrowser処理を再開します。open失敗、置換、Viewer終了は`interactive_open_cancelled`で必ずgateを解放します。Browserのメモリcache hitと実行中decodeは継続できます。

見開きでは論理currentを視覚上のRTL／LTR位置に関係なく最初に要求します。相方が未到着でもcurrentを描画し、相方位置には既存の軽量placeholderを置きます。相方到着時は同じDisplaySpreadへ追加するだけで、パン、ズーム、slider、ページ順を変更しません。

Browserから単体画像を開く場合は、現在モデルが持つ画像path順、選択画像とそのindex、size、mtime、scan generation、sort／visibility identityの`FolderListingSnapshot`を渡します。通常の非再帰openではFolderImageSourceがこのsnapshotをbook-session lifetimeのnavigation topologyとして保持し、同じフォルダを再列挙・再sortしません。mouse、keyboard、wheel、PageList、history、requested／displayed stateは同じPageModel順を参照します。Browser側のsort変更は既に開いているbookを組み替えず、plain Viewer reloadもopen時snapshotを維持します。Browserから再openした時点で新しい表示順へ切り替わります。Viewerの明示的な「逆順で読む」／recursive切替は意図されたtopology overrideなのでsnapshotを外します。起動引数、Explorer、command line、外部dropなどBrowser authorityがない経路は従来どおりFolderImageSourceが自然名前順で列挙します。

Folder／ZIP／外部書庫はpage sizeをlazy扱いとし、PageModel構築時に全ページのfull decodeや全header走査を行いません。未確定サイズは暫定的な通常ページとして局所的に組み、currentのdecode結果が届いた時だけその周辺の横長／単独判定を更新します。PDFはdocument metadataに全ページ寸法が既にあるため、従来の論理サイズを使用します。

### WebP decode pipeline

WebPはImageCacheのworker内で処理します。file-backed WebPはbytesを短時間で読み終えてファイルhandleを閉じ、worker-localの`QBuffer`と`QImageReader("webp")`、`setAutoTransform(True)`で先頭frameをQImageへ直接decodeします。ZIP内WebPも展開bytesを同じQBuffer経路へ渡します。QImageReaderまたはQt WebP pluginが利用できない場合だけPillowへ戻ります。

Pillow fallbackは先頭frameへ固定し、EXIF transposeを一度だけ適用します。RGB、RGBA、Lを維持し、palette等で必要な場合だけRGB／RGBAへ変換します。QImageはPillow bufferから必ずcopyし、buffer破棄後も安全に保持します。明るさ、コントラスト、ガンマが既定値なら追加mode変換を行いません。decode済みQImageはImageCacheに保持し、zoom、resize、fit、pan、joined spread切替では元WebPを再decodeしません。currentとspread partnerはLRU保護対象です。

`performance_trace.py`はDEBUG loggingまたは`NIVISVIEWER_DEBUG_TIMING=1`のときだけ、Controller open、Source準備、page list、PageModel、要求登録、worker開始、WebP decode、Pillow→QImage、ImageCache、結果到着、first paintを記録します。通常利用時はイベントを保存せず詳細ログも出しません。Browser pending件数もopen開始イベントへ含めます。

### ウィンドウの寿命

- アプリ起動時はBrowserWindowを表示する
- BrowserWindowがある間は、すべてのViewerWindowを閉じてもアプリを継続する
- BrowserWindowを閉じてもViewerWindowが残っていればアプリを継続する
- BrowserWindowと全ViewerWindowの両方がなくなったときだけ、ApplicationControllerが終了を一度要求する

## Stability Sprint: Repository Review P1 fixes

```text
ApplicationController
├─ AdjacentBookSearchService
├─ PathAvailabilityService
├─ PdfiumService
└─ FileOperationQueue
```

### AdjacentBookSearchService

```text
AdjacentBookSearchService
├─ Browser snapshot reuse
├─ asynchronous filesystem search
├─ lexical path keys
└─ generation cancellation
```

前／次の本の要求は、GUI threadではcurrent pathの字句的な絶対化、request
ID／generationの発行、Browser snapshotの取得、worker登録だけを行う。
`exists()`、`is_dir()`、`is_file()`、`resolve()`、`iterdir()`、`stat()`、
`scandir()`は候補探索のGUI経路では呼ばない。Browserが同じ親フォルダの
commit済み一覧を保持している場合はpath、item kind、extension、自然順identity、
scan generationをsnapshotとして再利用する。画像を含む兄弟folderかどうかなど
snapshotだけで確定できない情報はworkerが確認する。

候補cacheのfingerprintもworkerだけが取得する。Browser rescan、ファイル操作完了、
明示invalidateでcacheを無効化できる。最新request generationだけを対象Viewerへ
適用し、逆方向の連続操作、直接open、Viewer close、Application shutdown後の結果を
破棄する。実行中のOS I/Oは強制停止しない。

### Cached path availability

```text
HistoryModel / BookmarkModel / FolderBookmarkModel
└─ cached availability state
   └─ PathAvailabilityService asynchronous probe
```

MetadataStoreの`HistoryEntry`と`BrowserBookmark`は純粋データであり、property参照で
ファイルシステムへ問い合わせない。Modelの`data()`は`UNKNOWN`、`CHECKING`、
`AVAILABLE`、`MISSING`、`UNAVAILABLE`、`ERROR`のcacheだけを読む。paint、scroll、
tooltip、accessibility roleの評価を契機にprobeを増やさない。同一lexical pathの
pending probeはdeduplicateし、model generationが一致する結果だけをGUI threadで
反映する。切断UNCや取り外し媒体は`UNAVAILABLE`として`MISSING`と区別し、一時的な
確認失敗によって履歴やブックマークを自動削除しない。

### PdfiumService lifecycle

```text
PdfiumService
├─ RUNNING
├─ SHUTTING_DOWN
├─ pending cancellation
├─ close-all control barrier
└─ STOPPED
```

shutdown開始後は新規open／renderを受理せず、未開始jobのFutureをcancelledで必ず
完了する。実行中の最大1件は危険に停止せず、その直後に通常render priorityとは
独立したclose-all control barrierを実行する。全documentをworker threadでclose
した後にworkerを停止し、そこで初めて`STOPPED`へ遷移する。`close_document()`も
同documentの未開始renderをcancelし、無関係なpending renderより先にhandle解放を
行う。shutdownとcloseの多重要求は冪等とする。

### Merge MOVE result contract

```text
FileOperationService
└─ MergeMoveResult
   ├─ child results
   ├─ published destinations
   └─ residual sources
```

folder mergeは各childについてoperation kind、state、置換有無、destination公開、
source削除、bytes、errorを`FileOperationItemResult`へ記録する。root resultは
`child_results`、`published_destination_paths`、`moved_source_paths`、
`residual_source_paths`、`skipped_source_paths`、`failed_source_paths`、
再貼り付け用の`retry_source_paths`、source root削除結果を集約する。
`rmdir()`失敗は握り潰さない。

merge MOVEの完全成功は、全childが完全MOVED、source root不存在、residual／skip／
failureなしの場合だけである。部分成功時のcut bufferは移動済みchildを除外し、
残留する直下項目だけを再構成する。MetadataStoreとBrowser navigationのpath更新も
完全成功したchild単位で適用する。

### Replacement metadata policy

```text
MetadataStore
├─ copy replace reset
├─ move replace transactional relocation
└─ partial move replacement policy
```

実schemaの分類は次のとおり。

- 内容依存：`library_items`のfile size、mtime、identity、rating、comment、
  `reading_history`のreading position／open history／page count、`item_tags`
- path指向：`browser_bookmarks`のlabel、item type、sort order、created time
- 共有語彙：`tags`。作品との結び付きは`item_tags`側の内容依存情報

COPY＋REPLACEはdestinationの旧内容依存metadataだけをtransactionでresetし、
source metadataを変更・複製しない。destination path bookmarkは維持する。
完全なMOVE＋REPLACEはdestination旧内容をresetした後、source内容metadataを
destinationへtransaction内でrelocateする。destination bookmarkを優先し、
source bookmarkは競合がない場合だけ移す。destination公開後にsourceが残った
partial MOVEはCOPY＋REPLACE相当としてdestinationをresetし、source metadataを
元pathに維持する。skip、cancel、publish失敗ではDBを変更しない。

## Stability Sprint P2: startup, diagnostics, and deterministic shutdown

### 現行コンポーネント

```text
ApplicationController
├─ StartupRestoreCoordinator
│  └─ PathAvailabilityService
├─ ApplicationShutdownCoordinator
├─ FileOperationQueue lifecycle
├─ PdfiumService
│  └─ cached PdfAvailabilitySnapshot
└─ BrowserWindow
   ├─ HistoryModel
   ├─ BookmarkModel
   └─ FolderBookmarkModel
      └─ shared PathAvailabilityService

PreviewProviderRegistry
└─ WindowsShellPreviewService
   └─ dedicated STA thread lifecycle
```

起動時はBrowserWindowを先に表示し、`last_open_path`を字句的に正規化してから
`StartupRestoreCoordinator`が共有`PathAvailabilityService`へprobeを要求する。
GUI threadは`exists()`、`is_dir()`、`is_file()`、`stat()`、`resolve()`で起動対象を
確認しない。AVAILABLEだけを既存open経路へ渡し、MISSING、UNAVAILABLE、ERRORでは
現在UIと設定値を維持して非モーダル通知する。`--no-restore`はprobeを発行せず、
CLI／単一instanceからの明示openは進行中のrestore generationをcancelする。

Viewerのrecentと場所表示も同じserviceを使用する。recentの失敗は現在の本を閉じず、
履歴を自動削除しない。連続要求は最新generationだけを適用する。HistoryModel、
BookmarkModel、FolderBookmarkModelはApplicationController所有の同一serviceを
constructor injectionで共有し、paintではcached stateだけを読む。Modelごとの
module-global poolは持たない。

`PdfiumService`は`UNKNOWN`、`CHECKING`、`AVAILABLE`、`UNAVAILABLE`、`ERROR`、
`STOPPED`の`PdfAvailabilitySnapshot`を保持する。初期probeと明示再確認だけを
control priorityで実行し、診断ダイアログはcached snapshotを100 ms間隔で表示へ
反映するだけである。ダイアログopenはPDF Future、Event、render queueを待たず、
新しいrender jobやPDF document openを発行しない。

### 設計原則とshutdown順序

`FileOperationQueue`は`RUNNING`、`SHUTTING_DOWN`、`STOPPED`を持つ。shutdown開始後は
enqueueと新しい衝突dialogを拒否し、待機項目をcancel、active項目へcancel tokenを
通知する。完了は`shutdown_finished`、bounded timeoutは`shutdown_failed`で通知し、
終了経路では`processEvents(AllEvents)`を使わない。最後のWindowでcancel終了を
選んだ場合はcloseを一旦保留し、入力とD&Dを無効化してqueue完了後に一度だけcloseを
再要求する。timeoutではworkerを強制停止せずWindowを維持する。

ApplicationControllerの現行終了順は次のとおり。

1. controllerをSHUTTING_DOWN相当にし、新規open／navigation／operationを拒否
2. startup restoreをcancelし、Window入力を無効化
3. FileOperationQueueの待機項目cancelとactive項目の安全な終了
4. Viewer／BookSession、Browser scanner／thumbnail／previewの新規要求停止
5. ArchiveBackendRegistryとAdjacentBookSearchをclose
6. PathAvailabilityServiceを非ブロッキングにclose
7. PdfiumServiceのpending cancel、close-all barrier、bounded join
8. ImageWorkCoordinatorをshutdown
9. ConfigManager save
10. MetadataStore flush／close

`ApplicationShutdownCoordinator`はこの後半を一度だけ実行する。開始時の
`ApplicationShutdownSnapshot`はfile operation、PDF、Shell preview、path probe、
thumbnailのpending件数を保持し、通常ログへ大量出力せず診断に利用できる。

`WindowsShellPreviewService`は`RUNNING`、`SHUTTING_DOWN`、`STOPPED`、`STUCK`を
区別する。未開始taskをcancel完了し、worker内でCOM interface、HBITMAP、DCを解放して
`CoUninitialize`した後にbounded joinする。native Shell callが戻らない場合は
強制停止せず`STUCK`と`last_shutdown_error`を残し、後着画像をUIへ適用しない。

## Critical Follow-up: CUT、見開きslot、外部open

```text
InternalClipboardState
├─ COPY
├─ CUT
├─ normalized absolute cut paths
└─ operation request identity

BrowserItemModel
└─ path-based CutRole
   └─ BrowserItemDelegate cut visual opacity

ViewerDisplayUnit
├─ left slot state
└─ right slot state
   ├─ EMPTY
   ├─ LOADING
   ├─ READY
   ├─ FAILED
   └─ CANCELLED

SystemFileOpener
├─ default Windows association
└─ application picker fallback
```

NivisViewer内で開始したCUTは`InternalClipboardState`をoperation kindのSource of
Truthとする。OS clipboardはURL転送に使うが、Qtから遅れて届くclipboard変更通知で
CUTをCOPYへ上書きしない。COPY／CUTの切替、外部clipboard置換、完全MOVE、部分MOVE、
終了時にpath集合を更新し、`BrowserItemModel.CutRole`は該当pathの行だけを再描画する。
row番号や`QModelIndex`は保持しないため、sort、scan reset、別フォルダへの移動後も
同じabsolute pathへCUT表示を復元できる。delegateはthumbnail、fallback icon、
filename、type badgeを0.52 opacityで描き、selection／focus／hover／errorは通常濃度で
後描画する。

MOVEの完全成功条件は、destinationが公開済みかつsourceが物理的に不存在であること。
同一volumeはrenameを優先し、`EXDEV`だけを一時copy、flush／fsync、publish、
source削除の経路へfallbackする。destinationが完成してもsourceが残る場合は
`DESTINATION_PUBLISHED_SOURCE_REMAINS`または`SOURCE_REMOVAL_FAILED`であり、
完全成功とは扱わない。部分成功時は移動済みpathだけCUT集合から除外し、
skip／failure／residual pathを再貼り付け候補として残す。

`ViewerDisplayUnit`は表示順にleft／right（単独時はcenter）のslotを作り、各slotへ
page identity、request id、image-cache generationを保持する。ready／failed結果は
一致するslotだけを遷移させ、旧generationは適用しない。Browserから遅延寸法の
選択ページを直接開いた際、初回frame gateのdecode中心は見開き先頭の
`current_index`ではなく論理選択の`focused_index`とする。寸法確定で見開き先頭が
補正されても選択ページをcacheから退避させず、最初のpaint後に相方とprefetchを
開始する。decode failure、source unavailable、表示単位置換でもLOADINGを終了する。

Browserのfolderは従来どおり内部navigationへ渡し、NivisViewer対応形式は内部Viewerへ
渡す。`BrowserItem.openable_by_nivisviewer`がfalseの通常fileはdouble click、Enter、
context openから`SystemFileOpener`へ一度だけ渡す。Windowsではwide-character Shell
APIを使用し、関連付けがない場合だけapplication pickerへfallbackする。文字列結合した
`cmd.exe`や`shell=True`は使用しない。対応拡張子の破損・access denied・decode errorは
自動的に外部openせず、明示的な「既定のアプリで開く」だけを提供する。

## Critical File Operation Repair: canonical staging lifecycle

```text
FileOperationArtifactPolicy
├─ canonical staging name
├─ internal artifact detection
├─ nested artifact rejection / recovery derivation
├─ atomic publish
└─ orphan detection

FileOperationService
├─ stage
├─ verify
├─ publish
├─ remove source
└─ cleanup

BrowserScanner
├─ normal extensionless file
└─ unconditional internal artifact exclusion

ConflictPresentationModel
├─ source details
└─ destination details
```

ファイル／cross-volume MOVEのstagingは、必ず正規のfinal destinationから
`.<final-name>.nivisviewer-<operation-id>-<item-id>.tmp`を一度だけ生成する。
`FileOperationService`がstaging lifecycleを所有し、`ChunkedFileCopier`は指定された
stagingへbyte-for-byteで書き、flush、fsync、size検証、metadata適用を行う。
CopierはServiceから渡されたstagingを基準に別のstagingを生成しない。既存artifactを
`create_staging_path()`へ渡した場合は、検証可能な層を剥がして元のfinalを復元してから
新しいcanonical名を作るため、`.tmp.nivisviewer-...tmp`を生成しない。

publishは`os.replace(staging, final)`で行い、finalが存在しstagingが存在しないことを
事後条件とする。publish前のcancel／failureではartifactだけをcleanupしてsourceと既存
finalを維持する。publish後にMOVE sourceが残った場合は部分成功であり、完成finalを
削除せずCUT stateをsource側へ残す。cleanup失敗は`artifact_paths`と
`cleanup_errors`を構造化結果へ保持し、操作パネルの詳細へ表示する。既存orphanは
未回収データの可能性があるため自動削除せず、DEBUG診断へderived final、nested、
size、mtime、final存在だけを記録する。

内部artifact判定は`FileOperationArtifactPolicy`へ一元化する。BrowserScannerの列挙
最上流、BrowserItemModel、Planner、Service、内部clipboard、D&D MIME、外部openの
境界で常に除外／拒否する。show hidden／unsupported／systemを有効にしても表示しない。
一方、suffixが空の`README`、`LICENSE`、日本語名、zero-byte file、および一般利用者の
通常`.tmp`はartifactではない。unsupported generic itemとして表示し、Shell iconと
`SystemFileOpener`を利用できる。

`ConflictPresentationModel`は各衝突のsource／destination name、absolute path、size、
mtime、file／folder kind、`1 / N`、現在actionを保持する。実dialogは選択行が変わる
たびに全labelとtooltipを更新し、長いpath、日本語、空白、拡張子なしでもsourceと
destinationを取り違えない。

### 将来接続点

応答しないUNC／Shell native callをprocess外へ隔離する構成、非モーダルな
shutdown詳細パネル、SingleInstanceBrokerをshutdown coordinatorへ直接接続する処理、
frozen版の異常終了snapshot永続化は未実装の接続点であり、現行機能として扱わない。

## 次の構成

次段階では既存ImageSourceとPDFのtarget-aware renderingを踏まえてPageSource抽象化を進めます。基本操作と対応形式の安定後にMetadataStoreのレート／タグAPIへ編集UI、検索、絞り込み、サムネイル上の表示を接続します。ZipPlaの`{zpi$...}`は明示的な読み取り互換から始め、元ファイルへ自動的に書き戻さない境界を維持します。

`ApplicationController`は引き続きアプリ全体の寿命、共有設定、単一MetadataStore、ウィンドウ群、ウィンドウ間イベントを管理します。`BrowserWindow`は本を探して選ぶ責務、`ViewerWindow`はBookSessionとViewerWidgetを接続して読む責務を持ちます。PageModelはGUIに依存しない状態を保ちます。

## 設計上の決定事項

- `BrowserWindow`と`ViewerWindow`は、同一アプリ・同一プロセス内の別ウィンドウとする。
- `BrowserWindow`と`ViewerWindow`は、選択中の本について連動する。
- `BrowserWindow`から本を開いた際は、`ViewerWindow`を一度だけ前面へ表示する。
- `ViewerWindow`を常時最前面にはしない。
- `ViewerWindow`を再利用するか新規作成するかは設定可能にする。
- マウスの戻る／進むボタンは、BrowserWindowではフォルダ履歴、ViewerWindowでは前／次の書庫へ割り当てる。
- 最後の書庫から先頭へ戻るループは設定可能にする。
- 右クリックドラッグのマウスジェスチャーは、通常の右クリックと排他的に判定し、操作割り当てを設定可能にする。
- サムネイルサイズは設定画面から変更する。
- 設定、履歴、ブックマーク、キャッシュはポータブル配置を基本とする。
- RAR／7z／CBR／CB7は利用者環境のWinRAR／7-Zip backendを使用し、PDFは別Sourceで対応する。
- 入力形式ごとの差異を吸収する`PageSource`抽象化への移行を予定する。
