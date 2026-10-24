from concurrent.futures import ThreadPoolExecutor
from threading import Event
import zipfile

import pytest

from app.image_source import ImageSourceError, ZipImageSource
from app.raster_execution_budget import SourceRequestToken, source_request_scope


def payload_source(tmp_path, monkeypatch, compression=zipfile.ZIP_DEFLATED):
    path = tmp_path / "日本語.zip"
    payloads = {f"{i}.jpg": bytes(range(256)) * (2048 + i) for i in range(2)}
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    source = ZipImageSource(path)
    opened = {name: Event() for name in payloads}
    entered = {name: Event() for name in payloads}
    release = {name: Event() for name in payloads}
    closed = {name: Event() for name in payloads}
    original_open = source._zip.open

    class Entry:
        def __init__(self, stream, name):
            self.stream, self.name = stream, name
            self.first = True

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()
            closed[self.name].set()

        def read(self, size):
            if self.first:
                self.first = False
                entered[self.name].set()
                assert release[self.name].wait(5), "payload gate was not released"
            return self.stream.read(size)

    def open_entry(info, *args, **kwargs):
        stream = original_open(info, *args, **kwargs)
        opened[info.filename].set()
        return Entry(stream, info.filename)

    monkeypatch.setattr(source._zip, "open", open_entry)

    def read(name, token):
        with source_request_scope(token):
            registered = source._begin_request(name)
            try:
                return bytes(source._read_entry_qbytearray(name, registered)[0])
            finally:
                source._finish_request(name, registered)

    return source, payloads, opened, entered, release, closed, read


@pytest.mark.parametrize("action", ["complete", "cancel_one", "close"])
def test_deflated_payloads_overlap_without_losing_request_ownership(tmp_path, monkeypatch, action):
    source, payloads, _opened, entered, release, closed, read = payload_source(tmp_path, monkeypatch)
    tokens = {name: SourceRequestToken() for name in payloads}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {name: pool.submit(read, name, tokens[name]) for name in payloads}
            try:
                assert all(event.wait(2) for event in entered.values()), "second entry was serialized"
                if action == "cancel_one":
                    tokens["0.jpg"].set()
                elif action == "close":
                    source.close()
                    assert source._zip.fp is not None
            finally:
                for event in release.values():
                    event.set()
            for name, future in futures.items():
                if action == "close" or (action == "cancel_one" and name == "0.jpg"):
                    with pytest.raises(ImageSourceError):
                        future.result(timeout=5)
                else:
                    assert future.result(timeout=5) == payloads[name]
        assert all(event.is_set() for event in closed.values())
        assert not source._active_requests
        if action == "close":
            assert source._zip.fp is None
    finally:
        source.close()


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_other_compression_keeps_entry_open_and_read_serial(tmp_path, monkeypatch, compression):
    source, payloads, opened, entered, release, closed, read = payload_source(
        tmp_path, monkeypatch, compression
    )
    second_started = Event()

    def read_second():
        second_started.set()
        return read("1.jpg", SourceRequestToken())

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(read, "0.jpg", SourceRequestToken())
            try:
                assert entered["0.jpg"].wait(2)
                second = pool.submit(read_second)
                assert second_started.wait(2)
                assert not opened["1.jpg"].wait(.1)
            finally:
                for event in release.values():
                    event.set()
            assert first.result(timeout=5) == payloads["0.jpg"]
            assert second.result(timeout=5) == payloads["1.jpg"]
        assert all(event.is_set() for event in closed.values())
        assert not source._active_requests
    finally:
        source.close()


@pytest.mark.parametrize("fault", ["crc", "truncated", "read_error", "open_error"])
def test_payload_failure_closes_entry_and_preserves_other_reads(tmp_path, monkeypatch, fault):
    source, payloads, _opened, _entered, release, closed, read = payload_source(tmp_path, monkeypatch)
    for event in release.values():
        event.set()
    original_open = source._zip.open
    expected_error = zipfile.BadZipFile
    if fault == "crc":
        source._zip.getinfo("0.jpg").CRC ^= 1
    elif fault == "truncated":
        source._zip.getinfo("0.jpg").compress_size //= 2
    else:
        expected_error = OSError

    def faulty_open(info, *args, **kwargs):
        if info.filename == "0.jpg" and fault == "open_error":
            raise OSError("injected entry open failure")
        entry = original_open(info, *args, **kwargs)
        if info.filename == "0.jpg" and fault == "read_error":
            def fail_read(_size):
                raise OSError("injected entry read failure")
            entry.read = fail_read
        return entry

    monkeypatch.setattr(source._zip, "open", faulty_open)
    try:
        with pytest.raises(expected_error):
            read("0.jpg", SourceRequestToken())
        assert closed["0.jpg"].is_set() == (fault != "open_error")
        assert not source._active_requests
        assert read("1.jpg", SourceRequestToken()) == payloads["1.jpg"]
    finally:
        source.close()
    assert source._zip.fp is None
