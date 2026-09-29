from __future__ import annotations

import json
import time
from threading import Event

import pytest

from app.config_manager import ConfigManager
from app.release_updates import REPOSITORY_URL, fetch_latest_release, numeric_version
from app.settings_dialog import SettingsDialog


def test_numeric_version_and_public_release_validation(monkeypatch) -> None:
    assert numeric_version('v1.10.0') > numeric_version('1.9.9')
    for bad in ('v1.2', '1.2.3-beta', 'release-1.2.3'):
        with pytest.raises(ValueError):
            numeric_version(bad)

    class Response:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(self.value).encode()

    data = {'tag_name': 'v1.2.0', 'html_url': REPOSITORY_URL + '/releases/tag/v1.2.0',
            'draft': False, 'prerelease': False}
    monkeypatch.setattr('app.release_updates.urlopen', lambda _req, timeout: Response(data))
    assert fetch_latest_release() == ('v1.2.0', data['html_url'])
    data['prerelease'] = True
    with pytest.raises(ValueError):
        fetch_latest_release()
    data['prerelease'] = False
    data['html_url'] = 'https://example.com/release'
    with pytest.raises(ValueError):
        fetch_latest_release()


def test_manual_update_check_is_async_and_external_links_are_click_only(
    tmp_path, qapp, monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    calls = []
    url = REPOSITORY_URL + '/releases/tag/v99.0.0'
    monkeypatch.setattr('app.settings_dialog.fetch_latest_release',
                        lambda: ('v99.0.0', url))
    monkeypatch.setattr('app.settings_dialog.QDesktopServices.openUrl',
                        lambda target: calls.append(target.toString()) or True)
    dialog = SettingsDialog(config)
    try:
        assert calls == []
        dialog.check_updates_button.click()
        dialog.check_updates_button.click()
        deadline = time.monotonic() + 3
        while not dialog.check_updates_button.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert dialog.check_updates_button.isEnabled()
        assert '99.0.0' in dialog.update_status_label.text()
        assert not dialog.release_page_button.isHidden()
        assert calls == []
        dialog.github_page_button.click()
        dialog.release_page_button.click()
        assert calls == [REPOSITORY_URL, url]
    finally:
        dialog.reject()


def test_update_check_failure_and_close_ignore_late_result(
    tmp_path, qapp, monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    started = Event()
    release = Event()

    def blocked_fetch():
        started.set()
        assert release.wait(3)
        raise OSError('offline')

    monkeypatch.setattr('app.settings_dialog.fetch_latest_release', blocked_fetch)
    dialog = SettingsDialog(config)
    dialog.check_updates_button.click()
    assert started.wait(1)
    assert not dialog.check_updates_button.isEnabled()
    dialog.reject()
    release.set()
    deadline = time.monotonic() + 3
    while dialog._release_check_workers and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not dialog._release_check_workers


def test_update_check_failure_and_external_open_failure_are_shown(
    tmp_path, qapp, monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    def fail_fetch():
        raise OSError('offline')
    monkeypatch.setattr('app.settings_dialog.fetch_latest_release', fail_fetch)
    monkeypatch.setattr('app.settings_dialog.QDesktopServices.openUrl',
                        lambda _url: False)
    dialog = SettingsDialog(config)
    try:
        dialog.check_updates_button.click()
        deadline = time.monotonic() + 3
        while not dialog.check_updates_button.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert dialog.check_updates_button.isEnabled()
        assert '確認できません' in dialog.update_status_label.text()
        dialog.github_page_button.click()
        assert '開けません' in dialog.update_status_label.text()
    finally:
        dialog.reject()


def test_invalid_release_response_is_not_reported_as_network_failure(
    tmp_path, qapp, monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    def invalid_release():
        raise ValueError('invalid release version')
    monkeypatch.setattr('app.settings_dialog.fetch_latest_release', invalid_release)
    dialog = SettingsDialog(config)
    try:
        dialog.check_updates_button.click()
        deadline = time.monotonic() + 3
        while not dialog.check_updates_button.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert dialog.check_updates_button.isEnabled()
        assert 'リリース情報が不正' in dialog.update_status_label.text()
        assert 'ネットワーク' not in dialog.update_status_label.text()
    finally:
        dialog.reject()
