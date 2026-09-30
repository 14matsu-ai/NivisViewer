# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files, collect_dynamic_libs, collect_delvewheel_libs_directory,
)

from app.version import windows_version_info_text
from scripts.portable_build_policy import prepare_binaries

root = Path(SPECPATH)
# Set this inside Python as well: process launch environments may override the
# shell's PATH. The source audit below rejects any external toolchain leakage.
os.environ["PATH"] = os.pathsep.join([
    sys.base_prefix, str(Path(sys.base_prefix) / "DLLs"),
    str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"],
])
license_dir = Path(os.environ.get("NIVIS_LICENSES_DIR", root / "licenses"))
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
        (str(license_dir), "licenses"),
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
a.binaries = prepare_binaries(
    a.binaries,
    allowed_roots=[root, sys.base_prefix, sys.prefix, os.environ["SystemRoot"]],
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
