from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
from pathlib import Path
from threading import Event, current_thread
import zipfile

import pytest
from PySide6.QtCore import QSize
from app.vector_image_decoder import render_vector, VectorImageError, AI_ERROR
from app.image_source import create_image_source
from app.pdfium_service import PdfiumService
from app.pdfium_backend import PdfiumBackend
from app.thumbnail_provider import BrowserThumbnailProvider
from app.browser_model import BrowserItem, BrowserItemKind
from app.supported_formats import configure_vector_loading

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -20 100 50"><rect x="-10" y="-20" width="50" height="50" fill="red"/></svg>'
SVG_DTD = b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + SVG


def pdf_bytes(rotation=0):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 50] /Resources << >> /Contents 4 0 R >>',
               b'<< /Length 25 >>\nstream\n1 0 0 rg 0 0 50 50 re f\nendstream',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>']
    objects[2] = objects[2].replace(b'/Resources', f'/Rotate {rotation} /Resources'.encode())
    data = b'%PDF-1.4\n'; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()
    return data


def test_svg_target_transparency_negative_viewbox_and_worker(qapp):
    with ThreadPoolExecutor(1) as pool:
        image, logical = pool.submit(render_vector, SVG, '.svg', (800, 800)).result()
    assert logical == (100, 50)
    assert image.size() == QSize(800, 400)
    assert image.pixelColor(100, 100).red() == 255
    assert image.pixelColor(700, 100).alpha() == 0
    assert render_vector(SVG.replace(b'viewBox=', b'width="72pt" height="36pt" viewBox='), '.svg', probe=True) == (96, 48)


@pytest.mark.parametrize('content', [
    b'<image href="file:///C:/online/image.png"/>',
    b'<image xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://invalid/x"/>',
    b'<style>rect {fill:url(//server/share/a)}</style>',
    b'<style>@import "file:///x";</style>',
    b'<rect style="fill:u\\72l(file:///x)"/>',
    b'<g xml:base="file:///online/"/>', b'<script/>', b'<foreignObject/>',
    b'<animate attributeName="href" to="file:///x"/>',
])
def test_svg_external_dependencies_rejected_before_qt(content, monkeypatch, qapp):
    import app.vector_image_decoder as decoder
    monkeypatch.setattr(decoder, 'QSvgRenderer', lambda: pytest.fail('Qt received unsafe XML'))
    with pytest.raises(VectorImageError):
        render_vector(b'<svg xmlns="http://www.w3.org/2000/svg">'+content+b'</svg>', '.svg')


def test_dtd_and_cancel_rejected(qapp):
    with pytest.raises(VectorImageError):
        render_vector(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///x">]><svg>&x;</svg>', '.svg')
    cancel = Event(); cancel.set()
    with pytest.raises(InterruptedError):
        render_vector(SVG, '.svg', cancel_token=cancel)


@pytest.mark.parametrize('declaration', [
    '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 20010904//EN" "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd">',
    '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">',
    '<!DOCTYPE svg SYSTEM "file:///nonexistent/external.dtd">',
])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_svg_doctype_ignored_before_qt(declaration, encoding, monkeypatch, qapp):
    import app.vector_image_decoder as decoder
    expected, logical = render_vector(SVG, '.svg', (200, 100))
    original = decoder.QSvgRenderer
    received = []
    class CheckedRenderer(original):
        def load(self, data):
            received.append(bytes(data))
            assert b'<!DOCTYPE' not in bytes(data)
            assert b'external.dtd' not in bytes(data)
            return super().load(data)
    monkeypatch.setattr(decoder, 'QSvgRenderer', CheckedRenderer)
    payload = ('<?xml version="1.0" encoding="' + encoding + '"?>' + declaration + SVG.decode()).encode(encoding)
    image, actual_logical = render_vector(payload, '.svg', (200, 100))
    assert received and image == expected and actual_logical == logical


@pytest.mark.parametrize('declaration', [
    b'<!DOCTYPE svg [<!ENTITY x "expanded">]>',
    b'<!DOCTYPE svg [<!ENTITY % x SYSTEM "file:///external.dtd">%x;]>',
    b'<!DOCTYPE svg [<!ATTLIST svg width CDATA "100">]>',
])
def test_svg_internal_subset_rejected_before_qt(declaration, monkeypatch, qapp):
    import app.vector_image_decoder as decoder
    monkeypatch.setattr(decoder, 'QSvgRenderer', lambda: pytest.fail('Qt received a DTD'))
    with pytest.raises(VectorImageError):
        render_vector(declaration + SVG, '.svg')


def test_ai_first_page_owner_thread_and_failure_cleanup(qapp):
    class Tracked(PdfiumBackend):
        def render_ai(self, *args, **kwargs):
            assert current_thread().name == 'NivisViewer-Pdfium'
            return super().render_ai(*args, **kwargs)
    service = PdfiumService(backend=Tracked())
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: render_vector(pdf_bytes(), '.ai', (800, 800), service=service), range(2)))
        for image, logical in results:
            assert logical == (133, 67)
            assert image.width() > image.height()
            assert image.pixelColor(10, 10).red() == 255
            assert image.pixelColor(image.width()-10, 10).alpha() == 0
        for bad in (b'%!PS-Adobe', b'%PDF-1.7 invalid'):
            with pytest.raises(VectorImageError, match='PDF'):
                render_vector(bad, '.ai', service=service)
        assert not service.backend._documents
        assert render_vector(pdf_bytes(), '.ai', probe=True, service=service) == (133, 67)
        rotated, logical = render_vector(pdf_bytes(90), '.ai', (300,300), service=service)
        assert logical == (67,133) and rotated.height() > rotated.width()
    finally:
        assert service.shutdown()


