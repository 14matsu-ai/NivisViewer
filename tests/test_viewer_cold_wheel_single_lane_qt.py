"""Actual Qt + ZipRasterBookRuntime regressions. Run with offscreen only.

Uses a temporary real ZIP of small PNGs. A controlled source gate represents
an in-flight read; it is NOT a benchmark of Windows or native JPEG latency.
No ViewerWindow, main.py, Explorer, user collections, or native input is used.
"""
from __future__ import annotations
import io
import os
import time
import zipfile
from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PySide6')
from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from app.image_source import ZipImageSource
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime, ZipRasterPage, ZipRasterDisplayUnit,
    ZipRasterRenderSpec, ZipRasterRequest, ZipRasterFramePage, _CachedFrame,
)

@pytest.fixture(scope='module')
def app():
    assert os.environ.get('QT_QPA_PLATFORM')=='offscreen'
    return QApplication.instance() or QApplication([])


def wait_until(app, predicate, timeout=3.0):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return True
        time.sleep(.001)
    app.processEvents()
    return bool(predicate())


@pytest.fixture
def scene(tmp_path,app,monkeypatch):
    archive=tmp_path/'cold-gate.zip'
    with zipfile.ZipFile(archive,'w') as out:
        for index in range(8):
            with Image.new('RGB',(24,36),(index*20,80,150)) as image:
                data=io.BytesIO();image.save(data,format='PNG')
            out.writestr(f'{index}.png',data.getvalue())
    source=ZipImageSource(archive)
    runtime=ZipRasterBookRuntime(source,1,cache_byte_budget=16*1024*1024,
                                cache_soft_target_bytes=16*1024*1024)
    units=tuple(ZipRasterDisplayUnit(i,(ZipRasterPage(i,f'{i}.png',(24,36)),),True)
                for i in range(8))
    topology=RasterBookTopology(units,identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:(p.page_index for p in u.pages),page_count=8)
    spec=ZipRasterRenderSpec((32,48))
    context=SimpleNamespace(runtime=runtime,source=source,units=units,spec=spec,
        gate=Event(),entered=Event(),source_cancel=None,cooperative=True,
        decoded=[],published=[])
    real_open=source.open_image
    def controlled_open(image_id):
        context.decoded.append(image_id)
        if image_id=='1.png' and not context.gate.is_set():
            token=source._begin_request(image_id)
            context.source_cancel=token
            context.entered.set()
            try:
                # Bound this test-only gate even if fixture cleanup is broken.
                deadline=time.monotonic()+6.0
                while not context.gate.wait(.002):
                    if context.cooperative:source._raise_if_cancelled(token)
                    if time.monotonic()>deadline:raise RuntimeError('test gate timeout')
                source._raise_if_cancelled(token)
            finally:
                source._finish_request(image_id,token)
        return real_open(image_id)
    monkeypatch.setattr(source,'open_image',controlled_open)
    runtime.frameReady.connect(context.published.append)
    def request(index,serial):
        plan=RasterWarmupPlan(topology,current=units[index],identity_of=lambda u:u.identity,
            page_indexes_of=lambda u:(p.page_index for p in u.pages),direction=1,
            background_enabled=True)
        return ZipRasterRequest(1,serial,units[index],plan,spec,navigation_direction=1)
    context.request=request
    def cache_frame(index):
        unit=units[index];page=unit.pages[0]
        pixmap=QPixmap(24,36);pixmap.fill()
        frame=_CachedFrame(runtime._key_for(unit,spec),unit,
            (ZipRasterFramePage(index,page.image_id,page.image_id,(24,36),pixmap,None,None),),
            (None,),0.0,0.0)
        runtime._frame_store.put(frame)
    context.cache_frame=cache_frame
    def start_reverse_prefetch():
        cache_frame(2);cache_frame(3)
        assert runtime.request(request(2,1))
        assert runtime.release_continuous_warmup(request_id=1)
        assert wait_until(app,context.entered.is_set)
        assert runtime._active_job is not None
        assert runtime._active_job.key.unit_identity==units[1].identity
        return runtime._active_job
    context.start=start_reverse_prefetch
    try:yield context
    finally:
        runtime.cancel(clear_artifacts=True)
        context.gate.set()
        assert wait_until(app,lambda:not runtime.has_unfinished_tasks(),timeout=4.0)
        assert runtime.shutdown(wait_msecs=1000)
        source.close()


