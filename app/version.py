from __future__ import annotations

__version__ = "1.05"
VERSION_TUPLE = (1, 5, 0, 0)
COMPANY_NAME = "14matsu-ai"
PRODUCT_NAME = "NivisViewer"
APP_USER_MODEL_ID = "14matsu-ai.NivisViewer"
COPYRIGHT = "Copyright (c) 2026 14matsu-ai"
LICENSE_IDENTIFIER = "AGPL-3.0-or-later"


def windows_version_info_text() -> str:
    numeric = ", ".join(str(part) for part in VERSION_TUPLE)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric}),
    prodvers=({numeric}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041104B0',
        [
          StringStruct('CompanyName', '{COMPANY_NAME}'),
          StringStruct('FileDescription', '{PRODUCT_NAME}'),
          StringStruct('FileVersion', '{__version__}'),
          StringStruct('InternalName', '{PRODUCT_NAME}'),
          StringStruct('LegalCopyright', '{COPYRIGHT}'),
          StringStruct('Comments', 'License: {LICENSE_IDENTIFIER}'),
          StringStruct('OriginalFilename', '{PRODUCT_NAME}.exe'),
          StringStruct('ProductName', '{PRODUCT_NAME}'),
          StringStruct('ProductVersion', '{__version__}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1041, 1200])])
  ]
)"""
