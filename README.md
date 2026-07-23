# NivisViewer

NivisViewerは、Windows向けの漫画・画像ビューアです。ZipPlaの操作感や挙動を参考にしつつ、ソースコードは流用せず独自に実装しています。

現在は開発初期段階です。既存のプロトタイプでは、画像の単ページ／見開き表示、フォルダ・単体画像・ZIP/CBZの読み込み、自然順ソート、非同期画像読み込みなどを実装しています。

## 対応入力形式

- フォルダ
- 単体画像（親フォルダ内の対応画像を本として開きます）
- ZIP / CBZ
- 画像: JPEG、PNG、WebP、BMP、GIF、TIFF、ICO

PDF、RAR、7z、CBR、CB7は将来対応予定であり、現時点では未対応です。

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
