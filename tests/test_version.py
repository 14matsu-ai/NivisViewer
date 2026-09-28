from __future__ import annotations

from app.version import (
    APP_USER_MODEL_ID,
    VERSION_TUPLE,
    __version__,
    windows_version_info_text,
)
from app.windows_app_id import set_windows_app_user_model_id


def test_version_has_display_and_numeric_forms():
    display_parts = tuple(int(part) for part in __version__.split("."))
    assert len(display_parts) == 3
    assert len(VERSION_TUPLE) == 4
    assert VERSION_TUPLE[:3] == display_parts
    assert all(isinstance(part, int) and 0 <= part <= 65535 for part in VERSION_TUPLE)
    text = windows_version_info_text()
    assert "14matsu-ai" in text
    assert "NivisViewer.exe" in text
    assert __version__ in text
    assert f"StringStruct('FileVersion', '{__version__}')" in text
    assert f"StringStruct('ProductVersion', '{__version__}')" in text
    numeric = ", ".join(str(part) for part in VERSION_TUPLE)
    assert f"filevers=({numeric})" in text
    assert f"prodvers=({numeric})" in text


def test_app_user_model_id_is_mockable_and_non_windows_is_noop():
    calls = []
    assert set_windows_app_user_model_id(
        platform_name="win32",
        setter=lambda value: calls.append(value) or 0,
    )
    assert calls == [APP_USER_MODEL_ID]
    assert not set_windows_app_user_model_id(
        platform_name="linux",
        setter=lambda _value: 0,
    )
