"""Folder tree presentation without asking the shell to inspect real paths."""
from PySide6.QtCore import QDir, QFileInfo
from PySide6.QtWidgets import QFileIconProvider, QFileSystemModel


class MetadataOnlyTreeIconProvider(QFileIconProvider):
    def __init__(self):
        super().__init__()
        # Create generic icons on the GUI thread, before the model starts its
        # gatherer. Never pass QFileInfo to the native shell icon provider:
        # custom folder icons/overlays can access cloud placeholder contents.
        self.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)
        self._icons = {kind: super(MetadataOnlyTreeIconProvider, self).icon(kind)
                       for kind in QFileIconProvider.IconType}

    def icon(self, value):
        if isinstance(value, QFileInfo):
            value = (QFileIconProvider.IconType.Drive if value.isRoot()
                     else QFileIconProvider.IconType.Folder if value.isDir()
                     else QFileIconProvider.IconType.File)
        return self._icons[value]

    def type(self, info):
        # The tree hides the type column, but QFileSystemModel still gathers
        # it. Do not delegate to a platform MIME/type lookup for the real path.
        return "Folder" if info.isDir() else "File"


class FolderTreeModel(QFileSystemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._metadata_icon_provider = MetadataOnlyTreeIconProvider()
        self.setIconProvider(self._metadata_icon_provider)
        self.setOption(QFileSystemModel.Option.DontUseCustomDirectoryIcons, True)
        self.setOption(QFileSystemModel.Option.DontResolveSymlinks, True)
        self.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
