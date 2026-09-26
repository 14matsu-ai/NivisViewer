# NivisViewer

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

## 目錄

- [專案簡介](#overview)
- [Windows x64 可攜版](#portable)
- [支援的輸入格式](#formats)
- [執行環境](#requirements)
- [安裝與啟動](#setup)
- [從檔案總管拖放的檢查方式](#drop)
- [授權條款](#license)

<a id="overview"></a>
## 專案簡介

NivisViewer 是一款適用於 Windows 的漫畫與圖片檢視器。它參考 ZipPla 的操作方式與行為，但採用獨立實作，並未重用 ZipPla 的原始碼。

它提供與最愛資料夾同步的資料夾樹、以固定大小格位顯示縮圖的 BrowserWindow、獨立的書籍閱讀視窗 ViewerWindow、設定介面、無間距雙頁顯示、可攜式縮圖快取、資料夾及單張圖片瀏覽、ZIP/CBZ、RAR/7z 系列壓縮檔與 PDF 閱讀、自然排序和非同步圖片載入。ViewerWindow 也支援使用滑鼠上一頁／下一頁側鍵切換前後書籍、可設定的右鍵拖曳手勢，以及全螢幕時從螢幕邊緣顯示介面控制項。

NivisViewer 的名稱源自拉丁語中表示「雪」的 *nix, nivis*。\
這個名字寄託了這樣的想法：瀏覽大量圖片時，也能像靜靜看著積雪一樣自然、從容。

<a id="portable"></a>
## Windows x64 可攜版

將發行的 ZIP 解壓縮至任意可寫入的資料夾，然後執行 `NivisViewer.exe`，不必另外安裝 Python。標準發行包採用 PyInstaller 單資料夾形式。當執行檔旁有 `portable.flag` 時，`config.json` 與 `data/`（歷史紀錄資料庫、縮圖快取、日誌）也會儲存在同一個可攜版資料夾。程式不會將目前工作目錄或正在瀏覽的圖片資料夾用作應用程式資料儲存位置。

在唯讀位置仍可瀏覽檔案，但無法儲存設定、歷史紀錄、縮圖快取及日誌。請將整個資料夾移至可寫入的位置。解除安裝時刪除 NivisViewer 資料夾即可。如果曾註冊 Windows 檔案關聯，請先在「設定 → Windows 整合」中解除。

同一使用者從同一可攜版資料夾再次啟動程式，或從檔案總管連續執行「開啟」命令時，請求會安全地轉交給既有程序。複製到其他位置的可攜版會以獨立實例執行。移動可攜版資料夾後，由於執行檔路徑改變，需要重新註冊 Windows 檔案關聯。

只有使用者在設定中明確註冊時，Windows 整合才會啟用。程式可加入「開啟檔案」與預設應用程式候選清單，但不會強制變更 Windows 預設應用程式或 UserChoice。可攜版不隨附 WinRAR 或 7-Zip，但包含 pypdfium2／PDFium。

程式尚未進行程式碼簽章，因此執行下載的發行包時，Windows SmartScreen 可能顯示警告。回報問題時，可以從 Viewer 的「說明」選單複製診斷資訊。日誌儲存在 `data\logs\NivisViewer.log`，不會自動傳送至外部。

<a id="formats"></a>
## 支援的輸入格式

- 資料夾
- 單張圖片（將其所在資料夾內支援的圖片作為一本書開啟）
- ZIP / CBZ
- RAR / CBR（使用使用者環境中的 WinRAR 或 7-Zip）
- 7z / CB7（使用受支援的 WinRAR 命令列設定或使用者環境中的 7-Zip）
- PDF（使用 pypdfium2 v5／PDFium）
- 圖片：JPEG、PNG、WebP、AVIF、JPEG XL（JXL）、BMP、GIF、TIFF、ICO、PSD、PSB

閱讀 RAR／7z／CBR／CB7 時，程式會從 Windows 檔案關聯、標準安裝位置、PATH，或「設定 → 壓縮檔」中指定的 WinRAR／7-Zip 路徑選擇已識別的外部命令列工具。如果使用者安裝的 WinRAR 附有受支援的官方主控台工具，僅為閱讀 RAR／CBR 無須另裝 7-Zip。程式不會啟動未知的檔案關聯應用程式，也不會將其交給 ShellExecute。

NivisViewer 不會自動下載或安裝 WinRAR、7-Zip，發行包也不包含它們的二進位檔。目前確認可用的 WinRAR 主控台工具僅支援 RAR。如果該工具無法將 7z／CB7 內容輸出至 stdout，程式會安全地回報 `unsupported_archive`；只有在 7-Zip 可用時，才會嘗試一次備援處理。

程式可以偵測加密的壓縮檔和 PDF，但不支援輸入密碼。對於分割 RAR，普通 `.rar` 檔案和首卷 `.part1.rar` 會列為候選書籍；後續的 `.part2.rar` 及 `.r00` 等檔案會從書籍清單中排除。`.7z.001` 等分割 7z 尚未正式支援。固實壓縮檔只擷取所需頁面，因此跳至靠後的頁面可能較慢。

PDF 以唯讀方式處理，可顯示一般頁面與註解。不支援編輯、文字搜尋與選取、連結操作、目錄介面、表單輸入、JavaScript 或 XFA。PDF 的 100% 顯示相當於 96 邏輯 DPI。在高 DPI 螢幕上，繪製解析度會考慮裝置像素比例；調整視窗大小或縮放停止約 180 毫秒後，會依目標顯示尺寸重新繪製。

<a id="requirements"></a>
## 執行環境

- Windows
- Python 3.11 或更新版本（從原始碼執行時需要）

<a id="setup"></a>
## 安裝與啟動

在 PowerShell 中進入儲存庫根目錄，安裝相依套件並啟動程式：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

命令列可以指定檔案、資料夾或多個路徑。第二個及後續路徑會在獨立的 Viewer 視窗中開啟。

```powershell
NivisViewer.exe "D:\Comics\book.cbz"
NivisViewer.exe --reuse "book1.cbz" "book2.pdf"
NivisViewer.exe --new-window "C:\Images"
NivisViewer.exe --browser-only "D:\Books"
```

`--new-window` 與 `--reuse` 不能同時使用。`--no-restore` 僅在本次啟動時阻止還原上次位置。

如需執行測試，還要安裝開發用相依套件：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

<a id="drop"></a>
## 從 Windows 檔案總管拖放至 Browser 中央區域的檢查方式

請以一般使用者而非系統管理員身分啟動 NivisViewer。從檔案總管分別將檔案拖到 Browser 中央的項目上與空白處。游標應顯示可接受拖放；Browser 應開啟檔案所在資料夾、選取並置中顯示對應項目，而不開啟 Viewer。拖入資料夾時應顯示該資料夾。一次拖入多個檔案時，會選取同一父資料夾中的項目，並提示其他父資料夾中的項目數量。不接受 HTTP／HTTPS URL。

從檔案總管拖放至 Browser 中央區域表示「顯示此位置」，而不是複製或移動。只有把 NivisViewer 內的項目拖到資料夾項目、資料夾樹或最愛資料夾時，才會執行既有的複製／移動操作。

<a id="license"></a>
## 授權條款

NivisViewer 目前的專案授權條款為 **GNU AGPL 第 3 版或更新版本（`AGPL-3.0-or-later`）**。正式授權條款全文見 [LICENSE](LICENSE)，專案授權聲明及著作權資訊見 [PROJECT_LICENSE.md](PROJECT_LICENSE.md)。有關從 ZipPlaFork 移植的結構來源，請參閱 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 與 [ZipPlaFork 比較紀錄](docs/ZIPPLAFORK_COMPARISON.md)。相依套件各自的 MIT、BSD、Apache、LGPL、GPL 等授權條款均予以保留。
