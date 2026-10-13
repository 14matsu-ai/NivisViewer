from app.raster_adaptive_policy import RasterAdaptivePolicy, RasterResourceSample
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET
from app.zip_raster_book_runtime import ZipRasterBookRuntime
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from tests.test_full_prefetch_parallel import known_unit
from tests.test_viewer_parallel_trials import GateSource, close_runtime
from tests.test_zip_raster_book_runtime import _request, _write_zip, _wait_until
import pytest
from threading import Event


HEALTHY = RasterResourceSample(.2, 6 * 1024**3, 8 * 1024**3)


def test_auto_growth_backoff_and_comparable_decode_feedback():
    policy = RasterAdaptivePolicy(8, logical_cpus=16)
    policy.normal = 2
    policy.update(HEALTHY, background_owned=2, now=1)
    policy.update(HEALTHY, background_owned=2, now=1.5)
    assert policy.normal == 3
    policy.observe(('webp', 24), .1, 2, now=2)
    policy.observe(('png', 26), .8, 5, now=2.1)
    assert policy.normal == 3  # Different cost classes are not compared.
    policy.observe(('webp', 24), .2, 5, now=2.2)
    assert policy.normal == 2 and policy.reason == 'decode_slowdown'
    policy.update(HEALTHY, background_owned=2, now=3)
    assert policy.normal == 2  # Cooldown, not immediate oscillation.
    policy.update(RasterResourceSample(.95, HEALTHY.available_bytes, HEALTHY.total_bytes), background_owned=2, now=4)
    assert policy.normal == 1
    policy.update(RasterResourceSample(.1, 128 * 1024**2, HEALTHY.total_bytes), background_owned=1, now=5)
    assert policy.reason == 'memory_pressure'
    assert not policy.memory_admits(256 * 1024**2, 0)


def test_current_ready_restores_healthy_capacity_but_not_recent_slowdown():
    policy = RasterAdaptivePolicy(8, logical_cpus=16)
    policy.sample = HEALTHY
    policy.current_pending(now=0)
    assert policy.normal == 2
    policy.current_pending(now=.1)
    policy.current_ready(now=.2)
    assert policy.normal == 4
    policy.sample = RasterResourceSample(.75, HEALTHY.available_bytes, HEALTHY.total_bytes)
    policy.current_pending(now=.3)
    policy.current_ready(now=.4)
    assert policy.normal == 4  # Moderate load holds an established cap, without growing it.
    policy.observe(('webp', 24), .1, 2, now=1)
    policy.observe(('webp', 24), .3, 4, now=2)
    policy.current_pending(now=2.1)
    policy.current_ready(now=2.2)
    assert policy.normal <= 2


def test_low_core_or_unavailable_resources_remain_bounded():
    policy = RasterAdaptivePolicy(12, logical_cpus=2)
    for now in range(20):
        policy.update(HEALTHY, background_owned=1, now=now)
    assert policy.normal == 1
    policy = RasterAdaptivePolicy(12, logical_cpus=16)
    policy.update(None, background_owned=4, now=1)
    assert policy.normal <= 2
    assert policy.memory_admits(32 * 1024**2, 0)
    assert not policy.memory_admits(32 * 1024**2, 32 * 1024**2)


