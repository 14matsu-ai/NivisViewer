"""Actual font glyph coverage, immediate rendering and safe filename elision."""
from pathlib import Path
import pytest
from PySide6.QtCore import QRect,Qt
from PySide6.QtGui import QFont,QFontDatabase,QRawFont,QImage,QPainter
from PySide6.QtWidgets import QStyleOptionViewItem,QListView
from app.filename_fallback import FilenameFallback,_grapheme_boundaries
from app.browser_item_delegate import BrowserItemDelegate


@pytest.fixture
def real_fonts(qapp):
    paths=[Path('C:/Windows/Fonts/YuGothR.ttc'),Path('C:/Windows/Fonts/msyh.ttc')]
    if not all(p.exists() for p in paths):pytest.skip('Installed Windows CJK fonts required')
    ids=[QFontDatabase.addApplicationFont(str(p)) for p in paths]
    assert all(i>=0 for i in ids)
    font=QFont('Yu Gothic UI',9)
    assert QRawFont.fromFont(font).familyName()=='Yu Gothic UI'
    assert not QRawFont.fromFont(font).supportsCharacter(0x6da6)
    assert QRawFont.fromFont(QFont('Microsoft YaHei UI',9)).supportsCharacter(0x6da6)
    yield font
    for i in ids:QFontDatabase.removeApplicationFont(i)


def test_missing_glyph_is_explicitly_formatted_without_changing_other_text(real_fonts):
    resolver=FilenameFallback();text='Japanese \u6da6 filename.zip'
    layout=resolver.prepare(real_fonts,text,1000,suffix='.zip')
    assert layout is not None and layout.text()==text
    formats=layout.formats()
    assert len(formats)==1
    assert formats[0].start==text.index('\u6da6') and formats[0].length==1
    assert formats[0].format.font().family()=='Microsoft YaHei UI'
    assert layout.font().family()==real_fonts.family()
    assert layout.font().styleStrategy() & QFont.StyleStrategy.NoFontMerging
    families={run.rawFont().familyName() for run in layout.glyphRuns()}
    assert families=={'Yu Gothic UI','Microsoft YaHei UI'}
    assert all(0 not in run.glyphIndexes() for run in layout.glyphRuns())


def test_missing_glyph_elision_keeps_extension_and_fits(real_fonts):
    resolver=FilenameFallback()
    text='\u6da6 data long long long long filename.tar.gz'
    layout=resolver.prepare(real_fonts,text,105,suffix='.tar.gz')
    assert layout is not None
    assert layout.text().endswith('\u2026.tar.gz')
    assert layout.text().startswith('\u6da6')
    assert layout.lineAt(0).naturalTextWidth()<=105


def test_supported_and_bidirectional_names_keep_original_path(real_fonts):
    resolver=FilenameFallback()
    assert resolver.prepare(real_fonts,'normal Japanese filename.zip',105,suffix='.zip') is None
    assert resolver.prepare(real_fonts,'\u0639\u0631\u0628\u064a \u6da6.zip',105,suffix='.zip') is None
    assert _grapheme_boundaries('A\U0001f600e\u0301Z')==[0,1,2,4,5]


def test_filename_draws_immediately_and_reuses_layout_without_worker(real_fonts,qapp):
    view=QListView();view.setFont(real_fonts)
    delegate=BrowserItemDelegate(view,thumbnail_size=149,density='medium')
    option=QStyleOptionViewItem();option.initFrom(view);option.font=real_fonts;option.rect=delegate.grid_metrics.cell_rect()
    image=QImage(500,800,QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(2.5);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image)
    try:
        delegate._paint_title(painter,option,delegate.grid_metrics.thumbnail_frame_rect(option.rect),'\u6da6 filename.zip')
        layouts=[x for x in delegate._title_fallback_layouts.values() if x is not None]
        assert len(layouts)==1
        delegate._paint_title(painter,option,delegate.grid_metrics.thumbnail_frame_rect(option.rect),'\u6da6 filename.zip')
        assert [x for x in delegate._title_fallback_layouts.values() if x is not None]==layouts
    finally:painter.end();view.close()
    assert any(bytes(image.constBits()))
    assert not hasattr(delegate,'_title_cache')

def test_font_coverage_inspection_never_initializes_unrestricted_merging(real_fonts,monkeypatch):
    import app.filename_fallback as module
    original=module.QRawFont
    strategies=[]
    class Inspection:
        @staticmethod
        def fromFont(font):
            strategies.append(font.styleStrategy())
            return original.fromFont(font)
    monkeypatch.setattr(module,'QRawFont',Inspection)
    layout=module.FilenameFallback().prepare(real_fonts,'\u6da6 example.zip',105,suffix='.zip')
    assert layout is not None and strategies
    assert all(strategy & QFont.StyleStrategy.NoFontMerging for strategy in strategies)
