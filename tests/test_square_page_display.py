import zipfile
from contextlib import ExitStack

from PIL import Image
import pytest

from app.config_manager import ConfigManager
from app.page_model import PageModel
from app.settings_dialog import SettingsDialog
from app.viewer_window import ViewerWindow
from tests.test_page_model import LazyMemoryImageSource, make_model, logical_indexes
from tests.test_sprint18_navigation_followup import _wait_for_applied_display


@pytest.mark.parametrize("size,expected", [
    ((1000, 1000), True), ((900, 1000), True), ((1000, 900), True),
    ((899, 1000), False), ((1000, 899), False), ((800, 1200), False),
    ((1800, 900), False), ((0, 1000), False), ((1000, 0), False),
])
def test_square_tolerance_is_symmetric_and_inclusive(size, expected):
    model = make_model([size, (800, 1200)], single_first_page=False)
    model.update_options(treat_wide_image_as_single=False, treat_square_image_as_single=True)
    assert model.is_independent_image(0) is expected
    assert model.spread_at().is_single is expected


def test_square_metadata_reuses_incremental_topology_and_cover_slide_rules(monkeypatch):
    model = PageModel()
    source = LazyMemoryImageSource([(800, 1200)] * 4)
    def forbidden(*_args):
        pytest.fail("Shape checks must not read or decode images on the GUI path")
    monkeypatch.setattr(source, "open_image", forbidden)
    monkeypatch.setattr(source, "logical_size", forbidden)
    model.update_options(single_first_page=False, treat_wide_image_as_single=False,
                         treat_square_image_as_single=True)
    model.set_source(source)
    assert logical_indexes(model) == [0, 1]
    model.set_image_sizes(((0, (800, 1200)), (1, (950, 1000)),
                           (2, (800, 1200)), (3, (800, 1200))))
    assert [model.display_unit_start_at_ordinal(i) for i in range(model.display_unit_count)] == [0, 1, 2]
    assert logical_indexes(model) == [0]
    model.next()
    assert logical_indexes(model) == [1]
    model.next()
    assert logical_indexes(model) == [2, 3]
    model.previous()
    assert logical_indexes(model) == [1]
    model.update_options(square_image_tolerance_percent=0)
    assert logical_indexes(model) == [0, 1]
    model.set_image_size(1, (1000, 1000))
    assert logical_indexes(model) == [1]
    model.update_options(single_first_page=True)
    model.first()
    model.next()
    model.previous_single()
    assert logical_indexes(model) == [0]  # A square partner still stays alone.


def test_square_and_wide_options_are_independent():
    model = make_model([(1000, 1000), (1600, 800)], single_first_page=False)
    assert logical_indexes(model) == [0]
    assert not model.is_independent_image(0)
    model.update_options(treat_wide_image_as_single=False)
    assert logical_indexes(model) == [0, 1]
    model.update_options(treat_square_image_as_single=True)
    assert logical_indexes(model) == [0]
    assert not model.is_independent_image(1)
    model.update_options(square_image_tolerance_percent=0)
    assert model.is_independent_image(0)


def test_square_settings_apply_reload_cancel_and_reset(tmp_path, qapp):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    dialog = SettingsDialog(config)
    try:
        assert not dialog.square_single_checkbox.isChecked()
        assert not dialog.square_tolerance_spin.isEnabled()
        assert dialog.square_tolerance_spin.value() == 10
        dialog.square_single_checkbox.setChecked(True)
        assert dialog.square_tolerance_spin.isEnabled()
        dialog.square_tolerance_spin.setValue(15)
        changed = dialog.apply_settings()
        assert changed["treat_square_image_as_single"] is True
        assert changed["square_image_tolerance_percent"] == 15
        assert ConfigManager(config.path).load()["square_image_tolerance_percent"] == 15
        dialog.square_tolerance_spin.setValue(3)
        dialog.reject()
        assert config.get("square_image_tolerance_percent") == 15
    finally:
        dialog.close()
    reset = SettingsDialog(config)
    try:
        assert reset.square_single_checkbox.isChecked()
        assert reset.square_tolerance_spin.value() == 15
        reset._reset_viewer_scope()
        assert not reset.square_single_checkbox.isChecked()
        assert not reset.square_tolerance_spin.isEnabled()
        assert reset.square_tolerance_spin.value() == 10
        reset.apply_settings()
        assert ConfigManager(config.path).load()["treat_square_image_as_single"] is False
    finally:
        reset.close()


@pytest.mark.parametrize("value,expected", [(None, 10), ("bad", 10), (-1, 0), (90, 30), (0, 0), (18, 18)])
def test_square_tolerance_config_normalizes(value, expected, tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"square_image_tolerance_percent": value})
    assert config.get("square_image_tolerance_percent") == expected


@pytest.mark.parametrize("kind", ["folder", "zip", "pdf"])
@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_viewer_square_option_uses_existing_runtime_and_live_layout(tmp_path, qapp, kind, direction):
    folder = tmp_path / "book"
    folder.mkdir()
    for i, size in enumerate([(800, 1200), (900, 1000), (800, 1200), (800, 1200)]):
        with Image.new("RGB", size, (50 + i * 20, 90, 130)) as image:
            image.save(folder / f"{i}.jpg")
    target = folder
    if kind == "zip":
        target = tmp_path / "book.cbz"
        with zipfile.ZipFile(target, "w") as archive:
            for page in sorted(folder.iterdir()):
                archive.write(page, page.name)
    elif kind == "pdf":
        target = tmp_path / "book.pdf"
        with ExitStack() as stack:
            images = [stack.enter_context(Image.open(page)) for page in sorted(folder.iterdir())]
            images[0].save(target, "PDF", save_all=True, append_images=images[1:])
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"view_mode": "spread", "reading_direction": direction,
                  "single_first_page": False, "treat_wide_image_as_single": False,
                  "treat_square_image_as_single": True})
    window = ViewerWindow(config_manager=config)
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    try:
        assert window.open_path(target)
        assert window.book_session.wait_for_async(3000)
        _wait_for_applied_display(window, qapp)
        assert window.viewer.displayed_page_indexes == (0,)
        runtime = window._zip_runtime
        if kind != "pdf":
            assert window._zip_runtime_request(window.model.spread_at()).resolve_layout_metadata
        window.next_page()
        _wait_for_applied_display(window, qapp)
        assert window.viewer.displayed_page_indexes == (1,)
        window.next_page()
        _wait_for_applied_display(window, qapp)
        assert sorted(window.viewer.displayed_page_indexes) == [2, 3]
        window.previous_page()
        _wait_for_applied_display(window, qapp)
        assert window.viewer.displayed_page_indexes == (1,)
        config.apply({"square_image_tolerance_percent": 0})
        _wait_for_applied_display(window, qapp)
        assert sorted(window.viewer.displayed_page_indexes) == [0, 1]
        assert window._zip_runtime is runtime
        assert window.square_single_action.isChecked()
        window.square_single_action.trigger()
        assert not window.model.treat_square_image_as_single
        assert config.get("treat_square_image_as_single") is False
    finally:
        window.close()
        qapp.processEvents()
