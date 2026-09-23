"""Run in the real repository with QT_QPA_PLATFORM=offscreen.

No real application, native input, user files or extra GUI are launched.
"""
from __future__ import annotations
from time import monotonic
from unittest.mock import patch
import pytest
pytest.importorskip('PySide6')
from PIL import Image
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QTest
from app.image_source import FolderImageSource
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan, WarmupStopReason
from app.zip_raster_book_runtime import (RasterBookRuntime, ZipRasterPage, ZipRasterDisplayUnit,
    ZipRasterRenderSpec, ZipRasterRequest, ZipRasterFramePage, _CachedFrame,
    _SourceKey, _CachedSource)

@pytest.fixture
def runtime(tmp_path,qapp):
    folder=tmp_path/'temporary-book';folder.mkdir()
    Image.new('RGB',(16,24)).save(folder/'page.png')
    source=FolderImageSource(folder)
    value=RasterBookRuntime(source,1,cache_byte_budget=16*1024*1024,cache_soft_target_bytes=16*1024*1024)
    try:yield value
    finally:
        value.cancel(clear_artifacts=True)
        # This module never submits decoding jobs. Drain owned timer events.
        qapp.processEvents()
        assert value.shutdown(wait_msecs=1000)
        source.close()


def scene(runtime,count,*,fill=True):
    units=tuple(ZipRasterDisplayUnit(i,(ZipRasterPage(i,f'{i}.png',(16,24)),),True) for i in range(count))
    topology=RasterBookTopology(units,identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:(p.page_index for p in u.pages),page_count=count)
    plan=RasterWarmupPlan(topology,current=units[0],identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:(p.page_index for p in u.pages),direction=1,background_enabled=True)
    spec=ZipRasterRenderSpec((32,48))
    pixmap=QPixmap(1,1);pixmap.fill()
    for unit in units if fill else units[:1]:
        page=unit.pages[0]
        frame=_CachedFrame(runtime._key_for(unit,spec),unit,
            (ZipRasterFramePage(page.page_index,page.image_id,page.image_id,(16,24),pixmap,None,None),),
            (None,),0.0,0.0)
        runtime._frame_store.put(frame)
    request=ZipRasterRequest(1,1,units[0],plan,spec,navigation_direction=1)
    return units,spec,request

@pytest.mark.parametrize('count',[257,4097])
def test_qt_continuation_finishes_without_navigation_or_idle_poll(runtime,qapp,count):
    units,spec,request=scene(runtime,count)
    assert runtime.request(request)
    assert runtime.release_continuous_warmup(request_id=1)
    assert runtime._warmup_continue_timer.isActive()
    assert not runtime._warmup_planner.book_complete
    deadline=monotonic()+5.0
    while not runtime._warmup_planner.book_complete and monotonic()<deadline:
        qapp.processEvents();QTest.qWait(1)
    assert runtime._warmup_planner.book_complete
    assert not runtime._warmup_continue_timer.isActive()
    assert runtime.metrics.jobs_submitted==0
    with patch.object(runtime._warmup_continue_timer,'start',wraps=runtime._warmup_continue_timer.start) as start:
        QTest.qWait(20);qapp.processEvents();assert start.call_count==0

@pytest.mark.parametrize('stop',['cancel','suspend','invalidate_layout','retire'])
def test_qt_continuation_stops_at_lifecycle_boundary(runtime,qapp,stop):
    _,_,request=scene(runtime,4097)
    runtime.request(request);runtime.release_continuous_warmup(request_id=1)
    assert runtime._warmup_continue_timer.isActive()
    getattr(runtime,stop)()
    assert not runtime._warmup_continue_timer.isActive()
    qapp.processEvents()
    assert runtime.metrics.jobs_submitted==0


def test_qt_free_capacity_does_not_enumerate_victims(runtime):
    units,spec,request=scene(runtime,3,fill=False)
    with patch.object(runtime,'_drive'):
        runtime.request(request)
    key=runtime._key_for(units[1],spec)
    with patch.object(runtime._frame_store,'lower_rank_reclaim_candidates',side_effect=AssertionError('cache-wide frame scan')), \
         patch.object(runtime._source_store,'lower_rank_reclaim_candidates',side_effect=AssertionError('cache-wide source scan')):
        assert runtime._prefetch_admission_decision(key).admitted
        assert runtime._prefetch_worker_budget(key)>0
        assert runtime._lower_rank_reclaim_plan(key,units[1],bytes_needed=0)==((),())


def test_qt_bulk_trim_preserves_source_index_and_byte_ledgers(runtime):
    units,spec,request=scene(runtime,128)
    for unit in units:
        image=QImage(3,5,QImage.Format.Format_RGBA8888);image.fill(0)
        page=unit.pages[0]
        key=_SourceKey(1,id(runtime.source),page.image_id,spec.adjustments,(3,5),False)
        runtime._source_store.put(_CachedSource(key,page.page_index,image,(16,24),True))
    with patch.object(runtime,'_drive'):runtime.request(request)
    frames=runtime._frame_store;sources=runtime._source_store
    frame_kept={k for k in frames._frames if frames._is_protected(k)}
    source_kept=sources._protected_keys()
    frame_builds=[];source_builds=[]
    original_frames=frames._ensure_eviction_order
    original_sources=sources._ensure_eviction_order
    def count_frames():
        if frames._eviction_order is None:frame_builds.append(1)
        return original_frames()
    def count_sources():
        if sources._eviction_order is None:source_builds.append(1)
        return original_sources()
    with patch.object(frames,'_ensure_eviction_order',side_effect=count_frames), \
         patch.object(sources,'_ensure_eviction_order',side_effect=count_sources):
        runtime._enforce_combined_budget(limit_bytes=1)
    assert len(frame_builds)<=1 and len(source_builds)<=1
    assert frame_kept.issubset(frames._frames)
    assert source_kept.issubset(sources._sources)
    assert sources.byte_size==sum(s.qimage.sizeInBytes() for s in sources._sources.values())
    assert frames.byte_size==sum(frames._frame_bytes(f) for f in frames._frames.values())
    assert {k for group in sources._sources_by_identity.values() for k in group}==set(sources._sources)
    assert sources.largest_source_bytes==max((s.qimage.sizeInBytes() for s in sources._sources.values()),default=0)
    assert frames.largest_frame_bytes==max((frames._frame_bytes(f) for f in frames._frames.values()),default=0)
