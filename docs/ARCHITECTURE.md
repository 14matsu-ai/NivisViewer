# Architecture

## 現在の実装

Sprint 3では本を探す`BrowserWindow`を追加しました。BrowserWindowとViewerWindowは同じ`QApplication`・同じプロセス内で動く別ウィンドウで、ApplicationControllerが寿命と選択状態を連動させます。

```text
ApplicationController
├─ BrowserWindow
│  ├─ FolderTree (QFileSystemModel / QTreeView)
│  ├─ BrowserItemModel
│  └─ BrowserThumbnailProvider
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

### 設定の所有と保存

共有設定はApplicationControllerが所有するConfigManagerへ変更時に反映します。ViewerWindowを閉じる際にローカルな設定スナップショットを一括保存しないため、古い状態のウィンドウを後から閉じても共有設定は巻き戻りません。

共有設定には表示モード、綴じ方向、フィットモード、余白、表紙・横長画像の扱い、背景色、`open_viewer_behavior`、書庫移動ループ、前面表示設定などが含まれます。

ジオメトリ、ウィンドウ状態、全画面、回転角度はウィンドウ固有です。複数ウィンドウの完全な復元はまだ行わず、最後にアクティブだったViewerWindowの値を次回作成時の標準状態として保存します。現在の本、ページ、ズーム、パン、スライドショーの実行状態は各ViewerWindow内で独立し、ウィンドウ群としての復元対象にはしていません。

BrowserWindowは`last_browser_path`、`browser_sidebar_visible`、`browser_sidebar_width`、`browser_window_geometry`を保存します。サムネイルサイズは共有の`thumbnail_size`を使用し、80～500ピクセルへ正規化します。設定とメモリキャッシュをユーザーの画像フォルダへ書き込みません。

### ウィンドウの寿命

- アプリ起動時はBrowserWindowを表示する
- BrowserWindowがある間は、すべてのViewerWindowを閉じてもアプリを継続する
- BrowserWindowを閉じてもViewerWindowが残っていればアプリを継続する
- BrowserWindowと全ViewerWindowの両方がなくなったときだけ、ApplicationControllerが終了を一度要求する

## 次の構成

Sprint 4以降はBrowserWindowのサイドバーへブックマークと履歴のタブを追加し、共有設定を開いている各ウィンドウへ通知する設定サービスを導入します。BrowserWindowの明示的な選択、Controllerからの追従選択、履歴からの選択を区別した現在の接続点を維持します。

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
