# Architecture

## 現在の実装

Sprint 7では、BrowserWindowへセッション内フォルダ履歴、ナビゲーションバー、パス入力、選択・スクロール復元を追加しました。BrowserWindowとViewerWindowは同じ`QApplication`・同じプロセス内で動く別ウィンドウで、ApplicationControllerが寿命、選択状態、共有MetadataStoreを管理します。

```text
ApplicationController
├─ MetadataStore
├─ BrowserWindow
│  ├─ BrowserNavigationHistory
│  ├─ FolderTree (QFileSystemModel / QTreeView)
│  ├─ NavigationToolbar
│  ├─ AddressBar
│  ├─ BookmarkModel
│  ├─ HistoryModel
│  ├─ BrowserItemModel
│  ├─ BrowserThumbnailProvider
│  │  └─ ThumbnailDiskCache
│  └─ SettingsDialog
└─ ViewerWindow [0..n]
   ├─ BookSession
   │  ├─ ImageSource
   │  ├─ PageModel
   │  └─ ImageCache
   ├─ Viewer command dispatcher
   └─ ViewerWidget
      └─ MouseGestureRecognizer
```

- `main.py`: `QApplication`を生成し、起動引数を`ApplicationController`へ渡すエントリーポイント
- `ApplicationController`: 共通の`ConfigManager`と単一`MetadataStore`、単一BrowserWindow、ViewerWindow群、最後にアクティブだったViewerWindow、ウィンドウ選択、隣接書庫探索、前面表示、ウィンドウ間同期、終了判定を管理
- `MetadataStore`: SQLiteスキーマ、パス正規化、閲覧履歴、読書位置、Browserブックマーク、レート、タグを管理
- `BrowserWindow`: フォルダ履歴、ナビゲーションバー、パス入力、フォルダツリー、ブックマーク／履歴タブ、項目選択、サムネイル一覧、ステータス表示を管理。本のページ移動やBookSession、SQLは持たない
- `BrowserNavigationHistory`: Qtに依存せず、訪問フォルダ、選択項目、縦横スクロール位置、戻る／進むの分岐をセッション内で管理
- `BookmarkModel` / `HistoryModel`: MetadataStoreの公開APIと変更通知をQt Model/Viewへ公開し、BrowserWindowからSQLを分離
- `BrowserItemDiscovery`: サブフォルダ、ZIP/CBZ、対応画像を列挙し、種類ごとの一貫した順序と自然順で返す。画像のデコードや書庫の展開は行わない
- `BrowserItemModel`: BrowserItemの表示名、絶対パス、種類、更新日時と表示アイコンをQt Model/Viewへ公開
- `BrowserThumbnailProvider`: 最大2スレッドの専用`QThreadPool`で画像、画像フォルダ、ZIP/CBZのサムネイルを生成し、メモリLRUキャッシュを管理
- `ThumbnailDiskCache`: SQLiteインデックスとWebPまたはPNGファイルによるポータブルな永続サムネイルキャッシュ
- `SettingsDialog`: Viewerの開き方、見開き表示、Browserのサムネイルとディスクキャッシュ、マウス操作割り当てを編集
- `ViewerWindow`: 閲覧メニュー、ダイアログ、入力、ViewerWidgetへの描画、ページ移動、全画面などウィンドウ固有UIを管理し、キー・メニュー・マウス入力を共通コマンドへdispatch
- `BookSession`: 現在のパス、ImageSource、PageModel、ImageCache、読み込み世代、ソース切替と終了を管理
- `ConfigManager`: 実行ファイル基準の`config.json`を読み書きし、メタデータDBやキャッシュの配置基準も提供するポータブル設定管理
- `ImageSource`: フォルダ、単体画像の親フォルダ、ZIP/CBZを共通化する画像供給層
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

ナビゲーションバーは戻る、進む、上へ、更新、編集可能なパス欄を持ちます。相対パスは現在のフォルダを基準に絶対化します。対応画像またはZIP/CBZを入力した場合は親フォルダを表示して対象項目を選択し、非対応・不存在・アクセス不能では現在位置を維持します。更新は同じ履歴項目を再列挙し、サムネイル世代だけを進めるため履歴を増やしません。ディスクキャッシュのキーに元項目のサイズとmtimeが含まれるため、未変更項目は既存キャッシュを利用できます。