@pytest.mark.parametrize('maximum', [4, 8, 12])
def test_auto_shared_permits_and_latest_current_without_new_prefetch(tmp_path, qapp, monkeypatch, maximum):
    budget = RASTER_EXECUTION_BUDGET
    budget.configure(4)
    monkeypatch.setattr(budget._resource_probe, 'sample', lambda: HEALTHY)
    budget._adaptive_policy = RasterAdaptivePolicy(maximum, logical_cpus=16)
    normal = maximum - 2
    current_pair = (normal + 4, normal + 5)
    latest = normal + 2
    page_count = normal + 6
    source = GateSource(_write_zip(tmp_path, pages=page_count))
    source.entered.update({f'{i}.png': Event() for i in range(8, page_count)})
    source.release.update({f'{i}.png': Event() for i in range(8, page_count)})
    runtime = ZipRasterBookRuntime(source, 1, current_extra=True, spread_parallel=True,
        near_prefetch_parallel=True, prefetch_execution_mode='auto', adaptive_maximum=maximum)
    units = [known_unit(i) for i in range(page_count)]
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        source.block = {f'{i}.png' for i in range(1, page_count)}
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, lambda: all(source.entered[f'{i}.png'].is_set() for i in range(1, min(4, normal) + 1)))
        import app.raster_adaptive_policy as policy_module
        clock = [10.]
        monkeypatch.setattr(policy_module, 'monotonic', lambda: clock[0])
        budget._adaptive_policy.cooldown_until = 0
        for tick in (10. + i * .5 for i in range(max(0, normal - 4) * 2)):
            clock[0] = tick
            budget._sample_adaptive()
            qapp.processEvents()
        _wait_until(qapp, lambda: all(source.entered[f'{i}.png'].is_set() for i in range(1, normal + 1)))
        assert budget._adaptive_policy.normal == normal
        assert budget.debug_values()['owned_permits'] == normal
        runtime.request(_request(2, known_unit(*current_pair)), preserve_started_compatible=True)
        assert all(source.entered[f'{i}.png'].wait(1) for i in current_pair)
        assert budget.debug_values()['owned_permits'] == maximum
        assert budget.adaptive_pause_prefetch()
        assert not budget.background_available()
        assert not source.entered[f'{normal + 1}.png'].is_set()
        runtime.request(_request(3, units[latest]))
        assert not source.entered[f'{latest}.png'].is_set()
        assert budget.debug_values()['owned_permits'] == maximum
        for i in current_pair:
            source.release[f'{i}.png'].set()
        _wait_until(qapp, source.entered[f'{latest}.png'].is_set)
        source.release[f'{latest}.png'].set()
        _wait_until(qapp, lambda: any(frame.request_id == 3 for frame in frames))
        assert all(frame.request_id != 2 for frame in frames)
        assert source.calls[f'{latest}.png'] == 1
    finally:
        close_runtime(runtime, source, qapp)
        assert budget.debug_values()['adaptive_reserved_scratch_bytes'] == 0
        assert not budget._adaptive_timer.isActive()
        budget.configure(3)


def test_memory_recovery_resumes_prefetch_without_new_input(tmp_path, qapp, monkeypatch):
    budget = RASTER_EXECUTION_BUDGET
    sample = [RasterResourceSample(.2, 128 * 1024**2, 8 * 1024**3)]
    monkeypatch.setattr(budget._resource_probe, 'sample', lambda: sample[0])
    source = GateSource(_write_zip(tmp_path, pages=3))
    runtime = ZipRasterBookRuntime(source, 1, prefetch_execution_mode='auto')
    frames = []; runtime.frameReady.connect(frames.append)
    units = [known_unit(i) for i in range(3)]
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        source.block = {'1.png', '2.png'}
        runtime.release_continuous_warmup(request_id=1)
        qapp.processEvents()
        assert not source.entered['1.png'].is_set()
        sample[0] = HEALTHY
        budget._sample_adaptive()
        _wait_until(qapp, source.entered['1.png'].is_set)
    finally:
        close_runtime(runtime, source, qapp)


def test_auto_memory_veto_and_manual_owner_keep_stricter_cap(tmp_path, qapp, monkeypatch):
    budget = RASTER_EXECUTION_BUDGET
    monkeypatch.setattr(budget._resource_probe, 'sample', lambda: HEALTHY)
    source = GateSource(_write_zip(tmp_path, pages=3))
    runtime = ZipRasterBookRuntime(source, 1, prefetch_execution_mode='auto')
    class Owner: pass
    fixed = Owner()
    try:
        budget._adaptive_policy.normal = 6
        budget.configure(3, owner=fixed)
        assert budget.limit == 3
        assert not budget._emergency_enabled()
        object_job = object()
        assert not budget.reserve_auto_memory(object_job, 8 * 1024**3)
        assert budget.debug_values()['adaptive_reserved_scratch_bytes'] == 0
        assert budget.reserve_auto_memory(object_job, 1024)
        budget.release_auto_memory(object_job)
    finally:
        budget.release_owner(fixed)
        close_runtime(runtime, source, qapp)


