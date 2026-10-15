"""Resolve missing filename glyphs without an installed-font-wide search."""
from collections import OrderedDict
import unicodedata
from PySide6.QtCore import QPointF, QTextBoundaryFinder
from PySide6.QtGui import QFont, QRawFont, QTextLayout, QTextCharFormat, QTextOption


class FilenameFallback:
    FAMILIES = ('Segoe UI', 'Yu Gothic UI', 'Meiryo', 'Microsoft YaHei UI',
                'Microsoft JhengHei UI', 'Segoe UI Symbol', 'Segoe UI Emoji')

    def __init__(self):
        self._fonts = OrderedDict()

    def prepare(self, font, text, width, *, suffix=''):
        # Keep Qt's existing elision for bidirectional/contextual scripts.
        if any(unicodedata.bidirectional(char) in {'R', 'AL', 'AN', 'RLE', 'RLO', 'LRE', 'LRO', 'RLI', 'LRI', 'FSI', 'PDI', 'PDF'} for char in text):
            return None
        key = QFont(font)
        state = self._fonts.get(key)
        if state is None:
            inspection_font = QFont(font)
            inspection_font.setStyleStrategy(inspection_font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
            primary = QRawFont.fromFont(inspection_font)
            state = (primary, {}, {})
            self._fonts[key] = state
            if len(self._fonts) > 8:
                self._fonts.popitem(last=False)
        else:
            self._fonts.move_to_end(key)
        primary, candidates, choices = state
        if not primary.isValid() or not primary.supportsCharacter(0x2026):
            return None
        missing = False
        for char in set(text):
            if char not in choices:
                choice = None
                if not primary.supportsCharacter(ord(char)):
                    # Controls and selectors are interpreted by the text engine.
                    if char.isspace() or ord(char) in {0x200c, 0x200d} or 0xfe00 <= ord(char) <= 0xfe0f:
                        choices[char] = None
                        continue
                    for family in self.FAMILIES:
                        if family not in candidates:
                            candidate = QFont(font)
                            candidate.setFamily(family)
                            candidate.setStyleStrategy(candidate.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
                            raw = QRawFont.fromFont(candidate)
                            candidates[family] = (candidate, raw)
                        candidate, raw = candidates[family]
                        if (raw.isValid() and raw.familyName().casefold() == family.casefold()
                                and raw.supportsCharacter(ord(char))):
                            choice = candidate
                            break
                    if choice is None:
                        # Preserve Qt's unrestricted fallback for uncommon fonts.
                        return None
                choices[char] = choice
                if len(choices) > 2048:
                    choices.clear()
                    return None
            missing = missing or choices[char] is not None
        if not missing:
            return None
        base = QFont(font)
        base.setStyleStrategy(base.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
        def layout(value):
            result = QTextLayout(value, base)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.NoWrap)
            result.setTextOption(option)
            ranges = []
            offset = 0
            for char in value:
                length = 2 if ord(char) > 0xffff else 1
                choice = choices.get(char)
                if choice is not None:
                    entry = QTextLayout.FormatRange()
                    entry.start, entry.length = offset, length
                    entry.format = QTextCharFormat()
                    entry.format.setFont(choice)
                    ranges.append(entry)
                offset += length
            result.setFormats(ranges)
            result.setCacheEnabled(True)
            result.beginLayout()
            line = result.createLine()
            line.setLineWidth(1000000)
            result.endLayout()
            return result, line.naturalTextWidth()
        full, full_width = layout(text)
        if full_width <= width:
            return full
        ellipsis = '\u2026'
        _, ellipsis_width = layout(ellipsis)
        suffix_width = layout(suffix)[1] if suffix else 0
        if suffix and width-suffix_width < ellipsis_width:
            suffix, suffix_width = '', 0
        stem = text[:-len(suffix)] if suffix else text
        boundaries = _grapheme_boundaries(stem)
        low, high = 0, len(boundaries)-1
        while low < high:
            middle = (low+high+1)//2
            candidate = stem[:boundaries[middle]]+ellipsis+suffix
            if layout(candidate)[1] <= width:
                low = middle
            else:
                high = middle-1
        return layout(stem[:boundaries[low]]+ellipsis+suffix)[0]

    @staticmethod
    def draw(painter, rect, layout):
        line = layout.lineAt(0)
        position = QPointF(rect.left()+(rect.width()-line.naturalTextWidth())/2,
                           rect.top()+(rect.height()-line.height())/2)
        layout.draw(painter, position)


def _grapheme_boundaries(text):
    utf16_to_python = {0: 0}
    offset = 0
    for index, char in enumerate(text):
        offset += 2 if ord(char) > 0xffff else 1
        utf16_to_python[offset] = index+1
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    result = [0]
    while True:
        position = finder.toNextBoundary()
        if position < 0:
            return result
        if position in utf16_to_python:
            result.append(utf16_to_python[position])
