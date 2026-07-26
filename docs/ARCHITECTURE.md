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
   └─ ViewerWidget
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
- `BrowserItemModel`: BrowserItemの表示名、絶対パス、種類、元項目のmtime、ファイルサイズ、表示アイコンをQt Model/Viewへ公開し、BrowserSortPolicyで保持リストを並べ替える
- `BrowserSortPolicy`: QtやMetadataStoreに依存せず、自然順、更新日時、種類、サイズ、昇降順、フォルダ優先を一元管理
- `BrowserThumbnailScheduler`: viewport、gridSize、スクロール位置から可視行、選択行、通常時は前後1画面の先読み行を計画
- `BrowserThumbnailProvider`: 最大2スレッドの専用`QThreadPool`で画像、画像フォルダ、ZIP/CBZ/RAR/CBR/7z/CB7のサムネイルを生成し、メモリLRUキャッシュを管理
- `ThumbnailDiskCache`: SQLiteインデックスとWebPまたはPNGファイルによるポータブルな永続サムネイルキャッシュ
- `SettingsDialog`: Viewerの開き方、見開き表示、Browserのサムネイルとディスクキャッシュ、マウス操作割り当て、外部書庫backend選択、WinRAR／7-Zipの自動検出／明示パスを編集
- `ViewerWindow`: 閲覧メニュー、ダイアログ、入力、ViewerWidgetへの描画、ページ移動、全画面などウィンドウ固有UIを管理し、キー・メニュー・マウス入力を共通コマンドへdispatch
- `FullscreenChromeController`: 全画面時の上下端検出とmenu／slider／statusのoverlay表示、自動非表示、通常配置への復元を担当
- `BookSession`: 現在のパス、ImageSource、PageModel、ImageCache、読み込み世代、外部書庫の非同期prepare→commit、ソース切替と終了を管理
- `ConfigManager`: 実行ファイル基準の`config.json`を読み書きし、メタデータDBやキャッシュの配置基準も提供するポータブル設定管理
- `ImageSource`: フォルダ、単体画像の親フォルダ、ZIP/CBZ、外部backend書庫を共通化する画像供給層
- `PageModel`: 論理ページ順と単ページ／見開きの表示単位を管理する非GUIモデル
- `ImageCache`: セッション専用`QThreadPool`で画像を非同期デコードし、LRU形式で保持するキャッシュ
- `ViewerWidget`: 渡された画像の描画、拡大縮小、パン、クリックやホイール入力、追加ボタン検出、ジェスチャー軌跡オーバーレイを担当
- `MouseGestureRecognizer`: QtやGUI状態に依存せず、移動量をU/D/L/Rへ量子化して連続方向を圧縮
- `PageThumbnailProvider`: QImageからQtアイコンへの変換を担当し、ViewerWindowのページ一覧とBrowserWindowの一覧で利用

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

並び替えキーは`name`、`modified_time`、`item_type`、`file_size`です。名前はnatsortによる大文字小文字を過度に区別しない自然順とし、同一判定時は絶対パスで安定化します。更新日時とサイズが同じ項目、および同じ種類の項目は名前の自然順を二次順序にします。種類はフォルダ、書庫、PDF、画像、その他の順です。未対応形式も設定に従って`other`として保持しますが、NivisViewerのthumbnail decodeやViewer openへ渡しません。

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

ファイルコピーは`shutil.copy2()`、フォルダコピーはcopy2を使う再帰コピーにより、対象自身のmtimeを可能な範囲で維持します。コピーは同じ親の一時名へ完了させてからrenameし、キャンセルまたは失敗時はNivisViewerが作成した一時コピーだけを回収します。クロスボリューム移動はコピー完了前に元項目を削除せず、キャンセル時は確定済みコピーをロールバックして元を維持します。子の追加・削除で変化する親フォルダmtimeは通常のOS動作として許容します。MetadataStoreの更新日時を元ファイルmtimeへ書き戻しません。

同名項目は自動上書きしません。Sprint 10のBrowserWindowは複数衝突でダイアログを連続表示せず、既定ですべてスキップして終了時に件数とエラー種別を一度だけ通知します。FileOperationServiceには拡張子を維持した「`book - コピー.zip`」「`book - コピー (2).zip`」形式の別名生成と`FileCollisionPolicy.RENAME`を用意し、将来の衝突選択UIから利用できるようにしています。

通常のDeleteとコンテキストメニューの「ごみ箱へ移動」はWindows Shellの`SHFileOperationW`へ`FOF_ALLOWUNDO`、`FOF_NOCONFIRMATION`、`FOF_NOERRORUI`を指定します。NivisViewerが対象件数または単一名を一度確認するため、OS確認ダイアログを重ねません。Shell API失敗、キャンセル、API利用不能、操作後も元パスが残る場合は失敗として返し、`os.remove()`や`shutil.rmtree()`による永久削除へ切り替えません。Shift+Deleteと完全削除APIは未実装です。

renameとmoveの成功後だけMetadataStoreのパスをSQLiteトランザクションで追従させます。フォルダ操作では配下のlibrary_itemsをprefix置換し、reading_history、browser_bookmarks、rating、tags、commentは同じlibrary_itemとの関連を維持します。新パスに既存項目がある場合は、最新の読書履歴、open_countの合算、タグの和集合、既存側優先のrating/commentという安全な統合を行い、UNIQUE制約を破壊しません。トランザクション失敗時はロールバックします。コピーではメタデータを複製せず、ごみ箱移動では履歴やブックマークを即時削除しません。

