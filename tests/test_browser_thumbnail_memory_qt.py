"""Run in the real patched repository with PySide6 and QT_QPA_PLATFORM=offscreen.

Only temporary config/fake thumbnails are used. No real app/native input is run.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from app.browser_thumbnail_memory import BrowserThumbnailMemoryBroker
from app.browser_thumbnail_memory_policy import GIB, MIB
from app.browser_workflow_policy import WORKFLOW_DEFAULTS
from app.browser_workflow_settings import BrowserWorkflowSettings
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    if application.platformName().casefold() != 'offscreen':
        pytest.skip('These tests require an offscreen QApplication; never show native UI.')
    return application


def test_config_saves_mode_roundtrip_and_normalizes_unknown(tmp_path,app):
    cfg=ConfigManager(tmp_path/'config.json')
    cfg.load()
    assert cfg.get('browser_thumbnail_memory_mode')=='auto'
    cfg.apply({'browser_thumbnail_memory_mode':'1024'},save=True)
    another=ConfigManager(tmp_path/'config.json')
    another.load()
    assert another.get('browser_thumbnail_memory_mode')=='1024'
    another.apply({'browser_thumbnail_memory_mode':'invalid'},save=False)
    assert another.get('browser_thumbnail_memory_mode')=='auto'


def test_settings_modes_roundtrip_and_default(app):
    group=BrowserWorkflowSettings()
    try:
        for mode in ('auto','128','256','512','1024','2048'):
            group.load(dict(WORKFLOW_DEFAULTS,browser_thumbnail_memory_mode=mode))
            assert group.values()['browser_thumbnail_memory_mode']==mode
        group.load(WORKFLOW_DEFAULTS)
        assert group.values()['browser_thumbnail_memory_mode']=='auto'
    finally:
        group.close()
        group.deleteLater()


def test_settings_status_uses_provider_bytes_and_stops_polling_when_hidden(app):
    parent=QWidget()
    parent.thumbnail_provider=SimpleNamespace(browser_memory_diagnostics=lambda: {
        'bytes': 8*MIB, 'limit_bytes': 128*MIB, 'reason': 'headroom_limited',
    })
    group=BrowserWorkflowSettings(parent)
    try:
        parent.show()
        group.show()
        app.processEvents()
        assert group._memory_status_timer.isActive()
        assert '8' in group.memory_status.text()
        assert '128' in group.memory_status.text()
        assert 'Browser' in group.memory_status.text()
        group.hide()
        assert not group._memory_status_timer.isActive()
    finally:
        parent.close()
        parent.deleteLater()
        app.processEvents()


def test_real_qimage_store_exceeds_old_128_entries_but_obeys_bytes(app):
    provider=BrowserThumbnailProvider(loader=lambda *_:None,disk_cache_enabled=False)
    try:
        provider.configure_browser_memory(mode='512',limit_bytes=512*MIB,
            desired_bytes=512*MIB,group_capacity_bytes=GIB,reason='ready')
        image=QImage(32,32,QImage.Format.Format_RGB32)
        image.fill(0)
        for index in range(300):
            provider._consume_thumbnail_finished(
                str(index)+'.jpg',provider.generation,1,(1,),ThumbnailLoadResult(image))
        assert provider.browser_memory_diagnostics()['entries']==300
        provider.configure_browser_memory(mode='512',limit_bytes=100*image.sizeInBytes(),
            desired_bytes=512*MIB,group_capacity_bytes=GIB,reason='headroom_limited')
        assert provider.browser_memory_diagnostics()['entries']<=100
        assert provider.memory_cache_bytes<=100*image.sizeInBytes()
    finally:
        provider.close()


def test_qt_broker_shared_fallback_shrinks_first_and_closes_timer(app):
    broker=BrowserThumbnailMemoryBroker(app,reader=lambda:None,clock=lambda:0)
    clients=[]
    class Client(QObject):
        def __init__(self):
            super().__init__()
            self.window=QWidget()
            self.window._shutdown_prepared=False
            self.window.show()  # offscreen widget, never the real application.
            self.options={'browser_thumbnail_memory_mode':'1024'}
            self._memory_near_bytes=700*MIB
            self.window.thumbnail_provider=SimpleNamespace(memory_cache_bytes=0,memory_cache_limit_bytes=0)
        def _apply_memory_grant(self,grant):
            self.window.thumbnail_provider.memory_cache_limit_bytes=grant.limit_bytes
    try:
        for _ in range(2):
            client=Client();clients.append(client);broker.register(client)
        assert sum(c.window.thumbnail_provider.memory_cache_limit_bytes for c in clients)==256*MIB
        for client in clients:
            broker.unregister(id(client))
        assert not broker._timer.isActive()
    finally:
        broker.close()
        for client in clients:
            client.window.close();client.window.deleteLater();client.deleteLater()
