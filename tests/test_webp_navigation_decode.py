from threading import Event, Lock, get_ident
import zipfile

from PIL import Image
import pytest

from app import image_source as source_module
from app.image_source import FolderImageSource, ZipImageSource
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.zip_raster_book_runtime import ZipRasterBookRuntime, ZipRasterDisplayUnit, ZipRasterPage, ZipRasterRenderSpec
from app.viewer_render import qimage_to_pillow
from tests.test_zip_raster_book_runtime import _request, _wait_until


@pytest.mark.parametrize('orientation', range(1, 9))
@pytest.mark.parametrize('lossless', [True, False])
@pytest.mark.parametrize('size', [(128, 96), (96, 96), (96, 128)])
def test_folder_webp_header_has_logical_size_without_qt_or_pixel_decode(
    tmp_path, monkeypatch, orientation, lossless, size,
):
    from PIL import WebPImagePlugin

    path = tmp_path / '画像.webp'
    with Image.new('RGB', size, 'navy') as image:
        image.getexif()[274] = orientation
        image.save(path, 'WEBP', lossless=lossless, exif=image.getexif())
    source = FolderImageSource(tmp_path)
    original_open = Image.open
    formats = []

    def open_header(*args, **kwargs):
        formats.append(kwargs.get('formats'))
        return original_open(*args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('Folder WebP metadata must not enter Qt or decode pixels')

    monkeypatch.setattr(Image, 'open', open_header)
    monkeypatch.setattr(WebPImagePlugin.WebPImageFile, 'load', forbidden)
    monkeypatch.setattr(source_module, 'QImageReader', forbidden)
    try:
        expected = size[::-1] if orientation in {5, 6, 7, 8} else size
        assert source.probe_image_size(str(path)) == expected
        assert source.probe_image_size(str(path)) == expected
        assert formats == [('WEBP',)]  # The second lookup uses metadata cache.
    finally:
        source.close()


def test_folder_corrupt_webp_header_stays_on_explicit_decoder(tmp_path, monkeypatch):
    path = tmp_path / 'broken.webp'
    path.write_bytes(b'not a WebP image')
    source = FolderImageSource(tmp_path)
    original_open = Image.open
    formats = []

    def open_header(*args, **kwargs):
        formats.append(kwargs.get('formats'))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, 'open', open_header)
    try:
        assert source.probe_image_size(str(path)) is None
        assert formats == [('WEBP',)]
        assert str(path) not in source._size_cache
    finally:
        source.close()


def source_for(tmp_path, folder, image, *, lossless=True):
    path = tmp_path / '画像.webp'
    image.save(path, format='WEBP', lossless=lossless, exact=True, exif=image.getexif())
    if folder:
        source = FolderImageSource(tmp_path)
        return source, str(path), FolderRasterBookRuntime
    archive = tmp_path / 'book.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.write(path, path.name)
    return ZipImageSource(archive), path.name, ZipRasterBookRuntime


@pytest.mark.parametrize('folder', [True, False], ids=['folder', 'zip'])
@pytest.mark.parametrize(('orientation', 'algorithm'),
    [(value, 'nearest') for value in range(1, 9)] + [(1, value) for value in ('auto', 'fast', 'smooth', 'sharp', 'area')])