BrowserNavigationHistoryもrename/move成功後だけフォルダパスと選択パスをprefix置換し、連続する同一フォルダ履歴を重複させません。操作後の一覧はSprint 9の非同期scannerで、履歴を追加せず再読込します。rename、現在フォルダへのcopy/move、新規フォルダは新パスを選択し、ごみ箱移動は削除位置に近い項目を選択します。scan generationとthumbnail generationを維持するため、旧scan結果や旧パスのサムネイル結果を新項目へ適用しません。旧ディスクキャッシュは即時移管せずLRU回収に任せ、新パスで通常のfingerprint検索を行います。

コピー以外の変更対象が開いているFolderImageSource、単体画像、その親フォルダ、ZIP／CBZ、または操作対象フォルダ配下にある場合、ApplicationControllerが影響するViewerWindowだけを抽出します。BrowserWindowは操作前に一度確認し、ユーザーが続行を選んだ場合だけ対象Viewerを通常のclose経路で解放してからworkerを開始します。キャンセルではViewerを閉じず、無関係なViewerとBrowserWindowは維持します。コピーは元項目を変更しないためViewerを閉じません。

ドラッグ＆ドロップ、永久削除、OS標準の高度なUndo、ZIP／CBZ内部エントリの変更は後続課題です。

### 可視範囲優先サムネイル

BrowserThumbnailSchedulerは`ScrollPerPixel`のQListViewについて、viewport、gridSize、スクロール値、モデル件数から可視行を定数時間で近似します。優先順位はVISIBLE、SELECTED、PREFETCHです。可視範囲を先に要求し、その前後各2画面だけを先読みします。1万件でも要求数は可視範囲と限定先読みに収まり、全行の`visualRect()`走査や全件要求を行いません。

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

共有設定には表示モード、綴じ方向、フィットモード、余白、表紙・横長画像の扱い、背景色、`open_viewer_behavior`、書庫移動ループ、前面表示設定、マウスジェスチャーと追加ボタンの割り当て、`archive_backend_preference`、`winrar_executable`、`seven_zip_executable`などが含まれます。

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

FullscreenChromeControllerはfullscreen、UI非表示設定、上下overlay、pointer領域、popup／modal、slider、mouse button、timer generation、window-local cursorを単一の`reconcile_state()`で調停します。通常menu barとstatus barはQMainWindow配下のまま全画面中は常に隠し、fullscreen専用menu/statusをoverlayへ表示するため、後続の`menuBar()`／`statusBar()`呼び出しで通常UIが再生成・残留しません。上端／下端の既定8 logical px（4～32）で該当側だけを表示し、離れた次のevent loop（設定範囲0～3000ms、既定0ms）で隠します。cursorはViewerWindow配下だけへBlankCursorを設定し、600～1000msのidle、mouse move、edge UI、popup、slider、fullscreen解除、window終了を同じControllerで管理します。

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

お気に入りはpress時にnavigateせず、同一項目上のreleaseから発生するsingle clickをdouble-click interval内で確定します。待機対象は`QModelIndex`ではなくpathで保持し、並べ替え後に再解決します。dragが成立した入力列ではclickを発生させません。`FavoriteRowMetrics`の行高は`max(fontMetrics.height(), icon_size) + padding_y * 2`だけで決まり、wrapなし、elideあり、行間隔と14～24pxのfolder iconを独立設定します。

### Viewer最優先の画像作業調整

ApplicationControllerは`ImageWorkCoordinator`を1つ所有し、すべてのViewerWindowのImageCacheとBrowserThumbnailProviderへ共有注入します。通常の最大画像worker数は2で、1枠をViewer専用、1枠をBrowser専用とします。Viewer専用枠をBrowserへ貸し出さないため、Browser background decodeだけで全枠を占有しません。Browser枠は最大1件だけ実行します。

優先度は`VIEWER_CURRENT`、`VIEWER_SPREAD_PARTNER`、`VIEWER_INTERACTIVE_RERENDER`、`VIEWER_NEXT`、`VIEWER_PREVIOUS`、`BROWSER_VISIBLE`、`BROWSER_SELECTED`、`BROWSER_PREFETCH`の順です。同じ要求はImageCacheまたはThumbnailProviderで重複抑止し、未開始の要求がcurrent／visibleへ変わった場合はqueueから取り出して昇格します。実行中のdecodeは強制停止せず、generationとimage IDで後着結果を破棄します。

ViewerWindowは明示openの冒頭で`interactive_open_started`を通知します。ApplicationControllerはfirst frame gateを開始し、新規Browser decodeを保留して未開始要求を取り消します。ImageCacheは論理currentだけを最初に登録し、ViewerWidgetがそのpixmapを実際にpaintした時点で`first_frame_ready`を通知します。その後、見開き相方、前後ページとBrowser処理を再開します。open失敗、置換、Viewer終了は`interactive_open_cancelled`で必ずgateを解放します。Browserのメモリcache hitと実行中decodeは継続できます。

見開きでは論理currentを視覚上のRTL／LTR位置に関係なく最初に要求します。相方が未到着でもcurrentを描画し、相方位置には既存の軽量placeholderを置きます。相方到着時は同じDisplaySpreadへ追加するだけで、パン、ズーム、slider、ページ順を変更しません。

Browserから単体画像を開く場合は、現在モデルが持つ画像path順、選択画像、size、mtimeの`FolderListingSnapshot`を渡します。通常の非再帰openではFolderImageSourceがこのsnapshotを使用し、同じフォルダを再列挙しません。起動引数、Explorer、履歴などsnapshotがない経路は従来どおりSourceが列挙します。

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
