"""Exercise absent/broken codecs before any application imports in fresh processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

# Preserve only the Python test-process launcher before the autouse OS guard.
# The scoped override below launches this interpreter with fixed inline test code.
_python_test_run = subprocess.run
_python_test_popen_init = subprocess.Popen.__init__

@pytest.mark.parametrize("failure", ["ModuleNotFoundError", "ImportError", "OSError"])
def test_optional_jxl_failure_keeps_other_formats_usable(failure, monkeypatch):
    script = r'''
import importlib.abc
import sys
class BlockJxl(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pillow_jxl" or fullname.startswith("pillow_jxl."):
            raise FAILURE("test unavailable JXL codec")
sys.meta_path.insert(0, BlockJxl())
import app.application_controller
import app.browser_image_detail
from app import pillow_plugins
from app.diagnostics_dialog import diagnostic_text
from app.image_source import create_image_source, ImageSourceError
from app.archive_backend import ArchiveEntry, ArchiveListing
from app.supported_formats import IMAGE_EXTENSIONS
from PySide6.QtWidgets import QApplication
from PIL import Image, features
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import zipfile
assert QApplication.instance() is None
assert not pillow_plugins.JXL_AVAILABLE
assert 'FAILURE' in pillow_plugins.JXL_IMPORT_ERROR
assert '.jxl' in IMAGE_EXTENSIONS
assert pillow_plugins.decoder_unavailable_message('画像.JXL')
assert pillow_plugins.decoder_unavailable_message('画像.png') is None
with TemporaryDirectory() as temp:
    root = Path(temp)
    assert 'pillow-jxl-plugin' in diagnostic_text(root)
    for fmt in ('PNG', 'JPEG', 'AVIF'):
        if fmt == 'AVIF':
            assert features.check('avif')
        folder = root / fmt
        folder.mkdir()
        path = folder / ('画像.' + fmt)
        Image.new('RGB', (32, 48), 'red').save(path, format=fmt)
        archive = root / (fmt + '.zip')
        with zipfile.ZipFile(archive, 'w') as output:
            output.write(path, path.name)
        for target in (path, folder, archive):
            source, _ = create_image_source(target)
            try:
                with source.open_image(source.list_images()[0]) as image:
                    assert image.size == (32, 48)
                    assert image.convert('RGB').getpixel((0, 0))[0] > 200
            finally:
                source.close()
    folder = root / '未導入'
    folder.mkdir()
    path = folder / '画像.JXL'
    path.write_bytes(b'not decoded without plugin')
    archive = root / '未導入.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.write(path, path.name)
    def assert_unavailable(source):
        try:
            try:
                source.open_image(source.list_images()[0])
            except ImageSourceError as exc:
                assert exc.code == 'decoder_unavailable'
                assert 'pillow-jxl-plugin' in str(exc)
            else:
                raise AssertionError('JXL unexpectedly decoded')
        finally:
            source.close()
    for target in (path, folder, archive):
        source, _ = create_image_source(target)
        assert_unavailable(source)
    class Backend:
        def list_entries(self, path, **kwargs):
            return ArchiveListing(str(path), (
                ArchiveEntry('画像.JXL', 100, None, False, False),
            ), '7z', None, False)
        def read_entry(self, *args, **kwargs):
            raise AssertionError('Unavailable decoder must not extract payload')
    for suffix in ('.7z', '.rar'):
        archive = root / ('未導入' + suffix)
        archive.write_bytes(b'fake archive')
        source, _ = create_image_source(archive, archive_backend_registry=
            SimpleNamespace(backend_for_path=lambda path: Backend()))
        assert_unavailable(source)
assert QApplication.instance() is None
'''.replace('FAILURE', failure)
    with monkeypatch.context() as launcher:
        launcher.setattr(subprocess.Popen, "__init__", _python_test_popen_init)
        result = _python_test_run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True, text=True, timeout=60,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_jxl_registration_remains_enabled():
    from app.pillow_plugins import JXL_AVAILABLE, JXL_IMPORT_ERROR, JpegXLImagePlugin
    assert JXL_AVAILABLE
    assert not JXL_IMPORT_ERROR
    assert JpegXLImagePlugin.DECODE_THREADS == 2