def test_cached_hops_then_cold_target_preempts_background(scene,app):
    s=scene;r=s.runtime;old=s.start()
    assert r.request(s.request(3,2),preserve_started_compatible=True)
    assert not old.cancelled.is_set()
    assert r.request(s.request(4,3),preserve_started_compatible=True)
    assert old.cancelled.is_set()
    assert wait_until(app,lambda:any(f.unit.start_index==4 for f in s.published))
    assert not s.gate.is_set()  # Current did not require releasing old read gate.
    assert s.decoded.count('4.png')==1


def test_identical_cold_target_adopts_running_job(scene,app):
    s=scene;r=s.runtime;job=s.start()
    assert r.request(s.request(1,2),preserve_started_compatible=True)
    assert not job.cancelled.is_set() and job.prefetch_budget_bytes is None
    s.gate.set()
    assert wait_until(app,lambda:any(f.unit.start_index==1 for f in s.published))
    assert s.decoded.count('1.png')==1


def test_fully_warm_navigation_keeps_background_work(scene,app):
    s=scene;r=s.runtime;job=s.start();s.cache_frame(4)
    assert r.request(s.request(3,2),preserve_started_compatible=True)
    assert r.request(s.request(4,3),preserve_started_compatible=True)
    assert not job.cancelled.is_set()
    assert any(f.unit.start_index==4 for f in s.published)
    assert '4.png' not in s.decoded


def test_pending_current_callback_keeps_compatible_work_and_no_duplicate(scene):
    s=scene;r=s.runtime;job=s.start()
    key=r._key_for(s.units[4],s.spec);r._pending_completion_keys.add(key)
    assert r.request(s.request(4,2),preserve_started_compatible=True)
    assert not job.cancelled.is_set() and '4.png' not in s.decoded
    r._pending_completion_keys.discard(key)


def test_cancel_does_not_release_noninterruptible_slot_early(scene,app):
    s=scene;s.cooperative=False;r=s.runtime;job=s.start()
    r.request(s.request(4,2),preserve_started_compatible=True)
    assert job.cancelled.is_set()
    assert r._active_job is job and job in r._inflight_reservations
    assert '4.png' not in s.decoded
    s.gate.set()
    assert wait_until(app,lambda:any(f.unit.start_index==4 for f in s.published))
    assert job not in r._inflight_reservations


def test_reverse_before_old_slot_returns_dispatches_latest_target(scene,app):
    s=scene;s.cooperative=False;r=s.runtime;job=s.start()
    r.request(s.request(4,2),preserve_started_compatible=True)
    r.request(s.request(0,3),preserve_started_compatible=True)
    assert job.cancelled.is_set() and '0.png' not in s.decoded
    s.gate.set()
    assert wait_until(app,lambda:any(f.unit.start_index==0 for f in s.published))
    assert not any(f.unit.start_index==4 for f in s.published)


def test_staged_request_does_not_open_dispatch_early(scene,app):
    s=scene;s.cooperative=False;r=s.runtime;job=s.start()
    latest=s.request(4,2);assert r.stage(latest)
    assert job.cancelled.is_set()
    s.gate.set();assert wait_until(app,lambda:not r.has_unfinished_tasks())
    assert '4.png' not in s.decoded
    assert r.release_staged(latest)
    assert wait_until(app,lambda:any(f.unit.start_index==4 for f in s.published))


def test_stop_after_preemption_never_publishes_old_current(scene,app):
    s=scene;s.cooperative=False;r=s.runtime;s.start()
    r.request(s.request(4,2),preserve_started_compatible=True)
    r.cancel(clear_artifacts=True);s.gate.set()
    assert wait_until(app,lambda:not r.has_unfinished_tasks())
    assert not any(f.unit.start_index==4 for f in s.published)
    assert not r._inflight_reservations


def test_sliding_spread_reuses_shared_started_source(scene,app):
    s=scene;r=s.runtime;job=s.start()
    paired=ZipRasterDisplayUnit(1,(s.units[1].pages[0],s.units[2].pages[0]),False)
    topology=RasterBookTopology(s.units,identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:(p.page_index for p in u.pages),page_count=len(s.units))
    plan=RasterWarmupPlan(topology,current=paired,identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:(p.page_index for p in u.pages),direction=1,
        background_enabled=True)
    request=ZipRasterRequest(1,2,paired,plan,s.spec,navigation_direction=1)
    assert r.request(request,preserve_started_compatible=True)
    assert not job.cancelled.is_set()
    s.gate.set()
    assert wait_until(app,lambda:any(f.unit.identity==paired.identity for f in s.published))
    assert s.decoded.count('1.png')==1
