from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from app.config_manager import ConfigManager
from app.viewer_window import ViewerWindow, _CLOSING_VIEWERS


def test_destroyed_viewer_releases_deferred_close_retention(tmp_path, qapp):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.close()
        assert window in _CLOSING_VIEWERS
        # An owner can finish shutdown and dispose the Qt window before its
        # queued close timer runs. Do not destroy live workers in this test.
        assert window.prepare_shutdown()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not isValid(window)
        assert window not in _CLOSING_VIEWERS
    finally:
        # Also keep the pre-fix reproduction from poisoning session teardown.
        _CLOSING_VIEWERS.discard(window)
        if isValid(window):
            assert window.prepare_shutdown()
            window.close()
        qapp.processEvents()
