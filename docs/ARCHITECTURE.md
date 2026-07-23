# Architecture

## 現在の実装

Sprint 3では本を探す`BrowserWindow`を追加しました。BrowserWindowとViewerWindowは同じ`QApplication`・同じプロセス内で動く別ウィンドウで、ApplicationControllerが寿命と選択状態を連動させます。

```text
ApplicationController
├─ BrowserWindow
│  ├─ FolderTree (QFileSystemModel / QTreeView)
│  ├─ BrowserItemModel
│  ├─ BrowserThumbnailProvider
│  │  └─ ThumbnailDiskCache
│  └─ SettingsDialog
└─ ViewerWindow [0..n]
   ├─ BookSession
   │  ├─ ImageSource
   │  ├─ PageModel
   │  └─ ImageCache
   └─ ViewerWidget
```

- `main.py`: `QApplication`を生成し、起動引数を`ApplicationController`へ渡すエントリーポイント
- `ApplicationController`: 共通の`ConfigManager`、単一BrowserWindow、ViewerWindow群、最後にアクティブだったViewerWindow、ウィンドウ選択、隣接書庫探索、前面表示、ウィンドウ間同期、終了判定を管理
- `BrowserWindow`: フォルダツリー、現在のフォルダ、項目選択、サイドバー、サムネイル一覧、ステータス表示を管理。本のページ移動やBookSessionは持たない
- `BrowserItemDiscovery`: サブフォルダ、ZIP/CBZ、対応画像を列挙し、種類ごとの一貫した順序と自然順で返す。画像のデコードや書庫の展開は行わない
- `BrowserItemModel`: BrowserItemの表示名、絶対パス、種類、更新日時と表示アイコンをQt Model/Viewへ公開
- `BrowserThumbnailProvider`: 最大2スレッドの専用`QThreadPool`で画像、画像フォルダ、ZIP/CBZのサムネイルを生成し、メモリLRUキャッシュを管理
- `ThumbnailDiskCache`: SQLiteインデックスとWebPまたはPNGファイルによるポータブルな永続サムネイルキャッシュ
- `SettingsDialog`: Viewerの開き方、見開き表示、Browserのサムネイルとディスクキャッシュ設定を編集
- `ViewerWindow`: 閲覧メニュー、ダイアログ、入力、ViewerWidgetへの描画、ページ移動、全画面などウィンドウ固有UIを管理
- `BookSession`: 現在のパス、ImageSource、PageModel、ImageCache、読み込み世代、ソース切替と終了を管理
- `ConfigManager`: リポジトリ直下の`config.json`を読み書きするポータブル設定管理
- `ImageSource`: フォルダ、単体画像の親フォルダ、ZIP/CBZを共通化する画像供給層
- `PageModel`: 論理ページ順と単ページ／見開きの表示単位を管理する非GUIモデル
- `ImageCache`: セッション専用`QThreadPool`で画像を非同期デコードし、LRU形式で保持するキャッシュ
- `ViewerWidget`: 渡された画像の描画、拡大縮小、パン、クリックやホイール入力を担当
- `PageThumbnailProvider`: QImageからQtアイコンへの変換を担当し、ViewerWindowのページ一覧とBrowserWindowの一覧で利用

BrowserWindowのフォルダツリーはQt標準の`QFileSystemModel`と`QTreeView`を使い、フォルダだけを表示します。モデルの空ルートからWindowsのドライブへアクセスでき、フォルダ選択は短いタイマーでまとめてから一覧を更新します。右側は`QListView`と`BrowserItemModel`によるModel/View構成です。

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

### ViewerWindowの選択

`ApplicationController.open_path()`は`open_viewer_behavior`に従います。

- `reuse_active`: アクティブなViewerWindowを再利用し、なければ作成
- `always_new`: 本を開くたびにViewerWindowを作成
- `reuse_or_create`: 最後にアクティブだったViewerWindow、または既存の最後のViewerWindowを再利用し、なければ作成
- 不正な値: `reuse_or_create`として扱う

ViewerWindow内の通常のファイル選択、最近使った項目、ドロップ操作は、設定値にかかわらず操作元のViewerWindowを再利用します。