FolderTreeのユーザー選択は短いタイマー後に通常訪問として記録します。`navigate_to()`からのツリー同期中は専用フラグで`currentChanged`を無視し、履歴の二重追加と無限再帰を防ぎます。ツリーのユーザー移動待ちと、QFileSystemModelの読み込み待ちを別の状態として管理します。

BrowserWindowでは`BackButton / XButton1`をフォルダ履歴の戻る、`ForwardButton / XButton2`を進むとして押下時だけ処理します。サムネイル一覧、ツリー、ブックマーク、履歴などの主要子ウィジェットへevent filterを設定し、解放イベントでは再実行しません。パス欄のBackspaceは通常の文字編集として扱います。ViewerWindowでは従来どおりXButtonを前／次の本へ割り当て、Browserの履歴処理と共有しません。

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

### メタデータと元ファイルの分離

ApplicationControllerは`<config.jsonの配置ディレクトリ>/data/metadata.sqlite3`に接続するMetadataStoreを1つだけ所有し、BrowserWindowとすべてのViewerWindowへ共有注入します。各ウィンドウは独自接続を生成しません。DBは`PRAGMA user_version`でスキーマバージョンを保持し、`library_items`、`reading_history`、`browser_bookmarks`、`tags`、`item_tags`を管理します。

NivisViewerのメタデータは元画像、画像フォルダ、ZIP/CBZから完全に分離します。レート、タグ、ブックマーク、履歴の変更先はSQLiteだけであり、元ファイル名、内容、画像メタデータ、書庫、ADS、サイドカーファイル、元ファイルと元フォルダのmtimeを変更しません。`library_items.source_mtime_ns`は元項目を確認した時点の値、`metadata_updated_at`はNivisViewer内のレートやタグの更新時刻であり、別の概念です。BrowserWindowの更新日時表示とソートは従来どおり元項目の`st_mtime_ns`だけを使います。

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

共有設定には表示モード、綴じ方向、フィットモード、余白、表紙・横長画像の扱い、背景色、`open_viewer_behavior`、書庫移動ループ、前面表示設定、マウスジェスチャーと追加ボタンの割り当てなどが含まれます。

ジオメトリ、ウィンドウ状態、全画面、回転角度はウィンドウ固有です。複数ウィンドウの完全な復元はまだ行わず、最後にアクティブだったViewerWindowの値を次回作成時の標準状態として保存します。現在の本、ズーム、パン、スライドショーの実行状態は各ViewerWindow内で独立し、ウィンドウ群としての復元対象にはしていません。読書位置だけは本ごとの履歴としてMetadataStoreへ保存します。

BrowserWindowは`last_browser_path`、`browser_sidebar_visible`、`browser_sidebar_width`、`browser_window_geometry`を保存します。サムネイルサイズは共有の`thumbnail_size`を使用し、80～500ピクセルへ正規化します。ページ間隔は0～100、ディスクキャッシュ容量は128～4096MBへ正規化します。設定とキャッシュをユーザーの画像フォルダへ書き込みません。

### ウィンドウの寿命

- アプリ起動時はBrowserWindowを表示する
- BrowserWindowがある間は、すべてのViewerWindowを閉じてもアプリを継続する
- BrowserWindowを閉じてもViewerWindowが残っていればアプリを継続する
- BrowserWindowと全ViewerWindowの両方がなくなったときだけ、ApplicationControllerが終了を一度要求する

## 次の構成

次段階ではBrowserWindowの表示密度、並び替え、大量項目の非同期・増分列挙、安全なファイル操作を優先します。基本操作の安定後にMetadataStoreのレート／タグAPIへ編集UI、検索、絞り込み、サムネイル上の表示を接続します。ZipPlaの`{zpi$...}`は明示的な読み取り互換から始め、元ファイルへ自動的に書き戻さない境界を維持します。

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
- 将来PDF、RAR、7z、CBR、CB7へ対応する。
- 入力形式ごとの差異を吸収する`PageSource`抽象化への移行を予定する。
