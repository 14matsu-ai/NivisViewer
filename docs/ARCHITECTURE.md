# Architecture

## 現在の実装

現在は単一の`MainWindow`がアプリケーションの中心であり、ウィンドウ構築、メニューとショートカット、ファイル選択、本の切替、表示設定、履歴、ブックマーク、ページ一覧、設定保存をまとめて調整しています。

- `main.py`: `QApplication`と`MainWindow`を生成するエントリーポイント
- `ConfigManager`: リポジトリ直下の`config.json`を読み書きするポータブル設定管理
- `ImageSource`: フォルダ、単体画像の親フォルダ、ZIP/CBZを共通化する画像供給層
- `PageModel`: 論理ページ順と単ページ／見開きの表示単位を管理する非GUIモデル
- `ImageCache`: `QThreadPool`で画像を非同期デコードし、LRU形式で保持するキャッシュ
- `ViewerWidget`: 渡された画像の描画、拡大縮小、パン、クリックやホイール入力を担当するウィジェット
- `MainWindow`: 上記コンポーネントを接続し、現在の本と表示状態を管理する

非同期画像読み込みでは`ImageCache.generation`で読み込み世代を管理し、ソースや画像調整の変更前に開始された古い結果を破棄します。`MainWindow`側にも表示要求IDがあり、古い描画要求が現在の表示へ混入しないようにしています。

## 将来構成

```text
ApplicationController
├─ BrowserWindow
│  ├─ フォルダツリー
│  ├─ ブックマーク
│  └─ サムネイル一覧
└─ ViewerWindow
   ├─ ViewerWidget
   ├─ PageModel
   └─ ImageCache
```

`ApplicationController`はアプリ全体の寿命、共有設定、選択中の本、ウィンドウ間イベントを管理します。`BrowserWindow`は本を探して選ぶ責務、`ViewerWindow`は選択された本を読む責務を持ちます。画像列挙とデコードはUIスレッドから分離し、`PageModel`はGUIに依存しない状態を保ちます。

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
