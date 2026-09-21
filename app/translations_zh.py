"""Complete, explicit application-owned Chinese translation catalogs."""

from __future__ import annotations

from .translations_en import ENGLISH, ENGLISH_DISAMBIGUATED
from .translations_zh_part1 import SIMPLIFIED as _S1, TRADITIONAL as _T1
from .translations_zh_part2 import SIMPLIFIED as _S2, TRADITIONAL as _T2
from .translations_zh_part3 import SIMPLIFIED as _S3, TRADITIONAL as _T3
from .translations_zh_part4 import SIMPLIFIED as _S4, TRADITIONAL as _T4


def _complete_catalog(*parts: dict[str, str]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for part in parts:
        overlap = catalog.keys() & part.keys()
        if overlap:
            raise ValueError(f"Duplicate Chinese translation keys: {sorted(overlap)!r}")
        catalog.update(part)
    missing = ENGLISH.keys() - catalog.keys()
    extra = catalog.keys() - ENGLISH.keys()
    if missing or extra:
        raise ValueError(f"Chinese translation inventory mismatch: missing={sorted(missing)!r}, extra={sorted(extra)!r}")
    return catalog


SIMPLIFIED_CHINESE = _complete_catalog(_S1, _S2, _S3, _S4)
TRADITIONAL_CHINESE = _complete_catalog(_T1, _T2, _T3, _T4)

SIMPLIFIED_CHINESE_DISAMBIGUATED = {
    ("縮小", "resampling"): "缩小采样",
    ("拡大", "resampling"): "放大采样",
    ("移動", "navigation"): "跳转",
}
TRADITIONAL_CHINESE_DISAMBIGUATED = {
    ("縮小", "resampling"): "縮小取樣",
    ("拡大", "resampling"): "放大取樣",
    ("移動", "navigation"): "跳轉",
}

for _catalog in (SIMPLIFIED_CHINESE_DISAMBIGUATED, TRADITIONAL_CHINESE_DISAMBIGUATED):
    if _catalog.keys() != ENGLISH_DISAMBIGUATED.keys():
        raise ValueError("Chinese disambiguated translation inventory mismatch")
