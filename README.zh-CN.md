# NivisViewer

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

## 目录

- [项目简介](#overview)
- [Windows x64 便携版](#portable)
- [支持的输入格式](#formats)
- [运行环境](#requirements)
- [安装与启动](#setup)
- [从资源管理器拖放的检查方法](#drop)
- [许可证](#license)

<a id="overview"></a>
## 项目简介

NivisViewer 是一款适用于 Windows 的漫画与图片阅读器。它参考了 ZipPla 的操作方式与行为，但采用独立实现，不复用 ZipPla 的源代码。

它提供与收藏文件夹同步的文件夹树、采用固定大小网格显示缩略图的 BrowserWindow、独立的书籍阅读窗口 ViewerWindow、设置界面、无间隙双页显示、便携式缩略图缓存、文件夹及单张图片浏览、ZIP/CBZ、RAR/7z 系列压缩包与 PDF 阅读、自然排序和异步图片加载。ViewerWindow 还支持用鼠标前进／后退侧键切换前后书籍、可配置的右键拖动手势，以及全屏时通过屏幕边缘显示界面控件。

NivisViewer 的名称源自拉丁语中表示“雪”的 *nix, nivis*。\
这个名字寄托了这样的想法：浏览大量图片时，也能像静静看着积雪一样自然、从容。

<a id="portable"></a>
## Windows x64 便携版

将发布的 ZIP 解压到任意可写文件夹，然后运行 `NivisViewer.exe`，无需另行安装 Python。标准发布包采用 PyInstaller 单文件夹形式。当可执行文件旁存在 `portable.flag` 时，`config.json` 和 `data/`（历史记录数据库、缩略图缓存、日志）也保存在同一个便携版文件夹中。程序不会把当前工作目录或正在浏览的图片文件夹用作应用数据保存位置。

在只读位置仍可浏览文件，但无法保存设置、历史记录、缩略图缓存和日志。请将整个文件夹移至可写位置。卸载时删除 NivisViewer 文件夹即可。如果注册过 Windows 文件关联，请先在“设置 → Windows 集成”中解除关联。

同一用户从同一便携版文件夹再次启动程序，或从资源管理器连续执行“打开”命令时，请求会安全地转交给现有进程。复制到其他位置的便携版会作为独立实例运行。移动便携版文件夹后，由于可执行文件路径改变，需要重新注册 Windows 文件关联。

只有用户在设置中明确注册时，Windows 集成才会启用。程序可加入“打开方式”和默认应用候选列表，但不会强制修改 Windows 默认应用或 UserChoice。便携版不附带 WinRAR 或 7-Zip，但包含 pypdfium2／PDFium。

程序尚未进行代码签名，因此运行下载的发布包时，Windows SmartScreen 可能显示警告。报告问题时，可以从 Viewer 的“帮助”菜单复制诊断信息。日志保存在 `data\logs\NivisViewer.log`，不会自动发送到外部。

<a id="formats"></a>
## 支持的输入格式

- 文件夹
- 单张图片（将其所在文件夹内的受支持图片作为一本书打开）
- ZIP / CBZ
- RAR / CBR（使用用户环境中的 WinRAR 或 7-Zip）
- 7z / CB7（使用受支持的 WinRAR 命令行配置或用户环境中的 7-Zip）
- PDF（使用 pypdfium2 v5／PDFium）
- 图片：JPEG、PNG、WebP、AVIF、JPEG XL（JXL）、BMP、GIF、TIFF、ICO、PSD、PSB

阅读 RAR／7z／CBR／CB7 时，程序会从 Windows 文件关联、标准安装位置、PATH，或“设置 → 压缩包”中指定的 WinRAR／7-Zip 路径选择已识别的外部命令行工具。如果用户安装的 WinRAR 附带受支持的官方控制台工具，仅为阅读 RAR／CBR 无需另装 7-Zip。程序不会启动未知的文件关联应用，也不会将其交给 ShellExecute。

NivisViewer 不会自动下载或安装 WinRAR、7-Zip，发布包也不包含它们的二进制文件。目前确认可用的 WinRAR 控制台工具仅支持 RAR。如果该工具无法将 7z／CB7 内容输出到 stdout，程序会安全地返回 `unsupported_archive`；只有存在可用的 7-Zip 时，才会尝试一次回退。

程序可以检测加密的压缩包和 PDF，但不支持输入密码。对于分卷 RAR，普通 `.rar` 文件和首卷 `.part1.rar` 会被列为候选书籍；后续的 `.part2.rar` 及 `.r00` 等文件会从书籍列表中排除。`.7z.001` 等分卷 7z 尚未正式支持。固实压缩包只提取所需页面，因此跳至靠后的页面可能较慢。

PDF 以只读方式处理，可显示普通页面和注释。不支持编辑、文本搜索与选择、链接操作、目录界面、表单输入、JavaScript 或 XFA。PDF 的 100% 显示相当于 96 逻辑 DPI。在高 DPI 屏幕上，渲染分辨率会考虑设备像素比；调整窗口大小或缩放停止约 180 毫秒后，会按照目标显示尺寸重新渲染。

<a id="requirements"></a>
## 运行环境

- Windows
- Python 3.11 或更高版本（从源代码运行时需要）

<a id="setup"></a>
## 安装与启动

在 PowerShell 中进入仓库根目录，安装依赖并启动程序：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

命令行可以指定文件、文件夹或多个路径。第二个及后续路径会在独立的 Viewer 窗口中打开。

```powershell
NivisViewer.exe "D:\Comics\book.cbz"
NivisViewer.exe --reuse "book1.cbz" "book2.pdf"
NivisViewer.exe --new-window "C:\Images"
NivisViewer.exe --browser-only "D:\Books"
```

`--new-window` 与 `--reuse` 不能同时使用。`--no-restore` 仅在本次启动时阻止恢复上次位置。

如需运行测试，还要安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

<a id="drop"></a>
## 从 Windows 资源管理器拖放到 Browser 中央区域的检查方法

请以普通用户而非管理员身份启动 NivisViewer。从资源管理器分别将文件拖到 Browser 中央的项目上和空白处。光标应显示可接受拖放；Browser 应打开文件所在文件夹、选中并居中显示对应项目，而不打开 Viewer。拖入文件夹时应显示该文件夹。一次拖入多个文件时，会选中同一父文件夹中的项目，并提示其他父文件夹中的项目数量。不接受 HTTP／HTTPS URL。

从资源管理器拖放到 Browser 中央区域表示“显示此位置”，而不是复制或移动。只有把 NivisViewer 内的项目拖到文件夹项目、文件夹树或收藏夹时，才会执行现有的复制／移动操作。

<a id="license"></a>
## 许可证

NivisViewer 当前的项目许可证为 **GNU AGPL 第 3 版或更高版本（`AGPL-3.0-or-later`）**。正式许可文本见 [LICENSE](LICENSE)，项目许可声明及版权信息见 [PROJECT_LICENSE.md](PROJECT_LICENSE.md)。有关从 ZipPlaFork 移植的结构来源，请参阅 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [ZipPlaFork 对比记录](docs/ZIPPLAFORK_COMPARISON.md)。依赖库各自的 MIT、BSD、Apache、LGPL、GPL 等许可证保持不变。
