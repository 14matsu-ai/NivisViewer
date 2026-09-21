from __future__ import annotations

from app.version import (
    APP_USER_MODEL_ID,
    VERSION_TUPLE,
    __version__,
    windows_version_info_text,
)
from app.windows_app_id import set_windows_app_user_model_id


def test_version_has_display_and_numeric_forms():
    assert __version__ == "1.0.12"
    assert VERSION_TUPLE == (1, 0, 12, 0)
    text = windows_version_info_text()
    assert "14matsu-ai" in text
    assert "NivisViewer.exe" in text
    assert __version__ in text
    assert "StringStruct('FileVersion', '1.0.12')" in text
    assert "StringStruct('ProductVersion', '1.0.12')" in text
    assert "filevers=(1, 0, 12, 0)" in text


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
