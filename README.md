# NivisViewer

NivisViewerは、Windows向けの漫画・画像ビューアです。ZipPlaの操作感や挙動を参考にしつつ、ソースコードは流用せず独自に実装しています。

現在は開発初期段階です。フォルダツリーとサムネイル一覧を持つBrowserWindow、本を表示する独立したViewerWindow、基本設定画面、見開き密着表示、ポータブルなサムネイルキャッシュ、フォルダ・単体画像・ZIP/CBZ・RAR/7z系書庫の読み込み、自然順ソート、非同期画像読み込みなどを実装しています。ViewerWindowではマウスの戻る／進むボタンによる前後の本への移動と、設定可能な右クリックドラッグジェスチャーも利用できます。

## 対応入力形式

- フォルダ
- 単体画像（親フォルダ内の対応画像を本として開きます）
- ZIP / CBZ
- RAR / CBR（利用者環境のWinRARまたは7-Zipを使用）
- 7z / CB7（利用者環境の対応済みWinRAR CLI構成または7-Zipを使用）
- 画像: JPEG、PNG、WebP、BMP、GIF、TIFF、ICO

RAR／7z／CBR／CB7は、Windowsのファイル関連付け、標準インストール先、PATH、または設定画面の「書庫」タブで指定したWinRAR／7-Zipから、認識済みの外部CLIだけを選んで閲覧します。WinRAR利用者は、対応する公式コンソールCLIが同梱されていればRAR／CBR閲覧のためだけに7-Zipを追加導入する必要はありません。未知の関連付けアプリを起動したり、ShellExecuteへ渡したりしません。

NivisViewerはWinRARや7-Zipを自動ダウンロード・自動インストールせず、現在の配布物にも両アプリのバイナリを同梱しません。現在確認済みのWinRARコンソールCLIはRAR形式専用です。この構成で7z／CB7をstdout抽出できない場合は、安全に`unsupported_archive`として扱い、利用可能な7-Zipがある場合だけ1回フォールバックします。

パスワード付き書庫は検出のみで、パスワード入力には未対応です。分割RARは通常の`.rar`と先頭の`.part1.rar`を候補にしますが、後続`.part2.rar`以降や`.r00`等は本の一覧から除外します。7z分割`.7z.001`は正式対応外です。solid書庫は必要な1ページだけを取得するため、後方ページへの移動が遅い場合があります。PDFはSprint 12で対応予定です。

## 動作環境

- Windows
- Python 3.11以上

## セットアップと起動

PowerShellでリポジトリのルートへ移動し、依存関係をインストールします。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

テストを実行する場合は、開発用依存もインストールします。

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## License

NivisViewer本体は[MIT License](LICENSE)で公開します。直接依存するソフトウェアについては[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。
