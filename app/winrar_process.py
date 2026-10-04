from __future__ import annotations

from .seven_zip_process import (
    SevenZipProcessResult,
    SevenZipProcessRunner,
)


WinRARProcessResult = SevenZipProcessResult


class WinRARProcessRunner(SevenZipProcessRunner):
    """Safe process runner shared with 7-Zip.

    The locator supplies only WinRAR's console components (UnRAR.exe or
    Rar.exe), so this runner never starts WinRAR.exe's GUI process.
    """