@pytest.mark.parametrize('suffix,payload', [('.svg',SVG),('.svg',SVG_DTD),('.ai',pdf_bytes())], ids=['svg','svg-doctype','ai'])
def test_folder_zip_thumbnail_and_original_identity(tmp_path, qapp, suffix, payload):
    path = tmp_path / ('合成'+suffix); path.write_bytes(payload)
    archive = tmp_path / '合成.zip'
    with zipfile.ZipFile(archive,'w') as z: z.writestr('画像'+suffix,payload)
    service = PdfiumService()
    try:
        for input_path,kind in [(path,BrowserItemKind.IMAGE),(archive,BrowserItemKind.ARCHIVE)]:
            source, selected = create_image_source(input_path,pdfium_service=service)
            try:
                assert len(source.list_images()) == 1
                image_id = source.list_images()[0]
                assert image_id.endswith(suffix)
                image, logical = source.render_vector(image_id,(600,600))
                assert not image.isNull()
                fork = source.fork_for_thumbnail()
                try: assert not fork.render_vector(image_id,(64,64))[0].isNull()
                finally: fork.close()
                item = BrowserItem(path=input_path,display_name=input_path.name,kind=kind,modified_at=None)
                result = BrowserThumbnailProvider.load_thumbnail_result(item,128,pdfium_service=service)
                assert result.image is not None and not result.image.isNull()
                assert max(result.image.width(),result.image.height()) <= 256
            finally:
                source.close()
    finally:
        assert service.shutdown()


def test_disabled_and_online_only_never_read(tmp_path, monkeypatch, qapp):
    import app.vector_image_decoder as decoder
    path=tmp_path/'image.svg'; path.write_bytes(SVG)
    configure_vector_loading(svg=False)
    try:
        monkeypatch.setattr(decoder,'read_vector',lambda *a: pytest.fail('disabled source read'))
        with pytest.raises(VectorImageError): render_vector(path,'.svg')
    finally:
        configure_vector_loading()
    monkeypatch.undo()
    def blocked(*args): raise OSError('online only')
    monkeypatch.setattr(decoder,'require_local',blocked)
    monkeypatch.setattr(decoder,'QSvgRenderer',lambda: pytest.fail('online file rendered'))
    with pytest.raises(OSError): render_vector(path,'.svg')


def test_runtime_rerenders_for_zoom_dpi_rotation_and_lens(tmp_path,qapp):
    from app.folder_raster_book_runtime import FolderRasterBookRuntime
    from app.raster_book_runtime import RasterRenderSpec
    from tests.test_folder_raster_book_runtime import _request, _unit, _wait_until
    path=tmp_path/'image.svg'; path.write_bytes(SVG)
    source,_=create_image_source(path)
    unit=_unit(source,0)
    runtime=FolderRasterBookRuntime(source,1)
    frames=[]; runtime.frameReady.connect(frames.append)
    try:
        specs=[RasterRenderSpec((400,300)), RasterRenderSpec((400,300),device_pixel_ratio=2),
               RasterRenderSpec((400,300),fit_mode='manual_zoom',manual_zoom=12),
               RasterRenderSpec((400,300),rotation=90),
               RasterRenderSpec((400,300),vector_minimum_size=(1600,800))]
        for i,spec in enumerate(specs,1):
            assert runtime.request(_request(i,unit,spec=spec))
            _wait_until(qapp,lambda: len(frames)>=i)
            assert all(page.error is None for page in frames[-1].pages)
        sizes=[source.key.pixel_size for source in runtime._source_store._sources.values()]
        assert any(w>=1600 and h>=800 for w,h in sizes)
    finally:
        assert runtime.shutdown(wait_msecs=5000)
        source.close()


