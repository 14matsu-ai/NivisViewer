import json
from threading import Event

import pytest
from PySide6.QtWidgets import QGroupBox

from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from tests.test_pdf_support import _open_many_page_pdf_window, _close_measured_pdf_window
from tests.test_rar_raster_pipeline import Backend, viewer_fixture
from tests.test_zip_raster_viewer_integration import _wait_until


@pytest.mark.parametrize('legacy, expected', [
    ({'viewer_zip_priority_screens': 17}, (17, 1, 0)),
    ({'viewer_prefetch_preset': 'standard'}, (2, 3, 3)),
    ({'viewer_prefetch_preset': 'memory_saver'}, (2, 1, 0)),
    ({'viewer_prefetch_preset': 'disabled', 'viewer_prefetch_pdf_forward_units': 9}, (2, 0, 0)),
    ({'viewer_prefetch_preset': 'custom', 'viewer_prefetch_pdf_forward_units': 13,
      'viewer_prefetch_pdf_backward_units': 7}, (2, 13, 7)),
    ({'viewer_zip_priority_screens': 19, 'viewer_prefetch_forward_screens': 5,
      'viewer_pdf_prefetch_forward_screens': 4, 'viewer_pdf_prefetch_backward_screens': 2,
      'viewer_pdf_prefetch_individual': False}, (5, 4, 2)),
])
def test_legacy_migration_preserves_intent_and_roundtrip(tmp_path, legacy, expected):
    path = tmp_path / '設定.json'
    path.write_text(json.dumps(legacy), encoding='utf-8')
    config = ConfigManager(path)
    values = config.load()
    assert tuple(values[key] for key in (
        'viewer_prefetch_forward_screens', 'viewer_pdf_prefetch_forward_screens',
        'viewer_pdf_prefetch_backward_screens')) == expected
    assert values['viewer_prefetch_backward_screens'] == 1
    assert values['viewer_pdf_prefetch_individual'] == legacy.get('viewer_pdf_prefetch_individual', True)
    config.save()
    assert ConfigManager(path).load() == values


def test_new_defaults_and_partial_legacy_updates(tmp_path):
    config = ConfigManager(tmp_path / 'new.json')
    config.load()
    assert [config.get(key) for key in (
        'viewer_prefetch_forward_screens', 'viewer_prefetch_backward_screens',
        'viewer_pdf_prefetch_individual', 'viewer_pdf_prefetch_forward_screens',
        'viewer_pdf_prefetch_backward_screens')] == [2, 1, True, 1, 0]
    config.apply({'viewer_prefetch_preset': 'custom', 'viewer_prefetch_pdf_forward_units': 8,
                  'viewer_prefetch_pdf_backward_units': 6})
    config.apply({'viewer_prefetch_pdf_forward_units': 11})
    assert config.get('viewer_pdf_prefetch_backward_screens') == 6
    config.apply({'viewer_pdf_prefetch_individual': False})
    assert config.get('viewer_pdf_prefetch_forward_screens') == 11


@pytest.mark.parametrize('value, expected', [(-4, (1, 0)), (99, (20, 20)),
                                            (True, (2, 1)), (float('inf'), (2, 1))])
def test_common_range_normalization(tmp_path, value, expected):
    config = ConfigManager(tmp_path / 'new.json')
    config.load()
    config.apply({'viewer_prefetch_forward_screens': value, 'viewer_prefetch_backward_screens': value})
    assert (config.get('viewer_prefetch_forward_screens'), config.get('viewer_prefetch_backward_screens')) == expected