BrowserWindowから画像を開く場合は画像自身のパス、ZIP/CBZは書庫パスをApplicationControllerへ渡します。画像パスは既存のImageSource規則により親フォルダを本として開き、指定画像から開始します。フォルダ項目の明示的な起動はそのフォルダへ移動し、ViewerWindowを開きません。

ViewerWindowで本が変わると`book_changed`シグナルがApplicationControllerへ届きます。変更したViewerWindowが最後にアクティブだったViewerWindowの場合だけ、BrowserWindowが対象の親フォルダを表示し、項目を選択してスクロールします。この選択同期はBrowserWindowの明示的な「開く」処理を呼ばないため、再オープンのループは発生しません。前後の書庫移動も同じ経路で追従しますが、BrowserWindowへフォーカスは移しません。

BrowserWindowから本を開いたときは、ApplicationControllerが`open_viewer_behavior`に従ってViewerWindowを選択または作成します。`bring_viewer_to_front_on_open`が有効な場合だけ`show()`、`raise_()`、`activateWindow()`を一度実行し、常時最前面のフラグは設定しません。

`open_viewer_behavior`、前面表示、書庫移動ループはSettingsDialogから変更できます。通常の「開く」はこの設定に従い、BrowserWindowの「新しいViewerWindowで開く」は設定に関係なく新規ウィンドウを作成します。

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

共有設定には表示モード、綴じ方向、フィットモード、余白、表紙・横長画像の扱い、背景色、`open_viewer_behavior`、書庫移動ループ、前面表示設定などが含まれます。

ジオメトリ、ウィンドウ状態、全画面、回転角度はウィンドウ固有です。複数ウィンドウの完全な復元はまだ行わず、最後にアクティブだったViewerWindowの値を次回作成時の標準状態として保存します。現在の本、ページ、ズーム、パン、スライドショーの実行状態は各ViewerWindow内で独立し、ウィンドウ群としての復元対象にはしていません。

BrowserWindowは`last_browser_path`、`browser_sidebar_visible`、`browser_sidebar_width`、`browser_window_geometry`を保存します。サムネイルサイズは共有の`thumbnail_size`を使用し、80～500ピクセルへ正規化します。ページ間隔は0～100、ディスクキャッシュ容量は128～4096MBへ正規化します。設定とキャッシュをユーザーの画像フォルダへ書き込みません。

### ウィンドウの寿命

- アプリ起動時はBrowserWindowを表示する
- BrowserWindowがある間は、すべてのViewerWindowを閉じてもアプリを継続する
- BrowserWindowを閉じてもViewerWindowが残っていればアプリを継続する
- BrowserWindowと全ViewerWindowの両方がなくなったときだけ、ApplicationControllerが終了を一度要求する

## 次の構成

Sprint 5以降はBrowserWindowのサイドバーへブックマークと履歴のタブを追加します。BrowserWindowの明示的な選択、Controllerからの追従選択、履歴からの選択を区別した現在の接続点を維持します。

`ApplicationController`は引き続きアプリ全体の寿命、共有設定、ウィンドウ群、ウィンドウ間イベントを管理します。`BrowserWindow`は本を探して選ぶ責務、`ViewerWindow`はBookSessionとViewerWidgetを接続して読む責務を持ちます。PageModelはGUIに依存しない状態を保ちます。

## 設計上の決定事項

- `BrowserWindow`と`ViewerWindow`は、同一アプリ・同一プロセス内の別ウィンドウとする。
- `BrowserWindow`と`ViewerWindow`は、選択中の本について連動する。
- `BrowserWindow`から本を開いた際は、`ViewerWindow`を一度だけ前面へ表示する。
- `ViewerWindow`を常時最前面にはしない。
- `ViewerWindow`を再利用するか新規作成するかは設定可能にする。
- マウスの戻る／進むボタンで前／次の書庫へ移動する。
- 最後の書庫から先頭へ戻るループは設定可能にする。
- 右クリックドラッグのマウスジェスチャーを将来実装する。
- サムネイルサイズは設定画面から変更する。
- 設定、履歴、ブックマーク、キャッシュはポータブル配置を基本とする。
- 将来PDF、RAR、7z、CBR、CB7へ対応する。
- 入力形式ごとの差異を吸収する`PageSource`抽象化への移行を予定する。