@pytest.mark.parametrize('maximum,expected', [(2, 4), (99, 12), ('bad', 8), (True, 8)])
def test_auto_settings_normalize(maximum, expected, tmp_path):
    config = ConfigManager(tmp_path / 'config.json'); config.load()
    config.apply({'viewer_prefetch_execution_mode': 'auto', 'viewer_adaptive_maximum': maximum})
    assert config.get('viewer_prefetch_execution_mode') == 'auto'
    assert config.get('viewer_adaptive_maximum') == expected


def test_auto_settings_save_cancel_reset_and_manual_cap_separate(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json'); config.load()
    dialog = SettingsDialog(config)
    try:
        dialog._select_data(dialog.viewer_prefetch_execution_mode_combo, 'auto')
        dialog.viewer_adaptive_maximum_spin.setValue(10)
        assert not dialog.viewer_raster_execution_limit_combo.isEnabled()
        assert dialog.viewer_adaptive_maximum_spin.isEnabled()
        values = dialog.values()
        config.apply({'viewer_prefetch_execution_mode': values['viewer_prefetch_execution_mode'],
                      'viewer_adaptive_maximum': values['viewer_adaptive_maximum']}, save=True)
        assert ConfigManager(config.path).load()['viewer_adaptive_maximum'] == 10
        dialog._reset_viewer_scope()
        assert dialog.viewer_adaptive_maximum_spin.value() == 8
        assert dialog.viewer_prefetch_execution_mode_combo.currentData() == 'reserve'
        assert config.get('viewer_prefetch_execution_mode') == 'auto'
        dialog.reject()
        assert config.get('viewer_adaptive_maximum') == 10
    finally:
        dialog.close()


def test_auto_window_scope_and_maximum_apply_to_next_book(tmp_path, qapp):
    from PIL import Image
    from app.viewer_window import ViewerWindow
    config = ConfigManager(tmp_path / 'config.json'); config.load()
    config.apply({'viewer_prefetch_execution_mode': 'auto', 'viewer_adaptive_maximum': 8})
    with Image.new('RGB', (120, 180), (40, 80, 120)) as image:
        image.save(tmp_path / '画像.jpg')
    window = ViewerWindow(config_manager=config)
    budget = RASTER_EXECUTION_BUDGET
    try:
        assert window not in budget._limits  # An empty Auto Viewer is not a fixed-cap owner.
        assert window.open_path(tmp_path / '画像.jpg')
        _wait_until(qapp, lambda: window.book_session.viewer_runtime is not None)
        original = window.book_session.viewer_runtime
        assert original._adaptive and original._adaptive_maximum == 8
        assert window not in budget._limits
        config.apply({'viewer_prefetch_execution_mode': 'burst', 'viewer_adaptive_maximum': 12})
        window._load_prefetch_settings()
        assert window.book_session.viewer_runtime is original
        assert original._adaptive_maximum == 8
        assert window not in budget._limits
        assert window.open_path(tmp_path / '画像.jpg')
        _wait_until(qapp, lambda: window.book_session.viewer_runtime is not original)
        assert not window.book_session.viewer_runtime._adaptive
        assert window in budget._limits
    finally:
        window.prepare_shutdown(wait_msecs=10000); window.close(); qapp.processEvents()
        assert not budget._auto_pending


def test_feedback_failure_does_not_strand_frame_or_execution_resources(tmp_path, qapp, monkeypatch):
    budget = RASTER_EXECUTION_BUDGET
    monkeypatch.setattr(budget._resource_probe, 'sample', lambda: HEALTHY)
    def failed_feedback(*args):
        raise RuntimeError('unavailable telemetry')
    monkeypatch.setattr(budget, 'observe_auto_job', failed_feedback)
    source = GateSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(source, 1, prefetch_execution_mode='auto')
    frames = []; runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, known_unit(0)))
        _wait_until(qapp, lambda: bool(frames))
        assert frames[-1].pages[0].error is None
        assert not budget._auto_pending
    finally:
        close_runtime(runtime, source, qapp)
        assert budget.debug_values()['adaptive_reserved_scratch_bytes'] == 0
