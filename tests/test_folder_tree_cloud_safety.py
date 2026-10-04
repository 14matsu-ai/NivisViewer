import time

from PySide6.QtCore import QFileInfo, Qt
from PySide6.QtWidgets import QFileIconProvider

from app.folder_tree_model import FolderTreeModel, MetadataOnlyTreeIconProvider


def test_tree_provider_never_passes_paths_to_native_provider(qapp, tmp_path, monkeypatch):
    folder = tmp_path / "クラウド"
    folder.mkdir()
    file = folder / "online.bin"
    file.touch()
    original_icon = QFileIconProvider.icon
    calls = []

    def checked_icon(self, value):
        assert not isinstance(value, QFileInfo), "real path reached shell icon lookup"
        calls.append(value)
        return original_icon(self, value)

    def forbidden_type(*args):
        raise AssertionError("real path reached native type lookup")

    monkeypatch.setattr(QFileIconProvider, "icon", checked_icon)
    monkeypatch.setattr(QFileIconProvider, "type", forbidden_type)
    provider = MetadataOnlyTreeIconProvider()
    for path, expected in ((folder, "Folder"), (file, "File")):
        info = QFileInfo(str(path))
        assert not provider.icon(info).isNull()
        assert provider.type(info) == expected
    assert calls


def test_tree_enumerates_nested_folders_with_safe_provider(qapp, tmp_path):
    folder = tmp_path / "クラウド" / "子フォルダ"
    folder.mkdir(parents=True)
    model = FolderTreeModel()
    loaded = []
    model.directoryLoaded.connect(loaded.append)
    try:
        model.setRootPath(str(tmp_path))
        for path in (tmp_path, folder.parent, folder):
            index = model.index(str(path))
            model.fetchMore(index)
            deadline = time.monotonic() + 5
            while str(path).replace("\\", "/") not in loaded and time.monotonic() < deadline:
                qapp.processEvents()
                time.sleep(0.01)
            assert str(path).replace("\\", "/") in loaded
            assert not model.data(index, Qt.ItemDataRole.DecorationRole).isNull()
        assert isinstance(model.iconProvider(), MetadataOnlyTreeIconProvider)
    finally:
        model.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