def test_common_ui_pdf_toggle_save_cancel_reset(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'new.json')
    config.load()
    dialog = SettingsDialog(config)
    try:
        assert [g.title() for g in dialog.findChildren(QGroupBox)].count('先読み設定') == 1
        assert dialog.viewer_pdf_prefetch_individual_checkbox.isChecked()
        dialog.viewer_prefetch_backward_spin.setValue(20)
        dialog.viewer_pdf_prefetch_forward_spin.setValue(7)
        dialog.viewer_pdf_prefetch_individual_checkbox.setChecked(False)
        assert not dialog.viewer_pdf_prefetch_forward_spin.isEnabled()
        dialog.apply_settings()
        loaded = ConfigManager(config.path).load()
        assert loaded['viewer_prefetch_backward_screens'] == 20
        assert loaded['viewer_pdf_prefetch_forward_screens'] == 7
        assert loaded['viewer_pdf_prefetch_individual'] is False
        dialog._reset_viewer_scope()
        assert dialog.viewer_prefetch_backward_spin.value() == 1
        assert dialog.viewer_pdf_prefetch_individual_checkbox.isChecked()
        assert dialog.viewer_pdf_prefetch_forward_spin.value() == 1
        assert dialog.viewer_pdf_prefetch_backward_spin.value() == 0
        dialog.reject()
        assert config.get('viewer_pdf_prefetch_forward_screens') == 7
    finally:
        dialog.close()
        qapp.processEvents()


@pytest.mark.parametrize('extension', ['rar', '7z'])
def test_archive_common_priority_applies_live_without_ring(tmp_path, qapp, extension):
    # The backend is deterministic and in-process; no external extractor runs.
    backend = Backend(pages=48)
    with viewer_fixture(tmp_path, qapp, backend, name=f'book.{extension}') as window:
        source = window.book_session.source
        window.set_view_mode('single')
        window.model.go_to_index(24)
        window._refresh_view()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 24)
        for forward, backward, direction in ((1, 0, 1), (20, 20, 1), (2, 1, -1)):
            window.config.apply({'viewer_prefetch_forward_screens': forward,
                                 'viewer_prefetch_backward_screens': backward})
            live = window.book_session.viewer_runtime._current_request
            assert len(live.next_display_units) == forward + backward
            assert live.reverse_priority_screens == backward
            window.presentation_state._direction = direction
            request = window._zip_runtime_request(window.model.spread_at())
            actual = [u.start_index for u in request.next_display_units]
            assert actual == ([24 + direction * i for i in range(1, forward + 1)]
                              + [24 - direction * i for i in range(1, backward + 1)])
            assert all(request.warmup_plan.unit_for_identity(u.identity) is not None
                       for u in request.next_display_units)
            assert not hasattr(source, '_zip_read_ahead')


def _advance_far(window, qapp):
    assert window.image_cache.wait_for_done(5000)
    qapp.processEvents()
    window._pdf_far_timer.stop()
    window._advance_pdf_far_prefetch()
    assert window.image_cache.wait_for_done(5000)
    qapp.processEvents()
    window._pdf_far_timer.stop()


@pytest.mark.parametrize('mode, individual', [('single', True), ('spread', True), ('single', False), ('spread', False)])
def test_pdf_individual_bounds_or_common_paced_far(tmp_path, qapp, mode, individual):
    window, session, service, backend = _open_many_page_pdf_window(tmp_path, qapp, settings={
        'view_mode': mode, 'single_first_page': False, 'reading_direction': 'ltr',
        'viewer_pdf_prefetch_individual': individual, 'viewer_memory_mode': '256',
    })
    try:
        multiplier = 2 if mode == 'spread' else 1
        assert [r.page_index for r in backend.rendered] == list(range(multiplier * (2 if individual else 3)))
        before = len(backend.rendered)
        generation = window.image_cache.generation
        _advance_far(window, qapp)
        if individual:
            assert len(backend.rendered) == before
        else:
            assert len(backend.rendered) - before == multiplier
            assert all(r.priority == 1_000_000 for r in backend.rendered[before:])
            assert window.image_cache.generation == generation
            for _ in range(2):
                _advance_far(window, qapp)
            indexes = [r.page_index for r in backend.rendered]
            assert len(indexes) == len(set(indexes))
            assert indexes == list(range(before + multiplier * 3))
            # Switch individual ON while the same source remains open.
            window.config.apply({'viewer_pdf_prefetch_individual': True})
            _advance_far(window, qapp)
            assert window._pdf_far_units is None
    finally:
        _close_measured_pdf_window(window, session, service, qapp)


