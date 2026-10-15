"""Saved-disk allowance is independent from Browser RAM and native scratch."""
from dataclasses import replace
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtGui import QImage

from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import ThumbnailRenderSpec
from tests.test_thumbnail_admission import source, pixels


@pytest.mark.parametrize('settings,capacity,days', [({},0,0),
    ({'thumbnail_cache_limit_mb':512},512,0),
    ({'thumbnail_cache_max_unused_days':90},0,90),
    ({'thumbnail_cache_limit_mb':1024,'thumbnail_cache_max_unused_days':90},1024,90),
    ({'thumbnail_cache_limit_mb':0,'thumbnail_cache_max_unused_days':0},0,0)])
def test_missing_and_explicit_settings_survive_dialog_apply_and_restart(tmp_path,qapp,settings,capacity,days):
    path=tmp_path/'config.json'
    path.write_text(json.dumps(settings),encoding='utf-8')
    config=ConfigManager(path); config.load()
    assert config.get('thumbnail_cache_limit_mb') == capacity
    dialog=SettingsDialog(config)
    try:
        assert dialog.cache_limit_enabled_checkbox.isChecked() == (capacity>0)
        assert dialog.cache_limit_spin.isEnabled() == (capacity>0)
        dialog.browser_search_history_limit_spin.setValue(53)
        dialog.apply_settings()
        saved=ConfigManager(path); saved.load()
        assert saved.get('thumbnail_cache_limit_mb') == capacity
        assert saved.get('thumbnail_cache_max_unused_days') == days
    finally:
        dialog.close(); qapp.processEvents()


def test_capacity_option_cancel_apply_disable_and_reset(tmp_path,qapp):
    config=ConfigManager(tmp_path/'config.json'); config.load()
    dialog=SettingsDialog(config)
    dialog.cache_limit_enabled_checkbox.setChecked(True)
    dialog.cache_limit_spin.setValue(768)
    dialog.reject()
    assert config.get('thumbnail_cache_limit_mb') == 0
    dialog.close()
    dialog=SettingsDialog(config)
    try:
        dialog.cache_limit_enabled_checkbox.setChecked(True)
        dialog.cache_limit_spin.setValue(768)
        dialog.apply_settings()
        assert config.get('thumbnail_cache_limit_mb') == 768
        dialog.cache_limit_enabled_checkbox.setChecked(False)
        dialog.apply_settings()
        assert ConfigManager(config.path).load()['thumbnail_cache_limit_mb'] == 0
        dialog._reset_browser_scope()
        assert not dialog.cache_limit_enabled_checkbox.isChecked()
        assert dialog.cache_unused_days_combo.currentData() == 0
    finally:
        dialog.close(); qapp.processEvents()


def test_unlimited_never_lru_prunes_and_explicit_expiry_still_applies(tmp_path,qapp):
    cache=ThumbnailDiskCache(tmp_path/'cache',limit_bytes=1)
    items=[source(tmp_path/f'{i}.png') for i in range(4)]
    try:
        assert cache.put(items[0],64,pixels())
        cache.set_limit_mb(0)
        for item in items[1:]: assert cache.put(item,64,pixels())
        assert cache.limit_bytes == 0
        cache._connection.execute('UPDATE entries SET last_used=?',(time.time()-180*86400,))
        cache._commit()
        cache.prune(remove_orphans=True)
        assert cache.statistics()['entry_count'] == 4
        assert cache.usage_bytes()>1
        cache.set_max_unused_days(90)
        cache.prune(remove_orphans=False)
        assert cache.statistics()['entry_count'] == 0
    finally:
        cache.close()


@pytest.mark.parametrize('failure',['space','encoder','commit'])
def test_disk_pressure_and_write_failures_keep_existing_cache_readable(tmp_path,qapp,monkeypatch,failure):
    import app.thumbnail_disk_cache as module
    item=source(tmp_path/'image.jpg')
    spec=ThumbnailRenderSpec.from_settings(64,'square_1_1','smart_crop')
    cache=ThumbnailDiskCache(tmp_path/'cache')
    old=replace(spec,implementation_version=3)
    assert cache.put(item,old,pixels())
    original_files=set(cache.files_dir.iterdir())
    original_connection=cache._connection
    try:
        if failure=='space':
            monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
        elif failure=='encoder':
            def fail(*args,**kwargs): raise OSError('synthetic write failure')
            monkeypatch.setattr(Image.Image,'save',fail)
        else:
            class FailedCommit:
                def __getattr__(self,key): return getattr(original_connection,key)
                def commit(self): raise sqlite3.OperationalError('synthetic disk full')
            cache._connection=FailedCommit()
        assert not cache.put(item,spec,pixels())
        cache._connection=original_connection
        assert cache.enabled
        assert cache.get_suitable(item,old) is not None
        assert original_files.issubset(set(cache.files_dir.iterdir()))
        assert cache.statistics()['entry_count'] == 1
    finally:
        cache._connection=original_connection
        cache.close()
