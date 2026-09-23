from __future__ import annotations
import pytest
from app.browser_workflow_policy import (
    SelectionAppearance, ThumbnailWarmupCursor, decode_preferred_drop_effect,
    normalize_workflow_settings, paste_is_move,
)

@pytest.mark.parametrize('raw,expected', [
    (b'\x02\0\0\0','move'), (b'\x01\0\0\0','copy'),
    (b'\x03\0\0\0','unknown(3)'), (b'\x04\0\0\0','unknown(4)'),
    (b'', 'invalid'), (b'\x02', 'invalid'), (b'\x02\0\0\0\0','move'),
])
def test_windows_dword(raw, expected):
    assert decode_preferred_drop_effect(raw) == expected

@pytest.mark.parametrize('matching,cut,effect,expected', [
    (False,False,'move',True), (False,True,'copy',False),
    (False,True,'unspecified',False), (False,False,'invalid',False),
    (True,True,'unspecified',True), (True,False,'move',False),
])
def test_paste_current_clipboard_is_authoritative(matching,cut,effect,expected):
    assert paste_is_move(internal_matches=matching,internal_cut=cut,
                         preferred_effect=effect) is expected

@pytest.mark.parametrize('values,expected', [
    ({}, (3,38,2,'auto',True,False)),
    ({'browser_thumbnail_background_screens':-1},(-1,38,2,'auto',True,False)),
    ({'browser_thumbnail_background_screens':0},(0,38,2,'auto',True,False)),
    ({'browser_selection_color':'#FF00AA'},(3,38,2,'#ff00aa',True,False)),
    ({'browser_selection_color':'url(bad)'},(3,38,2,'auto',True,False)),
    ({'browser_selection_filename_opacity':999,'browser_selection_border_width':-99},(3,100,1,'auto',True,False)),
    ({'browser_thumbnail_background_screens':float('inf')},(3,38,2,'auto',True,False)),
    ({'browser_thumbnail_background_screens':True},(3,38,2,'auto',True,False)),
    ({'browser_selection_text_color_auto_adjust':False,
      'browser_selection_frame_rounded':True},(3,38,2,'auto',False,True)),
    ({'browser_selection_text_color_auto_adjust':'false',
      'browser_selection_frame_rounded':1},(3,38,2,'auto',True,False)),
])
def test_preferences_normalized(values,expected):
    data=normalize_workflow_settings(values)
    assert tuple(data.values()) == expected

@pytest.mark.parametrize('opacity,alpha', [(0,0),(38,96),(50,128),(100,255)])
def test_selection_opacity( opacity, alpha):
    assert SelectionAppearance.from_settings({'browser_selection_filename_opacity':opacity}).alpha == alpha


def drain(cursor):
    rows=[]
    while not cursor.exhausted:
        row=cursor.take(scan_limit=4)
        if row is not None:
            rows.append(row)
            cursor.complete(row)
    return rows


def test_finite_screens_both_sides_direction_priority():
    down=ThumbnailWarmupCursor(200)
    down.recenter(40,49,1,2)
    assert drain(down) == list(range(50,70)) + list(range(39,19,-1))
    up=ThumbnailWarmupCursor(200)
    up.recenter(40,49,-1,2)
    assert drain(up) == list(range(39,19,-1)) + list(range(50,70))


@pytest.mark.parametrize(
    "screens,expected_count",
    [(0, 0), (1, 80), (3, 240), (10, 800), (-1, 1960)],
)
def test_background_screen_range_values(screens, expected_count):
    cursor = ThumbnailWarmupCursor(2000)
    cursor.recenter(700, 739, 1, screens)
    rows = drain(cursor)
    assert len(rows) == expected_count
    if screens > 0:
        assert rows[: min(screens * 40, 1260)] == list(
            range(740, 740 + min(screens * 40, 1260))
        )


def test_zero_and_unlimited_do_not_materialize_a_queue():
    c=ThumbnailWarmupCursor(1000000)
    c.recenter(400,499,1,0)
    assert drain(c)==[]
    c.recenter(400,499,1,-1)
    assert isinstance(c._done,bytearray) and len(c._done)==1000000
    assert not isinstance(c._order, (list,tuple))
    assert c.take()==500


def test_unlimited_never_repeats_processed_rows_after_lru_eviction():
    c=ThumbnailWarmupCursor(300)
    c.recenter(0,9,1,-1)
    assert drain(c)==list(range(10,300))
    c.recenter(100,109,-1,-1)
    assert drain(c)==list(range(9,-1,-1))


def test_cancelled_or_rejected_request_not_lost():
    c=ThumbnailWarmupCursor(12)
    c.recenter(0,1,1,2)
    row=c.take(); assert row==2
    c.retry(row); assert c.take()==2
    c.complete(2); c.retry(2)
    assert c.take()==3


def test_recenter_retries_unfinished_without_revisiting_done():
    c=ThumbnailWarmupCursor(20)
    c.recenter(0,2,1,2)
    assert c.take()==3
    c.recenter(1,3,1,2)
    assert c.take()==4
    c.complete(4)
    c.recenter(0,2,1,2)
    assert c.take()==3
    c.complete(3)
    assert c.take()==5


def test_bounded_scan_yields():
    c=ThumbnailWarmupCursor(50)
    c.recenter(0,1,1,-1)
    for i in range(2,12): c.complete(i)
    assert c.take(scan_limit=3) is None and not c.exhausted
    assert c.take(scan_limit=3) is None and not c.exhausted


def test_empty_book():
    c=ThumbnailWarmupCursor(0); c.recenter(0,0,1,-1)
    assert c.take() is None and c.exhausted
