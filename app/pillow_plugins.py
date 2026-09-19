"""Register available optional Pillow codecs before image workers start."""

from pathlib import Path

from .i18n import tr


try:
    from pillow_jxl import JpegXLImagePlugin
except (ImportError, OSError) as exc:
    # A source checkout may use a different Python than the packaged runtime.
    # Missing packages/dependent DLLs must not disable unrelated image formats.
    JXL_AVAILABLE = False
    JXL_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    JXL_AVAILABLE = True
    JXL_IMPORT_ERROR = ""
    # Bound per-image native threads; registration still precedes worker use.
    JpegXLImagePlugin.DECODE_THREADS = 2


def jxl_status_text() -> str:
    if JXL_AVAILABLE:
        return tr('利用可能')
    return tr('JPEG XLは利用できません。このPython環境のpillow-jxl-pluginが未導入、または読み込めません。')


def decoder_unavailable_message(path: str | Path) -> str | None:
    if Path(path).suffix.casefold() == '.jxl' and not JXL_AVAILABLE:
        return jxl_status_text()
    return None
