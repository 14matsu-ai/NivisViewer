# Windowsポータブルビルド

現在のプロジェクトライセンスはAGPL-3.0-or-laterです。下記は開発用のビルド手順であり、
公開許可や再配布条件の充足を意味しません。[公開前監査](RELEASE_LICENSE_AUDIT.md)と
[チェックリスト](RELEASE_CHECKLIST.md)の未完了項目を先に確認してください。
`requirements-release.txt`の固定値と現在のインストール環境は一致していません。
公開候補は承認された別作業コピーと新しい出力先で作成し、選択した完全な依存lock、
wheel/sourceのhash、Python/toolchainバージョン、実行コマンドを保存します。
既存の作業ツリーを削除・復元する必要はありません。このライセンス整理ではbuild未実行です。

将来の再現手順: Python 3.11 x64の隔離環境を作成 → レビュー済み固定依存を導入 →
`scripts/collect_licenses.py --output <新しい監査用ディレクトリ>`で収集とmanifest警告を確認 →
specとbuild scriptを同じPythonで実行 → focused tests/frozen smoke/生成物を監査 →
同一sourceに対応するソース提供物とbinaryを照合します。未固定の下記pip手順だけでは
再現可能な公開ビルドにはなりません。DLL置換／relinkや依存sourceの作り方も別途必要です。

`LICENSE`と`PROJECT_LICENSE.md`はspec内のresourceとbundle直下の両方へsourceから
コピーされます。過去のdist/backupへ手動で差し替えず、承認された次回buildで生成します。

Python 3.11 x64と`requirements.txt`、`requirements-build.txt`の依存を使用します。PyInstaller one-folderだけを標準とし、one-fileは作成しません。

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1 -Clean -RunTests -RunSmoke -CreateZip
```

`-Clean`を指定した場合だけリポジトリ直下の`build/`と`dist/`を削除します。通常の`config.json`や`data/`は削除しません。出力は`dist/NivisViewer/`、バージョン付きZIP、`.sha256`です。ZIP内トップレベルは`NivisViewer/`です。

スモークはoffscreen Qtで一時profileと一時入力を使い、Qt plugin、JPEG／PNG／WebP、ZIP／CBZ、PDFium、ConfigManager、MetadataStore、ThumbnailDiskCache、Browser／Viewer生成、shutdownをJSONへ記録します。

Qtの`qwindows.dll`またはimageformats pluginが不足する場合はPyInstallerの標準PySide6 hookと実際のwheel内容を確認します。PDFiumで失敗する場合はpypdfium2 wheel内のnative componentとbuild警告を確認し、推測したDLL名をspecへハードコードしません。clean buildは`-Clean`を付け、ライセンス収集結果も再監査します。
