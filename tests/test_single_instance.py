from __future__ import annotations

import struct
import json
from types import SimpleNamespace

import pytest

from app.single_instance import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    InstanceMessage,
    InstanceUnavailableError,
    SingleInstanceBroker,
    decode_message,
    encode_message,
    make_server_name,
)


def test_json_frame_round_trip_with_unicode_unc_and_flags():
    message = InstanceMessage(
        paths=(r"C:\本\book one.cbz", r"\\server\share\日本語.pdf"),
        new_window=True,
        browser_only=False,
        no_restore=True,
        sender_pid=123,
    )
    assert decode_message(encode_message(message)) == message


def test_protocol_and_limits_are_rejected():
    with pytest.raises(ValueError, match="protocol"):
        encode_message(InstanceMessage(protocol_version=PROTOCOL_VERSION + 1))
    oversized = struct.pack(">I", MAX_MESSAGE_BYTES + 1)
    with pytest.raises(ValueError, match="large"):
        decode_message(oversized)


def test_server_name_hides_identity_and_separates_portable_copies(tmp_path):
    first = make_server_name(tmp_path / "copy one", user_identity="secret-user")
    again = make_server_name(tmp_path / "copy one", user_identity="secret-user")
    second = make_server_name(tmp_path / "copy two", user_identity="secret-user")
    assert first == again
    assert first != second
    assert "secret-user" not in first
    assert str(tmp_path) not in first


def test_primary_listens_and_closes(qapp, tmp_path):
    broker = SingleInstanceBroker(
        make_server_name(tmp_path, user_identity="test-user")
    )
    try:
        assert broker.try_forward_or_listen(InstanceMessage(sender_pid=1))
        assert broker.is_listening
    finally:
        broker.close()


