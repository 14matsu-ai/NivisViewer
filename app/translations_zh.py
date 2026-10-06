"""Complete, explicit application-owned Chinese translation catalogs."""

from __future__ import annotations

from .translations_en import ENGLISH, ENGLISH_DISAMBIGUATED
from .translations_zh_part1 import SIMPLIFIED as _S1, TRADITIONAL as _T1
from .translations_zh_part2 import SIMPLIFIED as _S2, TRADITIONAL as _T2
from .translations_zh_part3 import SIMPLIFIED as _S3, TRADITIONAL as _T3
from .translations_zh_part4 import SIMPLIFIED as _S4, TRADITIONAL as _T4
from .translations_zh_recent import SIMPLIFIED as _SR, TRADITIONAL as _TR


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


_ICON_LOCATION_SIMPLIFIED = {
    'ファイル種別アイコン': '文件类型图标',
    '左下アイコン：左端から': '左下角图标：距左边缘',
    '左下アイコン：下端から': '左下角图标：距下边缘',
    '自動（既定の位置）': '自动（默认位置）',
    'サムネイル枠の端から、見えるアイコンまでの距離です。\n画面倍率に応じて拡大されます。自動では従来の位置を保ちます。': '缩略图边框边缘到可见图标的距离。\n随显示缩放比例调整。自动时保留原来的位置。',
    'ドライブを選択': '选择驱动器',
}
_ICON_LOCATION_TRADITIONAL = {
    'ファイル種別アイコン': '檔案類型圖示',
    '左下アイコン：左端から': '左下角圖示：距左邊緣',
    '左下アイコン：下端から': '左下角圖示：距下邊緣',
    '自動（既定の位置）': '自動（預設位置）',
    'サムネイル枠の端から、見えるアイコンまでの距離です。\n画面倍率に応じて拡大されます。自動では従来の位置を保ちます。': '縮圖邊框邊緣到可見圖示的距離。\n隨顯示縮放比例調整。自動時保留原來的位置。',
    'ドライブを選択': '選擇磁碟機',
}
_PARALLEL_SIMPLIFIED = {
    '1（推奨）': '1（推荐）',
    '2': '2',
    '画像の同時読み込み数:': '同时加载的图像数：',
    'ZIPの次ページ展開を並列処理する（推奨）': '并行解压ZIP的下一页（推荐）',
    '同時読み込み数は画像フォルダとZIPに適用します。ZIPの並列展開は最大1ページ分です。読み込み数を増やすと、ページを戻す操作が遅くなる場合があります。変更は次にファイルを開いたときから適用します。':
        '同时加载数适用于图像文件夹和ZIP文件。ZIP最多提前解压一页。增加同时加载数可能会减慢向后翻页。更改将在下次打开文件时生效。',
}
_PARALLEL_TRADITIONAL = {
    '1（推奨）': '1（建議）',
    '2': '2',
    '画像の同時読み込み数:': '同時載入的影像數：',
    'ZIPの次ページ展開を並列処理する（推奨）': '平行解壓縮ZIP的下一頁（建議）',
    '同時読み込み数は画像フォルダとZIPに適用します。ZIPの並列展開は最大1ページ分です。読み込み数を増やすと、ページを戻す操作が遅くなる場合があります。変更は次にファイルを開いたときから適用します。':
        '同時載入數適用於影像資料夾和ZIP檔案。ZIP最多提前解壓縮一頁。增加同時載入數可能會減慢向後翻頁。變更將於下次開啟檔案時生效。',
}
_FOLDER_SORT_SIMPLIFIED = {
    '並び順変更指定…': '文件夹排序规则…',
    '並び順変更指定': '文件夹排序规则',
    '指定が適用されるフォルダでは、一覧上部の並び替えは一時変更です。保存するにはこの画面で編集してください。':
        '对于应用规则的文件夹，列表上方的排序更改仅临时生效。要保存规则，请在此编辑。',
    '並び順': '排序',
    '並び指定を編集': '编辑文件夹排序规则',
    '対象フォルダ:': '目标文件夹：',
    '対象フォルダ': '目标文件夹',
    '対象フォルダを選択': '选择目标文件夹',
    '対象フォルダを絶対パスで指定してください。': '请输入目标文件夹的绝对路径。',
    '普段の並び順を使う': '使用默认排序',
    '適用範囲:': '适用范围：',
    '適用範囲': '适用范围',
    'このフォルダのみ': '仅此文件夹',
    'このフォルダと階下': '此文件夹及所有子文件夹',
    'このフォルダを除いた階下': '仅所有子文件夹',
    'フォルダごとの並び順を指定します。指定が重なる場合は、近いフォルダの指定を優先します。お気に入りへの登録は不要です。':
        '为文件夹设置排序。规则重叠时优先使用最近的文件夹规则。无需添加到收藏。',
    '現在のフォルダを追加…': '添加当前文件夹…',
    'フォルダを選んで追加…': '选择要添加的文件夹…',
    '編集…': '编辑…',
    '{p0}〈一時変更〉': '{p0}（临时）',
    '{p0}<指定>': '{p0}<指定>',
}
_FOLDER_SORT_TRADITIONAL = {
    '並び順変更指定…': '資料夾排序規則…',
    '並び順変更指定': '資料夾排序規則',
    '指定が適用されるフォルダでは、一覧上部の並び替えは一時変更です。保存するにはこの画面で編集してください。':
        '對於套用規則的資料夾，清單上方的排序變更僅暫時生效。要儲存規則，請在此編輯。',
    '並び順': '排序',
    '並び指定を編集': '編輯資料夾排序規則',
    '対象フォルダ:': '目標資料夾：',
    '対象フォルダ': '目標資料夾',
    '対象フォルダを選択': '選擇目標資料夾',
    '対象フォルダを絶対パスで指定してください。': '請輸入目標資料夾的絕對路徑。',
    '普段の並び順を使う': '使用預設排序',
    '適用範囲:': '適用範圍：',
    '適用範囲': '適用範圍',
    'このフォルダのみ': '僅此資料夾',
    'このフォルダと階下': '此資料夾及所有子資料夾',
    'このフォルダを除いた階下': '僅所有子資料夾',
    'フォルダごとの並び順を指定します。指定が重なる場合は、近いフォルダの指定を優先します。お気に入りへの登録は不要です。':
        '為資料夾設定排序。規則重疊時優先使用最近的資料夾規則。無需加入收藏。',
    '現在のフォルダを追加…': '新增目前資料夾…',
    'フォルダを選んで追加…': '選擇要新增的資料夾…',
    '編集…': '編輯…',
    '{p0}〈一時変更〉': '{p0}（暫時）',
    '{p0}<指定>': '{p0}<指定>',
}
SIMPLIFIED_CHINESE = _complete_catalog(_S1, _S2, _S3, _S4, _ICON_LOCATION_SIMPLIFIED, _PARALLEL_SIMPLIFIED, _SR, _FOLDER_SORT_SIMPLIFIED)
TRADITIONAL_CHINESE = _complete_catalog(_T1, _T2, _T3, _T4, _ICON_LOCATION_TRADITIONAL, _PARALLEL_TRADITIONAL, _TR, _FOLDER_SORT_TRADITIONAL)

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
