# Architecture

## 現在の実装

Sprint 1ではアプリケーション全体と本単位のライフサイクルを、`MainWindow`から分離しました。画面構成は従来どおり単一の`MainWindow`ですが、所有関係は次のとおりです。

```text
ApplicationController
└─ MainWindow
   ├─ BookSession
   │  ├─ ImageSource
   │  ├─ PageModel
   │  └─ ImageCache
   └─ ViewerWidget
```

- `main.py`: `QApplication`を生成し、起動引数を`ApplicationController`へ渡すエントリーポイント
- `ApplicationController`: 共通の`ConfigManager`、MainWindow群、起動、前面表示、終了処理を管理
- `MainWindow`: メニュー、ダイアログ、入力、ViewerWidgetへの描画、全画面などウィンドウ固有UIを管理
- `BookSession`: 現在のパス、ImageSource、PageModel、ImageCache、読み込み世代、ソース切替と終了を管理
- `ConfigManager`: リポジトリ直下の`config.json`を読み書きするポータブル設定管理
- `ImageSource`: フォルダ、単体画像の親フォルダ、ZIP/CBZを共通化する画像供給層
- `PageModel`: 論理ページ順と単ページ／見開きの表示単位を管理する非GUIモデル
- `ImageCache`: セッション専用`QThreadPool`で画像を非同期デコードし、LRU形式で保持するキャッシュ
- `ViewerWidget`: 渡された画像の描画、拡大縮小、パン、クリックやホイール入力を担当

非同期画像読み込みでは`ImageCache.generation`で古い結果を破棄し、`MainWindow`でも表示要求IDを確認します。本を切り替えた時点でキャッシュ世代を更新し、旧ImageSourceを参照するタスクが残っている場合は、`BookSession`がソースを遅延破棄します。

## 将来構成

```text
ApplicationController
├─ BrowserWindow
└─ ViewerWindow
   ├─ BookSession
   └─ ViewerWidget
```

`ApplicationController`はアプリ全体の寿命、共有設定、ウィンドウ群、ウィンドウ間イベントを管理します。`BrowserWindow`は本を探して選ぶ責務、`ViewerWindow`はBookSessionとViewerWidgetを接続して読む責務を持ちます。画像列挙とデコードはUIスレッドから分離し、`PageModel`はGUIに依存しない状態を保ちます。

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