def test_server_releases_disconnected_client_sockets(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtNetwork import QLocalSocket

    broker = SingleInstanceBroker(make_server_name(tmp_path, user_identity='test-user'))
    # QLocalServer parents accepted sockets; simulate delivery without opening
    # an OS pipe so this ownership regression also runs in restricted sessions.
    client = QLocalSocket(broker.server)
    pending = [client]
    monkeypatch.setattr(broker.server, 'hasPendingConnections', lambda: bool(pending))
    monkeypatch.setattr(broker.server, 'nextPendingConnection', lambda: pending.pop())

    try:
        broker._accept_connections()
        assert client in broker._buffers
        client.disconnected.emit()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not broker._buffers
        assert not broker.server.findChildren(QLocalSocket)
    finally:
        broker.close()


def test_secondary_exits_before_mutable_services_are_opened(
    qapp, monkeypatch, tmp_path
):
    import main as entry_point

    class ForwardingBroker:
        def __init__(self, *args, **kwargs):
            pass

        def try_forward_or_listen(self, message, *, allow_listen):
            return False

        def close(self):
            pass

    monkeypatch.setattr(entry_point, "QApplication", lambda _args: qapp)
    monkeypatch.setattr(entry_point, "SingleInstanceBroker", ForwardingBroker)
    monkeypatch.setattr(
        entry_point,
        "resolve_app_paths",
        lambda **_kwargs: __import__(
            "app.app_paths", fromlist=["resolve_app_paths"]
        ).resolve_app_paths(
            profile_override=str(tmp_path),
            source_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        entry_point,
        "ConfigManager",
        lambda *_args, **_kwargs: pytest.fail(
            "secondary must not construct ConfigManager"
        ),
    )
    monkeypatch.setattr(
        entry_point,
        "MetadataStore",
        lambda *_args, **_kwargs: pytest.fail(
            "secondary must not construct MetadataStore"
        ),
    )
    monkeypatch.setattr(
        entry_point,
        "ApplicationController",
        lambda *_args, **_kwargs: pytest.fail(
            "secondary must not construct PdfiumService/controller"
        ),
    )

    assert entry_point.main([]) == 0


@pytest.mark.parametrize('accepted,status', [(None, 'busy'), (False, 'busy'), (True, 'ok')])
def test_ack_requires_acceptance(qapp, monkeypatch, accepted, status):
    message = InstanceMessage(paths=('日本語.cbz',))
    client = SimpleNamespace(readAll=lambda: encode_message(message))
    broker = SingleInstanceBroker('test-ack')
    calls, replies = [], []
    if accepted is not None:
        broker.request_handler = lambda request: (calls.append(request), accepted)[1]
    # QObject socket identity is hashable; use a minimal hashable stand-in.
    class Socket:
        readAll = staticmethod(client.readAll)
    socket = Socket()
    broker._buffers[socket] = bytearray()
    monkeypatch.setattr(broker, '_reply', lambda _, code, *args: replies.append(code))
    broker._read_from_socket(socket)
    broker._read_from_socket(socket)
    assert replies == [status]
    assert calls == ([] if accepted is None else [message])


@pytest.mark.parametrize('response', [None, {'status': 'busy'}, {'status': 'error'}, {'status': 'ok'}])
def test_connected_peer_never_falls_back_to_listening(qapp, monkeypatch, response):
    from app import single_instance
    payload = bytearray(json.dumps(response).encode() if response is not None else b'')
    socket = SimpleNamespace(connectToServer=lambda _: None, waitForConnected=lambda _: True,
        write=lambda frame: len(frame), bytesToWrite=lambda: 0, bytesAvailable=lambda: len(payload),
        waitForReadyRead=lambda _: False, disconnectFromServer=lambda: None)
    def read(count):
        chunk = bytes(payload[:count]); del payload[:count]; return chunk
    socket.read = read
    monkeypatch.setattr(single_instance, 'QLocalSocket', lambda: socket)
    broker = SingleInstanceBroker('test-connected')
    monkeypatch.setattr(broker, '_listen', lambda: pytest.fail('existing owner cannot be replaced'))
    if response == {'status': 'ok'}:
        assert broker.try_forward_or_listen(InstanceMessage()) is False
    else:
        with pytest.raises(InstanceUnavailableError):
            broker.try_forward_or_listen(InstanceMessage())


@pytest.mark.parametrize('arguments', [[], ['--no-single-instance']])
def test_busy_profile_cannot_start_writers(qapp, tmp_path, monkeypatch, arguments):
    from PySide6.QtCore import QLockFile
    import main as entry_point
    from app.app_paths import resolve_app_paths
    lock = QLockFile(str(tmp_path / '.nivisviewer-instance.lock'))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    messages = []
    monkeypatch.setattr(entry_point, 'QApplication', lambda _: qapp)
    monkeypatch.setattr(entry_point, 'resolve_app_paths', lambda **_: resolve_app_paths(
        profile_override=str(tmp_path), check_writable=False))
    monkeypatch.setattr(entry_point, '_probe_writable', lambda _: pytest.fail('busy profile must not be written'))
    monkeypatch.setattr(entry_point.QMessageBox, 'warning', lambda *args: messages.append(args))
    monkeypatch.setattr(SingleInstanceBroker, '_forward', lambda *args: False)
    monkeypatch.setattr(SingleInstanceBroker, '_listen', lambda *args: pytest.fail('cannot listen with a busy profile'))
    monkeypatch.setattr(entry_point, '_run_application', lambda *args: pytest.fail('cannot start writers'))
    try:
        assert entry_point.main(arguments) == 1
        assert len(messages) == 1
    finally:
        lock.unlock()


@pytest.mark.parametrize('accept', [True, False])
def test_real_local_socket_ack_matches_primary_handler(qapp, tmp_path, accept):
    from PySide6.QtNetwork import QLocalSocket
    from tests.test_application_controller import wait_until
    name = make_server_name(tmp_path, user_identity='test-ack')
    primary = SingleInstanceBroker(name)
    received, response = [], bytearray()
    primary.request_handler = lambda message: (received.append(message), accept)[1]
    assert primary.try_forward_or_listen(InstanceMessage())
    message = InstanceMessage(paths=('日本語.cbz',))
    client = QLocalSocket()
    client.readyRead.connect(lambda: response.extend(bytes(client.readAll())))
    try:
        client.connectToServer(name)
        assert client.waitForConnected(1000)
        frame = encode_message(message)
        assert client.write(frame) == len(frame)
        assert wait_until(qapp, lambda: response.endswith(b'}'))
        assert received == [message]
        assert json.loads(response)['status'] == ('ok' if accept else 'busy')
    finally:
        client.abort()
        primary.close()
