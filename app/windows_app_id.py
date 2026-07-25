from __future__ import annotations

import ctypes
import sys
from typing import Callable

from .version import APP_USER_MODEL_ID


def set_windows_app_user_model_id(
    app_id: str = APP_USER_MODEL_ID,
    *,
    platform_name: str | None = None,
    setter: Callable[[str], int] | None = None,
) -> bool:
    if (platform_name or sys.platform) != "win32":
        return False
    try:
        function = setter
        if function is None:
            function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        return int(function(app_id)) == 0
    except (AttributeError, OSError, TypeError, ValueError):
        return False