def test_static_webp_pixels_alpha_exif_and_preview_policy(tmp_path, qapp, monkeypatch, folder, orientation, algorithm):
    image = Image.new('RGBA', (96, 128))
    image.putdata([(x * 2, y, (x + y) % 256, (x * 3 + y) % 255 + 1)
                   for y in range(128) for x in range(96)])
    image.getexif()[274] = orientation
    source, image_id, runtime_type = source_for(tmp_path, folder, image)
    image.close()
    with source.open_image(image_id) as original:
        expected_size = original.size
        from app.viewer_render import ResamplingPolicy, pillow_resampling_for_policy
        target = (48, 64) if orientation < 5 else (64, 48)
        expected = original.resize(target, pillow_resampling_for_policy(ResamplingPolicy(algorithm), original.size, target))
    calls = []
    original_open = source.open_image
    monkeypatch.setattr(source, 'open_image', lambda identity: (calls.append(identity), original_open(identity))[1])
    decode_threads = []
    original_qt = source_module._read_webp_qimage
    def qt_decode(data):
        decode_threads.append(get_ident())
        return original_qt(data)
    monkeypatch.setattr(source_module, '_read_webp_qimage', qt_decode)
    runtime = runtime_type(source, 1)
    frames = []
    runtime.frameReady.connect(frames.append)
    unit = ZipRasterDisplayUnit(0, (ZipRasterPage(0, image_id, expected_size),), True)
    target = expected.size
    try:
        assert runtime.request(_request(1, unit, unit, spec=ZipRasterRenderSpec(
            target, decoder_maximum_size=target, downscale_algorithm=algorithm)))
        _wait_until(qapp, lambda: bool(frames))
        page = frames[-1].pages[0]
        assert page.error is None
        assert page.original_size == expected_size
        assert page.source_is_preview
        assert page.source_qimage.size().toTuple() == target
        with qimage_to_pillow(page.source_qimage) as actual:
            assert actual.convert('RGBA').tobytes() == expected.convert('RGBA').tobytes()
        assert len(calls) == (0 if orientation == 1 else 1)
        assert bool(decode_threads) == (orientation == 1)
        assert all(thread != get_ident() for thread in decode_threads)
    finally:
        expected.close()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


@pytest.mark.parametrize('folder', [True, False], ids=['folder', 'zip'])
@pytest.mark.parametrize('lossless', [True, False], ids=['lossless', 'lossy'])
def test_webp_preview_can_hydrate_full_source_for_zoom(tmp_path, qapp, folder, lossless):
    with Image.new('RGBA', (96, 128)) as image:
        image.putdata([(x * 2, y, (x + y) % 256, (x * 3 + y) % 255 + 1)
                       for y in range(128) for x in range(96)])
        source, image_id, runtime_type = source_for(tmp_path, folder, image, lossless=lossless)
    with source.open_image(image_id) as original:
        expected = original.convert('RGBA').tobytes()
    runtime = runtime_type(source, 1)
    frames = []
    runtime.frameReady.connect(frames.append)
    unit = ZipRasterDisplayUnit(0, (ZipRasterPage(0, image_id, (96, 128)),), True)
    try:
        assert runtime.request(_request(1, unit, unit, spec=ZipRasterRenderSpec(
            (48, 64), decoder_maximum_size=(48, 64))))
        _wait_until(qapp, lambda: bool(frames))
        assert frames[-1].pages[0].source_is_preview
        assert runtime.request(_request(2, unit, unit, spec=ZipRasterRenderSpec((96, 128))))
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        page = frames[-1].pages[0]
        assert not page.source_is_preview
        assert page.source_qimage.size().toTuple() == (96, 128)
        # Decoders may differ in color rounding on lossy WebP, but preserve
        # transparency and full-resolution hydration without preview upscale.
        with qimage_to_pillow(page.source_qimage) as actual:
            rgba = actual.convert('RGBA').tobytes()
            assert rgba[3::4] == expected[3::4]
            assert max(abs(a - b) for a, b in zip(rgba, expected)) <= 1
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


@pytest.mark.parametrize('folder', [True, False], ids=['folder', 'zip'])
def test_animated_webp_declines_static_decoder(tmp_path, qapp, folder):
    path = tmp_path / 'animation.webp'
    first = Image.new('RGBA', (96, 128), (40, 90, 150, 120))
    second = Image.new('RGBA', (96, 128), (220, 40, 60, 255))
    try:
        first.save(path, format='WEBP', save_all=True, append_images=[second], lossless=True, duration=30)
    finally:
        first.close(); second.close()
    if folder:
        source = FolderImageSource(tmp_path)
        image_id = str(path)
    else:
        archive = tmp_path / 'book.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.write(path, path.name)
        source = ZipImageSource(archive)
        image_id = path.name
    try:
        assert source.open_static_webp_qimage(image_id) is None
        with source.open_image(image_id) as image:
            assert image.size == (96, 128)
            assert image.getpixel((0, 0)) == (40, 90, 150, 120)
    finally:
        source.close()