@pytest.mark.parametrize('direction,rotation', [('ltr',0),('rtl',90)])
@pytest.mark.parametrize('suffix,payload', [('.svg',SVG),('.ai',pdf_bytes())], ids=['svg','ai'])
def test_actual_viewer_spread_rotation_lens_and_book_switch(tmp_path,qapp,direction,rotation,suffix,payload,monkeypatch):
    from app.config_manager import ConfigManager
    from app.viewer_window import ViewerWindow
    from PySide6.QtGui import QPixmap
    from tests.test_folder_raster_book_runtime import _wait_until
    archive=tmp_path/'book.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for i in range(3): z.writestr(f'{i}{suffix}',payload)
    config=ConfigManager(tmp_path/'config.json');config.load()
    config.apply({'view_mode':'spread','single_first_page':False,'reading_direction':direction,
                  'show_page_list':False,'magnifier_zoom':2.0,'treat_wide_image_as_single':False})
    service=PdfiumService()
    window=ViewerWindow(config_manager=config,pdfium_service=service)
    window.resize(800,600);window.show()
    try:
        assert window.open_path(archive)
        _wait_until(qapp,lambda: len(window.viewer._images)==2 and all(i.qimage is not None for i in window.viewer._images),timeout_ms=10000)
        assert all(i.is_vector and i.error is None for i in window.viewer._images)
        exported=[]
        monkeypatch.setattr(window, '_request_path_probe', lambda path,purpose: exported.append((Path(path),purpose)))
        window.open_current_with_application_picker()
        _wait_until(qapp,lambda:bool(exported))
        assert exported[0][0].suffix == suffix and exported[0][0].read_bytes() == payload
        assert exported[0][1] == 'open_with'
        if rotation:
            previous=window.presentation_state.committed_frame_serial
            window.rotate_right()
            _wait_until(qapp,lambda: window.presentation_state.committed_frame_serial>previous)
        pm=QPixmap(window.viewer.size());window.viewer.render(pm)
        widget=window.viewer
        before=[i.qimage.size() for i in widget._images]
        assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
        _wait_until(qapp,lambda: widget.magnifier_active,timeout_ms=10000)
        _wait_until(qapp,lambda: not window.book_session.viewer_runtime.has_unfinished_tasks(),timeout_ms=10000)
        assert widget.magnifier_active
        assert any(i.qimage.width()>size.width() or i.qimage.height()>size.height() for i,size in zip(widget._images,before))
        widget.cancel_magnifier()
        next_path=tmp_path/'new.svg';next_path.write_bytes(SVG)
        assert window.open_path(next_path)
        _wait_until(qapp,lambda: window.book_session.source is not None and window.book_session.source.source_path==tmp_path and any('new.svg' in i.image_id for i in widget._images),timeout_ms=10000)
        assert all(not i.image_id.endswith(suffix) or 'new.svg' in i.image_id for i in widget._images)
    finally:
        window.prepare_shutdown(wait_msecs=5000);window.close();qapp.processEvents()
        assert service.shutdown()


def test_settings_persist_and_help(tmp_path,qapp):
    from app.config_manager import ConfigManager
    from app.settings_dialog import SettingsDialog
    config=ConfigManager(tmp_path/'settings.json');config.load()
    dialog=SettingsDialog(config)
    try:
        assert dialog.ai_loading_checkbox.isChecked() and dialog.svg_loading_checkbox.isChecked()
        assert 'PDF' in dialog.ai_loading_help_button.toolTip()
        assert dialog.svg_loading_help_button.toolTip()
        dialog.ai_loading_checkbox.setChecked(False)
        values=dialog.apply_settings()
        assert values['ai_loading_enabled'] is False and config.get('svg_loading_enabled') is True
    finally:
        dialog.reject()


