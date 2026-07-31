# Third-Party Notices

NivisViewerは現在、次のソフトウェアを直接依存として使用しています。

- PySide6 / Qt：GUI
- Pillow：画像デコードと画像処理
- natsort：ファイル名の自然順ソート
- pypdfium2 / PDFium：PDFページの読み取り専用レンダリング

Viewer性能設計の比較と構造移植には、AGPL-3.0-or-laterのZipPlaFork
（https://github.com/himamon/ZipPlaFork、固定revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`）を使用しています。
固定snapshotで確認したupstreamの表示は、`license/About.txt`の
`Copyright ©  2016 Rio's Toolbox`と、
`source/ZipPla/Properties/AssemblyInfo.cs`の
`Copyright © 2016-2017 Rio's Toolbox`です。

NivisViewerは、Viewerの単一execution lane、current中心のqueue再構築、
1 display unitがdecodeからdisplay-ready terminalへ到達してから次unitを
投入するpaced dispatch、完成frameのatomic publish、source/display cacheの
連動したmemory policyをZipPlaFork由来の構造として適用しています。
NivisViewerではliteralな1 worker jobへ統合せず、decode taskからQt signalを
経てrender taskへ渡す境界を残したまま、同じ1-worker Viewer laneで順序を
制御します。ZipPlaForkは移動方向を状態として追跡せず、新currentを基準に
数値上のnext、previousの順で再計算します。NivisViewerの方向追跡、
request/generation検証、16 ms入力coalescing、decoder-sized JPEGは独自拡張です。

今回、C#のソース表現、pixel loop、GDI操作、翻訳コード、binary、upstream
source fileは取り込まず、ZipPlaForkをruntime／build依存にもしていません。
一方、repository規則に従い、上記アルゴリズム／処理構造は
AGPL-3.0-or-later由来として扱います。固定revision、元file／class／method、
処理内容、NivisViewer側の対応箇所、移植境界、cache accountingの相違は
`docs/ZIPPLAFORK_COMPARISON.md`に記録しています。upstreamの完全な
AGPL本文は`licenses/ZipPlaFork/AGPL.txt`、正式通知は
`licenses/ZipPlaFork/About.txt`として、固定revisionの内容を変更せず保持して
います。

RAR／7z／CBR／CB7閲覧では、利用者環境のWindows関連付け、標準インストール先、PATH、または設定画面で指定されたWinRAR／7-Zipの認識済みCLIを任意で呼び出します。

- WinRARはNivisViewerとは別の第三者ソフトウェアです。現在の配布物へWinRAR、UnRAR、RARのバイナリを同梱しません。
- 7-ZipもNivisViewerとは別の第三者ソフトウェアです。現在の配布物へ7-Zipバイナリを同梱しません。
- NivisViewerは両アプリを自動取得・自動インストールせず、未知の関連付けアプリを起動しません。

動画サムネイルではWindows Shellを優先し、利用者が別途用意したFFmpegを任意で呼び出せます。NivisViewerはFFmpegを自動取得・自動インストールせず、現在の配布物へFFmpeg binaryを同梱しません。将来同梱する場合は、採用するbuildと含まれるcodecごとの公式ライセンスおよび再配布条件を改めて監査し、必要な文書を`licenses/`へ追加します。

各依存ソフトウェアのライセンス名や配布条件は、採用するバージョン、配布形態、同梱物によって確認すべき内容が変わるため、この文書では推測による断定を行いません。

正式公開前に、実際に採用する固定バージョン（pypdfium2と同梱PDFiumを含む）の公式配布物および公式ライセンス文書を監査します。pypdfium2については採用wheelへ同梱された正式なライセンスファイルを基準にします。必要な著作権表示、ライセンス本文、NOTICEなどを収集し、`licenses/`ディレクトリへ同梱した上で、この文書に採用バージョンと確認結果を記録します。

frozen配布では、実際に含まれるPySide6／Qt DLLとplugin、shiboken6、Pillowのnative component、pypdfium2／PDFium native binary、PyInstaller bootloaderを監査対象にします。`scripts/collect_licenses.py`はインストール済みdistributionの正式なLICENSE／COPYING／NOTICE等だけを収集し、バージョンmanifestを生成します。自動収集結果は正式公開前に必ず人手で監査します。PyInstallerはビルドツールである一方、生成物にはbootloaderが含まれるためruntime componentとは区別して記録します。

将来WinRAR、UnRAR、RAR、7-Zipのいずれかを同梱する配布形態へ変更する場合は、その時点の公式ライセンス、構成要素ごとの条件、著作権表示および再配布条件を改めて監査し、必要な文書を`licenses/`へ追加します。
