# Architecture

## 現在の実装

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
│  ├─ NavigationToolbar
│  ├─ DisplaySettings
│  ├─ AddressBar
│  ├─ BookmarkModel
│  ├─ HistoryModel
│  ├─ BrowserItemModel
│  │  └─ BrowserSortPolicy
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
- `BrowserItemDiscovery`: サブフォルダ、ZIP/CBZ/RAR/CBR/7z/CB7、対応画像を列挙し、列挙時のmtimeとファイルサイズをBrowserItemへ保存する。画像のデコードや書庫の展開は行わない
- `BrowserItemModel`: BrowserItemの表示名、絶対パス、種類、元項目のmtime、ファイルサイズ、表示アイコンをQt Model/Viewへ公開し、BrowserSortPolicyで保持リストを並べ替える
- `BrowserSortPolicy`: QtやMetadataStoreに依存せず、自然順、更新日時、種類、サイズ、昇降順、フォルダ優先を一元管理
- `BrowserThumbnailScheduler`: viewport、gridSize、スクロール位置から可視行、選択行、前後2画面の先読み行を計画
- `BrowserThumbnailProvider`: 最大2スレッドの専用`QThreadPool`で画像、画像フォルダ、ZIP/CBZ/RAR/CBR/7z/CB7のサムネイルを生成し、メモリLRUキャッシュを管理
- `ThumbnailDiskCache`: SQLiteインデックスとWebPまたはPNGファイルによるポータブルな永続サムネイルキャッシュ
- `SettingsDialog`: Viewerの開き方、見開き表示、Browserのサムネイルとディスクキャッシュ、マウス操作割り当て、外部書庫backend選択、WinRAR／7-Zipの自動検出／明示パスを編集
- `ViewerWindow`: 閲覧メニュー、ダイアログ、入力、ViewerWidgetへの描画、ページ移動、全画面などウィンドウ固有UIを管理し、キー・メニュー・マウス入力を共通コマンドへdispatch
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

並び替えキーは`name`、`modified_time`、`item_type`、`file_size`です。名前はnatsortによる大文字小文字を過度に区別しない自然順とし、同一判定時は絶対パスで安定化します。更新日時とサイズが同じ項目、および同じ種類の項目は名前の自然順を二次順序にします。種類はフォルダ、ZIP/CBZ共通の書庫カテゴリ、画像の順です。未対応形式は一覧へ追加しません。

更新日時は列挙時に取得した元ファイルまたは元フォルダ自身の`st_mtime_ns`だけを使います。サイズはファイル自身の`st_size`で、フォルダを再帰走査しません。stat失敗時は`None`として安全な既定値で比較します。MetadataStoreの`metadata_updated_at`、サムネイル生成日時、キャッシュ時刻は並び替えへ混ぜません。更新操作は再列挙するため最新のファイルシステム情報を取得しますが、並び替えだけで`os.stat()`を繰り返しません。

`browser_folders_first`が有効なら昇順・降順やキーにかかわらずフォルダを先頭グループへ固定し、各グループ内部へ現在の並び替え条件を適用します。ZIPとCBZはフォルダ扱いせず、同一の書庫カテゴリです。無効時はすべての項目を選択キーだけで並べます。

表示密度はコンパクト、標準、ゆったりの3段階で、`QListView`の`gridSize`、`spacing`、折り返しだけを変更します。サムネイル画像サイズとは独立しているため、密度変更ではサムネイル世代、メモリキャッシュ、永続キャッシュキーを変更しません。`thumbnail_size`変更時だけ新しい世代を開始し、古い非同期結果を破棄してサイズ別キャッシュを利用します。

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

ThumbnailProviderはQThreadPoolの優先度を使い、未開始の低優先度要求を可視要求が追い越せるようにします。同一パス・サイズ・generationのpendingは重複させず、queued要求は優先度を引き上げられます。高速スクロール中は可視・選択項目だけを要求し、未開始の旧PREFETCHを`tryTake()`可能な範囲で除外します。最後のスクロールから180ms後に可視範囲を再計算して前後2画面の先読みを再開します。通常画像の実行中デコードは強制停止せず、外部7-Zip処理にはcancel tokenを渡します。完了結果はパスとthumbnail generationで安全にキャッシュ・照合します。

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

BrowserWindowはbackend利用不可でも外部書庫項目を一覧へ残し、標準の書庫アイコンへフォールバックします。サムネイル失敗はモーダル通知せず、Viewerで明示的に開いた失敗は対象Viewerのステータスへ短く表示します。RAR／7z系ファイルはSprint 10のファイル操作では通常の不透明なファイルであり、内部項目のrename、delete、追加、再圧縮は提供しません。元書庫へ書き込まず、書庫全体や一時ディレクトリへ展開せず、stdoutから単一ページだけを取得します。PDFはSprint 12で外部書庫backendとは別のImageSourceとして接続します。

### メタデータと元ファイルの分離

ApplicationControllerは`<config.jsonの配置ディレクトリ>/data/metadata.sqlite3`に接続するMetadataStoreを1つだけ所有し、BrowserWindowとすべてのViewerWindowへ共有注入します。各ウィンドウは独自接続を生成しません。DBは`PRAGMA user_version`でスキーマバージョンを保持し、`library_items`、`reading_history`、`browser_bookmarks`、`tags`、`item_tags`を管理します。

NivisViewerのメタデータは元画像、画像フォルダ、ZIP/CBZ/RAR/CBR/7z/CB7から完全に分離します。レート、タグ、ブックマーク、履歴の変更先はSQLiteだけであり、元ファイル名、内容、画像メタデータ、書庫、ADS、サイドカーファイル、元ファイルと元フォルダのmtimeを変更しません。`library_items.source_mtime_ns`は元項目を確認した時点の値、`metadata_updated_at`はNivisViewer内のレートやタグの更新時刻であり、別の概念です。BrowserWindowの更新日時表示とソートは従来どおり元項目の`st_mtime_ns`だけを使います。

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

BrowserWindowは`last_browser_path`、`browser_sidebar_visible`、`browser_sidebar_width`、`browser_window_geometry`、並び替えキー・順序、フォルダ優先、表示密度を保存します。サムネイルサイズは共有の`thumbnail_size`を使用し、96～384ピクセルへ正規化します。ページ間隔は0～100、ディスクキャッシュ容量は128～4096MBへ正規化します。設定とキャッシュをユーザーの画像フォルダへ書き込みません。

### ウィンドウの寿命

- アプリ起動時はBrowserWindowを表示する
- BrowserWindowがある間は、すべてのViewerWindowを閉じてもアプリを継続する
- BrowserWindowを閉じてもViewerWindowが残っていればアプリを継続する
- BrowserWindowと全ViewerWindowの両方がなくなったときだけ、ApplicationControllerが終了を一度要求する

## 次の構成

次段階ではPageSource抽象化を進め、Sprint 12でPDFを7-Zipとは別のSourceとして接続します。基本操作と対応形式の安定後にMetadataStoreのレート／タグAPIへ編集UI、検索、絞り込み、サムネイル上の表示を接続します。ZipPlaの`{zpi$...}`は明示的な読み取り互換から始め、元ファイルへ自動的に書き戻さない境界を維持します。

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