@pytest.mark.parametrize('folder', [True, False], ids=['folder', 'zip'])
def test_qt_webp_failure_falls_back_to_pillow_without_terminal_error(tmp_path, qapp, monkeypatch, folder):
    with Image.new('RGB', (96, 128), (25, 80, 150)) as image:
        source, image_id, runtime_type = source_for(tmp_path, folder, image)
    monkeypatch.setattr(source_module, '_read_webp_qimage', lambda data: None)
    runtime = runtime_type(source, 1)
    frames = []
    runtime.frameReady.connect(frames.append)
    unit = ZipRasterDisplayUnit(0, (ZipRasterPage(0, image_id, (96, 128)),), True)
    try:
        assert runtime.request(_request(1, unit, unit, spec=ZipRasterRenderSpec((48, 64), decoder_maximum_size=(48, 64))))
        _wait_until(qapp, lambda: bool(frames))
        assert frames[-1].pages[0].error is None
        assert frames[-1].pages[0].source_qimage.size().toTuple() == (48, 64)
        assert runtime.metrics.terminal_errors == 0
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


@pytest.mark.parametrize('folder', [True, False], ids=['folder', 'zip'])
def test_cancelled_static_webp_decode_cannot_publish_after_runtime_cancel(tmp_path, qapp, monkeypatch, folder):
    with Image.new('RGB', (96, 128), (25, 80, 150)) as image:
        source, image_id, runtime_type = source_for(tmp_path, folder, image)
    entered, release = Event(), Event()
    original = source_module._read_static_webp_qimage
    def blocked(data):
        entered.set()
        assert release.wait(5)
        return original(data)
    monkeypatch.setattr(source_module, '_read_static_webp_qimage', blocked)
    runtime = runtime_type(source, 1)
    frames = []
    runtime.frameReady.connect(frames.append)
    unit = ZipRasterDisplayUnit(0, (ZipRasterPage(0, image_id, (96, 128)),), True)
    try:
        assert runtime.request(_request(1, unit, unit, spec=ZipRasterRenderSpec((48, 64), decoder_maximum_size=(48, 64))))
        _wait_until(qapp, entered.is_set)
        runtime.cancel(clear_artifacts=True)
        release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert not frames
        assert runtime.cached_unit_count == 0
    finally:
        release.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


@pytest.mark.parametrize('folder', [True, False], ids=['folder', 'zip'])
def test_shared_static_webp_cancel_does_not_cancel_other_viewer(tmp_path, qapp, monkeypatch, folder):
    with Image.new('RGB', (96, 128), (25, 80, 150)) as image:
        source, image_id, runtime_type = source_for(tmp_path, folder, image)
    both_entered, release, lock = Event(), Event(), Lock()
    started = 0
    original = source_module._read_static_webp_qimage
    def blocked(data):
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                both_entered.set()
        assert release.wait(5)
        return original(data)
    monkeypatch.setattr(source_module, '_read_static_webp_qimage', blocked)
    runtimes = [runtime_type(source, 1), runtime_type(source, 1)]
    frames = [[], []]
    unit = ZipRasterDisplayUnit(0, (ZipRasterPage(0, image_id, (96, 128)),), True)
    try:
        for index, runtime in enumerate(runtimes):
            runtime.frameReady.connect(frames[index].append)
            assert runtime.request(_request(1, unit, unit, spec=ZipRasterRenderSpec(
                (48, 64), decoder_maximum_size=(48, 64))))
        _wait_until(qapp, both_entered.is_set)
        runtimes[0].cancel(clear_artifacts=True)
        release.set()
        _wait_until(qapp, lambda: bool(frames[1]) and all(not r.has_unfinished_tasks() for r in runtimes))
        assert not frames[0]
        assert frames[1][-1].pages[0].error is None
    finally:
        release.set()
        for runtime in runtimes:
            assert runtime.shutdown(wait_msecs=3000)
        source.close()
