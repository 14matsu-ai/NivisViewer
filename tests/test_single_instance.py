from __future__ import annotations

import struct

import pytest

from app.single_instance import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    InstanceMessage,
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


def test_secondary_exits_before_mutable_services_are_opened(
    qapp, monkeypatch, tmp_path
):
    import main as entry_point

    class ForwardingBroker:
        def __init__(self, *args, **kwargs):
            pass

        def try_forward_or_listen(self, message):
            return False

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
