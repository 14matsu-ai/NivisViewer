# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files, collect_dynamic_libs, collect_delvewheel_libs_directory,
)

from app.version import windows_version_info_text

root = Path(SPECPATH)
version_file = root / "build" / "NivisViewer_version_info.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(windows_version_info_text(), encoding="utf-8")

pypdfium_binaries = collect_dynamic_libs("pypdfium2")
pypdfium_data = collect_data_files(
    "pypdfium2",
    include_py_files=False,
)
app_icon = root / "assets" / "icons" / "nivisviewer.ico"
jxl_data, jxl_binaries = collect_delvewheel_libs_directory(
    "pillow_jxl", libdir_name="pillow_jxl_plugin.libs",
)

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=pypdfium_binaries + jxl_binaries,
    datas=pypdfium_data + jxl_data
    + [
        (str(root / "LICENSE"), "."),
        (str(root / "PROJECT_LICENSE.md"), "."),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(root / "portable.flag"), "."),
        (str(root / "licenses"), "licenses"),
        (str(root / "assets" / "icons"), "assets/icons"),
    ],
    hiddenimports=["pillow_jxl.JpegXLImagePlugin", "PySide6.QtSvg", "PIL.AvifImagePlugin"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "scripts" / "frozen_smoke_hook.py")],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NivisViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    version=str(version_file),
    icon=str(app_icon),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NivisViewer",
)