@pytest.mark.parametrize('suffix,payload', [('.svg',SVG),('.ai',pdf_bytes())], ids=['svg','ai'])
def test_page_list_renders_at_thumbnail_size(tmp_path,qapp,suffix,payload):
    from app.viewer_page_list_runtime import ViewerPageListRuntime
    from tests.test_viewer_page_list_runtime import _thumbnail_spec, _wait_until
    path=tmp_path/('image'+suffix);path.write_bytes(payload)
    service=PdfiumService();source,_=create_image_source(path,pdfium_service=service)
    runtime=ViewerPageListRuntime(source,source.list_images(),1)
    delivered=[];runtime.thumbnailReady.connect(delivered.append)
    try:
        runtime.set_visible(True)
        assert runtime.request_visible_pages((0,),_thumbnail_spec(192))
        _wait_until(qapp,lambda:bool(delivered))
        assert delivered[0].error is None
    finally:
        assert runtime.shutdown(wait_msecs=5000)
        source.close();assert service.shutdown()


def test_ai_cancel_retains_service_ownership_until_return(qapp):
    started=Event();release=Event();cancel=Event()
    class Blocked(PdfiumBackend):
        def render_ai(self,*args,**kwargs):
            result=super().render_ai(*args,**kwargs)
            started.set()
            assert release.wait(3)
            return result
    service=PdfiumService(backend=Blocked())
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(render_vector,pdf_bytes(),'.ai',(100,100),service=service,cancel_token=cancel)
            assert started.wait(2)
            cancel.set()
            assert not future.done()
            release.set()
            with pytest.raises(InterruptedError):future.result(timeout=3)
        assert service.maximum_concurrent_calls == 1
    finally:
        release.set();assert service.shutdown()


@pytest.mark.parametrize('size', [b'width="0" height="10"',b'width="NaN" height="1"',b'viewBox="0 0 -10 2"',b'width="0%" viewBox="0 0 10 2"'])
def test_invalid_svg_geometry(size,qapp):
    with pytest.raises(VectorImageError):render_vector(b'<svg xmlns="http://www.w3.org/2000/svg" '+size+b'/>','.svg')


def test_internal_gradient_and_embedded_raster(qapp):
    import base64
    from PIL import Image
    buf=io.BytesIO()
    with Image.new('RGBA',(2,2),'blue') as image:image.save(buf,format='PNG')
    data=(b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><defs><linearGradient id="g"><stop stop-color="red"/></linearGradient></defs><rect width="20" height="20" fill="url(#g)"/><image width="10" height="10" href="data:image/png;base64,'+base64.b64encode(buf.getvalue())+b'"/></svg>')
    image,_=render_vector(data,'.svg',(100,100))
    assert not image.isNull() and image.pixelColor(10,10).blue()>200


def test_svg_editor_metadata_preserves_pixels_and_external_reference_blocking(qapp, monkeypatch):
    import app.vector_image_decoder as decoder
    plain = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'
    annotated = b'''<svg xmlns="http://www.w3.org/2000/svg"
        xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
        xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
        width="20" height="20" inkscape:version="1.4">
        <metadata><rdf:RDF><rdf:Description rdf:about="https://example.invalid/license"/></rdf:RDF></metadata>
        <sodipodi:namedview inkscape:document-units="mm"/>
        <rect width="20" height="20" fill="red" inkscape:label="shape"/>
        </svg>'''
    reference, logical = render_vector(plain, '.svg', (40,40))
    original = decoder.QSvgRenderer
    received = []
    class CaptureRenderer(original):
        def load(self, data):
            received.append(bytes(data))
            return super().load(data)
    monkeypatch.setattr(decoder, 'QSvgRenderer', CaptureRenderer)
    result, actual = render_vector(annotated, '.svg', (40,40))
    assert actual == logical and result == reference
    assert received and all(word not in received[0] for word in (b'RDF', b'metadata', b'inkscape', b'sodipodi', b'example.invalid'))
    for unsafe in (b'<image href="file:///C:/online.png"/>',
                   b'<g xmlns="https://unknown.invalid/drawing"><rect/></g>',
                   b'<rect style="fill:url(https://example.invalid/image)"/>'):
        received.clear()
        with pytest.raises(VectorImageError):
            render_vector(annotated.replace(b'</svg>', unsafe+b'</svg>'), '.svg', (40,40))
        assert not received
