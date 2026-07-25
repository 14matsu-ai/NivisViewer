# Windowsポータブルビルド

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
