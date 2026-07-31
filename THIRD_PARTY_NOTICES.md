# Third-Party Notices

NivisViewerは現在、次のソフトウェアを直接依存として使用しています。

- PySide6 / Qt：GUI
- Pillow：画像デコードと画像処理
- natsort：ファイル名の自然順ソート
- pypdfium2 / PDFium：PDFページの読み取り専用レンダリング

性能設計の比較監査では、AGPL-3.0-or-laterのZipPlaFork
（https://github.com/himamon/ZipPlaFork、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`、
Copyright © 2016-2017 Rio's Toolbox）を参照しました。表示サイズ成果物、
単一優先列、要求差し替え、メモリ上限の処理構造をNivisViewer向けに
適用しています。今回、C#のソース表現、翻訳コード、binary、source
fileは取り込まず、runtime／build依存にもしていません。固定revision、
元file／method、処理、ライセンス由来、NivisViewer側の対応箇所と
コード利用境界は`docs/ZIPPLAFORK_COMPARISON.md`に記録します。

RAR／7z／CBR／CB7閲覧では、利用者環境のWindows関連付け、標準インストール先、PATH、または設定画面で指定されたWinRAR／7-Zipの認識済みCLIを任意で呼び出します。

- WinRARはNivisViewerとは別の第三者ソフトウェアです。現在の配布物へWinRAR、UnRAR、RARのバイナリを同梱しません。
- 7-ZipもNivisViewerとは別の第三者ソフトウェアです。現在の配布物へ7-Zipバイナリを同梱しません。
- NivisViewerは両アプリを自動取得・自動インストールせず、未知の関連付けアプリを起動しません。

動画サムネイルではWindows Shellを優先し、利用者が別途用意したFFmpegを任意で呼び出せます。NivisViewerはFFmpegを自動取得・自動インストールせず、現在の配布物へFFmpeg binaryを同梱しません。将来同梱する場合は、採用するbuildと含まれるcodecごとの公式ライセンスおよび再配布条件を改めて監査し、必要な文書を`licenses/`へ追加します。

各依存ソフトウェアのライセンス名や配布条件は、採用するバージョン、配布形態、同梱物によって確認すべき内容が変わるため、この文書では推測による断定を行いません。

正式公開前に、実際に採用する固定バージョン（pypdfium2と同梱PDFiumを含む）の公式配布物および公式ライセンス文書を監査します。pypdfium2については採用wheelへ同梱された正式なライセンスファイルを基準にします。必要な著作権表示、ライセンス本文、NOTICEなどを収集し、`licenses/`ディレクトリへ同梱した上で、この文書に採用バージョンと確認結果を記録します。

frozen配布では、実際に含まれるPySide6／Qt DLLとplugin、shiboken6、Pillowのnative component、pypdfium2／PDFium native binary、PyInstaller bootloaderを監査対象にします。`scripts/collect_licenses.py`はインストール済みdistributionの正式なLICENSE／COPYING／NOTICE等だけを収集し、バージョンmanifestを生成します。自動収集結果は正式公開前に必ず人手で監査します。PyInstallerはビルドツールである一方、生成物にはbootloaderが含まれるためruntime componentとは区別して記録します。

将来WinRAR、UnRAR、RAR、7-Zipのいずれかを同梱する配布形態へ変更する場合は、その時点の公式ライセンス、構成要素ごとの条件、著作権表示および再配布条件を改めて監査し、必要な文書を`licenses/`へ追加します。