@pytest.mark.parametrize('action', ['navigate', 'close_book'])
def test_pdf_paced_far_cancel_preserves_current_and_source_lifetime(tmp_path, qapp, action):
    window, session, service, backend = _open_many_page_pdf_window(tmp_path, qapp, settings={
        'view_mode': 'single', 'viewer_pdf_prefetch_individual': False,
    })
    entered, release = Event(), Event()
    original = backend.render_page
    old_source = session.source
    delivered = []
    window.image_cache.pageLoaded.connect(lambda image: delivered.append(image.page_index))

    def blocked(request, *, cancel_token=None):
        if request.page_index == 3:
            entered.set()
            assert release.wait(3)
        return original(request, cancel_token=cancel_token)

    backend.render_page = blocked
    try:
        window._pdf_far_timer.stop()
        window._advance_pdf_far_prefetch()
        assert entered.wait(2)
        # Only one distant display unit is admitted while native render blocks.
        assert [key[1] for key in window.image_cache._tasks] == [3]
        if action == 'navigate':
            window.model.go_to_index(10)
            window._refresh_view()
            window._pdf_prefetch_timer.stop()
            assert not window._pdf_far_timer.isActive()
            assert window._pdf_far_units is None
        else:
            session.close_book()
            assert not old_source._closed.is_set()
        release.set()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert 3 not in delivered
        assert not window.image_cache.contains(3)
        if action == 'navigate':
            assert 10 in delivered
            assert window.image_cache.contains(10)
            assert [r.page_index for r in backend.rendered].count(3) == 1
        else:
            assert service.flush(wait_seconds=2)
            qapp.processEvents()
            assert window.image_cache.source is None
    finally:
        release.set()
        _close_measured_pdf_window(window, session, service, qapp)


def test_pdf_far_stops_before_budget_or_generation_and_reverse_priority(tmp_path, qapp):
    window, session, service, backend = _open_many_page_pdf_window(tmp_path, qapp, settings={
        'view_mode': 'single', 'viewer_pdf_prefetch_individual': False,
    })
    try:
        before = len(backend.rendered)
        window.viewer_cache_budget_bytes = window.image_cache.cache_bytes
        _advance_far(window, qapp)
        assert len(backend.rendered) == before
        assert window._pdf_far_units is None
        window.config.apply({'viewer_memory_mode': '256'})
        window.model.go_to_index(15)
        window._refresh_view()
        window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        window.model.go_to_index(14)
        window._refresh_view()
        window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        window._pdf_prefetch_timer.stop()
        assert window._pdf_prefetch_direction == -1
        assert window._configured_prefetch_units(14, (14,), forward_units=2,
            backward_units=1, direction=-1) == ((13,), (15,), (12,))
        window._start_deferred_pdf_prefetch()
        window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        before = len(backend.rendered)
        window.image_cache.generation += 1
        _advance_far(window, qapp)
        assert len(backend.rendered) == before
        assert window._pdf_far_units is None
    finally:
        _close_measured_pdf_window(window, session, service, qapp)


def test_pdf_common_retention_uses_memory_budget_across_navigation(tmp_path, qapp):
    window, session, service, backend = _open_many_page_pdf_window(tmp_path, qapp, settings={
        'view_mode': 'single', 'viewer_pdf_prefetch_individual': False,
        'cache_size': 2, 'viewer_memory_mode': '256',
    })
    try:
        for _ in range(12):
            _advance_far(window, qapp)
        retained = set(window.image_cache._cache)
        assert len(retained) == 15
        window.model.go_to_index(7)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert retained.issubset(window.image_cache._cache)
        assert window.image_cache.cache_bytes <= window.viewer_cache_budget_bytes
    finally:
        _close_measured_pdf_window(window, session, service, qapp)
