"""Bounded, self-contained static vector images. Native objects stay on workers."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import io
import math
from pathlib import Path
import re
import threading
from xml.parsers import expat
import xml.etree.ElementTree as ET

from PIL import Image
from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from .cloud_files import require_local
from .pdf_backend import is_cancelled
from .i18n import tr

VECTOR_SUFFIXES = frozenset({'.svg', '.ai'})
RENDERER_VERSION = 3
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
MAX_DIMENSION = 16384
AI_ERROR = 'PDF互換で保存されたAIに対応しています。非互換または破損したAIは表示できません。'
SVG_ERROR = '外部参照を含まない静止画SVGに対応しています。'
_context = ContextVar('vector_render_context', default=(None, None, 0))
_svg_slots = threading.BoundedSemaphore(2)


class VectorImageError(RuntimeError):
    pass


def check_cancel(token):
    if is_cancelled(token):
        raise InterruptedError('Vector request cancelled')


@contextmanager
def vector_context(service=None, cancel_token=None, priority=0):
    token = _context.set((service, cancel_token, priority))
    try:
        yield
    finally:
        _context.reset(token)


def read_vector(source, suffix):
    limit = MAX_SVG_BYTES if suffix == '.svg' else MAX_INPUT_BYTES
    if isinstance(source, (str, Path)):
        require_local(source)
        with open(source, 'rb') as stream:
            data = stream.read(limit + 1)
    else:
        if len(source) > limit:
            raise VectorImageError('Vector input exceeds limit')
        data = bytes(source)
    if not data or len(data) > limit:
        raise VectorImageError('Vector input exceeds limit')
    return data


def target_size(logical, bounds):
    w, h = logical
    if not all(math.isfinite(v) and 0 < v <= 1_000_000 for v in (w, h)):
        raise VectorImageError('Invalid vector dimensions')
    bw, bh = bounds or logical
    bw, bh = bw or w, bh or h
    if not all(math.isfinite(v) and v > 0 for v in (bw, bh)):
        raise VectorImageError('Invalid render dimensions')
    scale = min(bw / w, bh / h, MAX_DIMENSION / w, MAX_DIMENSION / h,
                math.sqrt(MAX_PIXELS / (w * h)))
    return max(1, math.floor(w * scale)), max(1, math.floor(h * scale))


def _reference(value):
    value = value.strip()
    if value.startswith('#') and len(value) > 1 and not any(c.isspace() for c in value):
        return
    if value.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
        try:
            payload = base64.b64decode(value.split(',', 1)[1], validate=True)
            if len(payload) > 4 * 1024 * 1024:
                raise ValueError()
            with Image.open(io.BytesIO(payload)) as im:
                if im.format not in {'PNG', 'JPEG'} or im.width * im.height > MAX_PIXELS:
                    raise ValueError()
                im.verify()
            return
        except Exception as exc:
            raise VectorImageError(tr(SVG_ERROR)) from exc
    raise VectorImageError(tr(SVG_ERROR))


def _css(value):
    # Reject escaping/comments instead of attempting to interpret a second CSS grammar.
    if any(c in value for c in ('\\', '/*', '*/', '@', '\x00')):
        raise VectorImageError(tr(SVG_ERROR))
    remaining = re.sub(r'url\s*\(\s*([\'"]?)(.*?)\1\s*\)',
                       lambda m: (_reference(m.group(2)) or ''), value, flags=re.I | re.S)
    if re.search(r'url\s*\(', remaining, re.I):
        raise VectorImageError(tr(SVG_ERROR))


def _svg_logical_size(root):
    box = root.get('viewBox')
    viewport = None
    if box is not None:
        try:
            values = [float(v) for v in re.split(r'[\s,]+', box.strip())]
            if len(values) != 4 or not all(math.isfinite(v) for v in values):
                raise ValueError()
            if values[2] <= 0 or values[3] <= 0:
                raise ValueError()
            viewport = values[2:]
        except ValueError as exc:
            raise VectorImageError('Invalid SVG viewBox') from exc
    sizes = []
    factors = {'':1, 'px':1, 'pt':96/72, 'pc':16, 'in':96, 'cm':96/2.54, 'mm':96/25.4}
    for axis, name in enumerate(('width', 'height')):
        value = root.get(name)
        if value is None or value.endswith('%'):
            if value is not None:
                try:
                    percent = float(value[:-1])
                except ValueError as exc:
                    raise VectorImageError('Invalid SVG dimensions') from exc
                if not math.isfinite(percent) or percent <= 0:
                    raise VectorImageError('Invalid SVG dimensions')
            if viewport is None:
                raise VectorImageError('SVG requires dimensions or viewBox')
            sizes.append(viewport[axis])
            continue
        match = re.fullmatch(r'\s*([+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(px|pt|pc|in|cm|mm)?\s*', value)
        if match is None:
            raise VectorImageError('Invalid SVG dimensions')
        sizes.append(float(match[1]) * factors[match[2] or ''])
    if not all(math.isfinite(v) and 0 < v <= 1_000_000 for v in sizes):
        raise VectorImageError('Invalid SVG dimensions')
    return tuple(max(1, round(v)) for v in sizes)


_SVG_NAMESPACE = 'http://www.w3.org/2000/svg'
_EDITOR_NAMESPACES = frozenset({
    'http://www.inkscape.org/namespaces/inkscape',
    'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd',
})


def _namespace(name):
    return name[1:].split('}', 1)[0] if name.startswith('{') else ''


def _remove_svg_metadata(node):
    # These subtrees/attributes describe the document/editor, not drawing.
    # Never send their content (including RDF resource URLs) to Qt.
    for key in tuple(node.attrib):
        if _namespace(key) in _EDITOR_NAMESPACES:
            del node.attrib[key]
    previous = None
    for child in tuple(node):
        namespace = _namespace(child.tag)
        metadata = (namespace in {'', _SVG_NAMESPACE}
                    and child.tag.rsplit('}', 1)[-1] == 'metadata')
        if metadata or namespace in _EDITOR_NAMESPACES:
            # Preserve following mixed-content text when metadata is embedded
            # within a text element. ElementTree removal otherwise loses it.
            if child.tail:
                if previous is None:
                    node.text = (node.text or '') + child.tail
                else:
                    previous.tail = (previous.tail or '') + child.tail
            node.remove(child)
        else:
            _remove_svg_metadata(child)
            previous = child


def _svg(data):
    # Ignore external document type identifiers without loading their DTD.
    # Internal subsets/entities remain forbidden; Qt only sees the tree below.
    parser = expat.ParserCreate()
    def reject(*args):
        raise VectorImageError(tr(SVG_ERROR))
    def doctype(name, system_id, public_id, has_internal_subset):
        if name != 'svg' or has_internal_subset:
            reject()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.ProcessingInstructionHandler = reject
    depth = elements = 0
    def start_element(*args):
        nonlocal depth, elements
        depth += 1
        elements += 1
        if depth > 128 or elements > 50000:
            raise VectorImageError('SVG structure exceeds limit')
    def end_element(*args):
        nonlocal depth
        depth -= 1
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    try:
        parser.Parse(data, True)
        root = ET.fromstring(data)
        if root.tag not in {'svg', '{http://www.w3.org/2000/svg}svg'}:
            raise VectorImageError(tr(SVG_ERROR))
        _remove_svg_metadata(root)
        count = 0
        for node in root.iter():
            count += 1
            if count > 50000:
                raise VectorImageError('SVG element limit exceeded')
            local = node.tag.rsplit('}', 1)[-1]
            if local in {'script', 'foreignObject', 'animate', 'animateMotion', 'animateTransform', 'set', 'audio', 'video'}:
                raise VectorImageError(tr(SVG_ERROR))
            if node.tag.startswith('{') and not node.tag.startswith('{http://www.w3.org/2000/svg}'):
                raise VectorImageError(tr(SVG_ERROR))
            for key, value in node.attrib.items():
                attr = key.rsplit('}', 1)[-1].lower()
                if attr in {'base', 'src'} or attr.startswith('on'):
                    raise VectorImageError(tr(SVG_ERROR))
                if attr == 'href':
                    _reference(value)
                _css(value)
            if local == 'style':
                _css(''.join(node.itertext()))
        logical = _svg_logical_size(root)
        renderer = QSvgRenderer()
        renderer.setAnimationEnabled(False)
        # Serialize the inspected tree, never the original unfiltered bytes.
        sanitized = ET.tostring(root, encoding="utf-8")
        if not renderer.load(QByteArray(sanitized)) or not renderer.isValid():
            raise VectorImageError('Invalid SVG')
        renderer.setAnimationEnabled(False)
        target_size(logical, logical)
        box = renderer.viewBoxF()
        if not all(math.isfinite(v) for v in (box.x(), box.y(), box.width(), box.height())):
            raise VectorImageError('Invalid SVG viewBox')
        return renderer, logical
    except VectorImageError:
        raise
    except Exception as exc:
        raise VectorImageError(tr(SVG_ERROR)) from exc


def render_vector(source, suffix, bounds=None, *, probe=False, service=None, cancel_token=None, priority=None):
    ctx_service, ctx_cancel, ctx_priority = _context.get()
    service = service if service is not None else ctx_service
    cancel_token = cancel_token if cancel_token is not None else ctx_cancel
    priority = priority if priority is not None else ctx_priority
    from .supported_formats import ENABLED_IMAGE_EXTENSIONS
    if suffix not in ENABLED_IMAGE_EXTENSIONS:
        raise VectorImageError("Vector loading is disabled")
    check_cancel(cancel_token)
    data = read_vector(source, suffix)
    check_cancel(cancel_token)
    if suffix == '.ai':
        if not re.match(br'%PDF-[12]\.[0-9](?:\s|%)', data[:16]) or service is None:
            raise VectorImageError(tr(AI_ERROR))
        try:
            result = service.render_ai(data, bounds, probe=probe, cancel_token=cancel_token, priority=priority)
            check_cancel(cancel_token)
            return result
        except InterruptedError:
            raise
        except Exception as exc:
            check_cancel(cancel_token)
            raise VectorImageError(tr(AI_ERROR)) from exc
    if suffix != '.svg':
        raise VectorImageError('Unsupported vector format')
    while not _svg_slots.acquire(timeout=0.05):
        check_cancel(cancel_token)
    try:
        check_cancel(cancel_token)
        renderer, logical = _svg(data)
        try:
            if probe:
                return logical
            width, height = target_size(logical, bounds)
            image = QImage(width, height, QImage.Format.Format_RGBA8888)
            if image.isNull():
                raise VectorImageError('Vector allocation failed')
            image.fill(0)
            painter = QPainter(image)
            try:
                renderer.render(painter, QRectF(0, 0, width, height))
            finally:
                painter.end()
            check_cancel(cancel_token)
            return image, logical
        finally:
            del renderer
    finally:
        _svg_slots.release()


def vector_pil(source, suffix, bounds=None, **kwargs):
    image, logical = render_vector(source, suffix, bounds, **kwargs)
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    result = Image.frombytes('RGBA', (converted.width(), converted.height()),
                             bytes(converted.constBits()), 'raw', 'RGBA', converted.bytesPerLine())
    result.info['vector_logical_size'] = logical
    return result
