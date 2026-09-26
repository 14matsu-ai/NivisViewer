# NivisViewer

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

## 目次

- [概要](#overview)
- [Windows x64ポータブル版](#portable)
- [対応入力形式](#formats)
- [動作環境](#requirements)
- [セットアップと起動](#setup)
- [Explorerからのドロップ確認](#drop)
- [ライセンス](#license)

<a id="overview"></a>
## 概要

NivisViewerは、Windows向けの漫画・画像ビューアです。ZipPlaの操作感や挙動を参考にしつつ、ソースコードは流用せず独自に実装しています。

お気に入りフォルダと同期可能なフォルダツリー、固定セルのサムネイル一覧を持つBrowserWindow、本を表示する独立したViewerWindow、基本設定画面、見開き密着表示、ポータブルなサムネイルキャッシュ、フォルダ・単体画像・ZIP/CBZ・RAR/7z系書庫・PDFの読み込み、自然順ソート、非同期画像読み込みなどを実装しています。ViewerWindowではマウスの戻る／進むボタンによる前後の本への移動、設定可能な右クリックドラッグジェスチャー、全画面での画面端UI表示も利用できます。

Nivis はラテン語で「雪」を意味する nix, nivis に由来します。\
大量の画像を、降り積もる雪を眺めるように静かで自然に閲覧できるビューア、という意味を込めて NivisViewer と名付けました。

<a id="portable"></a>
## Windows x64ポータブル版

配布ZIPを任意の書き込み可能なフォルダへ展開し、`NivisViewer.exe`を起動します。Pythonの別途インストールは不要です。標準配布はPyInstaller one-folder形式で、`portable.flag`がexeの隣にあると`config.json`と`data/`（履歴DB、サムネイルキャッシュ、ログ）も同じポータブルフォルダへ保存します。カレントディレクトリや閲覧中の画像フォルダは保存先に使いません。

読み取り専用の場所では閲覧を継続できますが、設定、履歴、サムネイルキャッシュ、ログは保存されません。書き込み可能な場所へフォルダごと移動してください。アンインストールはNivisViewerフォルダの削除です。Windows関連付けを登録した場合は、削除前に設定画面の「Windows連携」から解除してください。

二重起動やExplorerからの連続openは、同じユーザー・同じポータブルフォルダの既存プロセスへ安全に転送します。別の場所へコピーしたポータブル版は別インスタンスです。フォルダ移動後はWindows関連付けのexeパスが変わるため再登録が必要です。

Windows連携は設定画面で利用者が明示的に登録した場合だけ有効になります。「プログラムから開く」と既定アプリ候補を登録しますが、Windowsの既定アプリやUserChoiceを強制変更しません。WinRAR／7-Zipは同梱しません。pypdfium2／PDFiumはポータブル版へ同梱します。

コード署名はまだ行っていないため、ダウンロードした配布物でSmartScreen警告が表示される可能性があります。不具合報告時はViewerの「ヘルプ」から診断情報をコピーできます。ログは`data\logs\NivisViewer.log`に保存され、外部へ自動送信されません。

<a id="formats"></a>
## 対応入力形式

- フォルダ
- 単体画像（親フォルダ内の対応画像を本として開きます）
- ZIP / CBZ
- RAR / CBR（利用者環境のWinRARまたは7-Zipを使用）
- 7z / CB7（利用者環境の対応済みWinRAR CLI構成または7-Zipを使用）
- PDF（pypdfium2 v5／PDFiumを使用）
- 画像: JPEG、PNG、WebP、AVIF、JPEG XL（JXL）、BMP、GIF、TIFF、ICO、PSD、PSB

RAR／7z／CBR／CB7は、Windowsのファイル関連付け、標準インストール先、PATH、または設定画面の「書庫」タブで指定したWinRAR／7-Zipから、認識済みの外部CLIだけを選んで閲覧します。WinRAR利用者は、対応する公式コンソールCLIが同梱されていればRAR／CBR閲覧のためだけに7-Zipを追加導入する必要はありません。未知の関連付けアプリを起動したり、ShellExecuteへ渡したりしません。

NivisViewerはWinRARや7-Zipを自動ダウンロード・自動インストールせず、現在の配布物にも両アプリのバイナリを同梱しません。現在確認済みのWinRARコンソールCLIはRAR形式専用です。この構成で7z／CB7をstdout抽出できない場合は、安全に`unsupported_archive`として扱い、利用可能な7-Zipがある場合だけ1回フォールバックします。

パスワード付き書庫とパスワード付きPDFは検出のみで、パスワード入力には未対応です。分割RARは通常の`.rar`と先頭の`.part1.rar`を候補にしますが、後続`.part2.rar`以降や`.r00`等は本の一覧から除外します。7z分割`.7z.001`は正式対応外です。solid書庫は必要な1ページだけを取得するため、後方ページへの移動が遅い場合があります。

PDFは読み取り専用で扱い、通常ページと注釈を表示します。編集、テキスト検索・選択、リンク操作、目次UI、フォーム入力、JavaScript、XFAには対応していません。PDFの100%表示は96 logical DPI相当です。高DPI画面ではdevice pixel ratioをレンダー解像度へ反映し、リサイズやズームの停止から約180ms後に表示先サイズへ再レンダーします。

<a id="requirements"></a>
## 動作環境

- Windows
- Python 3.11以上

<a id="setup"></a>
## セットアップと起動

PowerShellでリポジトリのルートへ移動し、依存関係をインストールします。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

コマンドラインではファイル、フォルダ、複数パスを指定できます。2件目以降は別Viewerで開きます。

```powershell
NivisViewer.exe "D:\漫画\book.cbz"
NivisViewer.exe --reuse "book1.cbz" "book2.pdf"
NivisViewer.exe --new-window "C:\画像集"
NivisViewer.exe --browser-only "D:\Books"
```

`--new-window`と`--reuse`は同時指定できません。`--no-restore`はその起動だけ前回位置の復元を抑止します。

テストを実行する場合は、開発用依存もインストールします。

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

<a id="drop"></a>
## Windows ExplorerからBrowser中央へのドロップ確認

NivisViewerを管理者権限ではなく通常ユーザーとして起動し、ExplorerからBrowser中央の項目上と空白上へファイルをドロップします。cursorが受理表示になり、ファイルの親フォルダを表示して対象を選択・中央表示し、Viewerを開かないことを確認します。フォルダをドロップした場合はそのフォルダを表示します。複数ファイルでは同じ親の項目を選択し、別の親にある項目は件数だけを通知します。HTTP／HTTPS URLは受理しません。

Browser中央へのExplorer dropはコピー／移動ではなく「場所を表示」です。NivisViewer内の項目をフォルダ項目、フォルダツリー、お気に入りへdropした場合だけ、既存のコピー／移動操作になります。

<a id="license"></a>
## ライセンス

NivisViewerの現在のプロジェクトライセンスは **GNU AGPL version 3 or later
(`AGPL-3.0-or-later`)** です。[LICENSE](LICENSE)に正式本文、
[PROJECT_LICENSE.md](PROJECT_LICENSE.md)に適用通知と著作権表示を記載しています。
ZipPlaForkからの構造移植の由来は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)と
[比較記録](docs/ZIPPLAFORK_COMPARISON.md)を参照してください。
依存ライブラリのMIT・BSD・Apache・LGPL・GPL等のライセンスはそのまま保持します。
