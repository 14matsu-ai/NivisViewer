from __future__ import annotations

from .i18n import tr


import getpass
import hashlib
import json
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_PATHS = 100
MAX_PATH_LENGTH = 32768
HEADER_SIZE = 4


@dataclass(frozen=True)
class InstanceMessage:
    protocol_version: int = PROTOCOL_VERSION
    paths: tuple[str, ...] = ()
    new_window: bool = False
    reuse: bool = False
    browser_only: bool = False
    no_restore: bool = False
    sender_pid: int = 0


def make_server_name(
    executable_dir: str | Path,
    *,
    profile_dir: str | Path | None = None,
    user_identity: str | None = None,
    protocol_version: int = PROTOCOL_VERSION,
) -> str:
    user = user_identity if user_identity is not None else getpass.getuser()
    user_hash = hashlib.sha256(user.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    location = _normalized_location(executable_dir)
    if profile_dir is not None:
        location += "\0" + _normalized_location(profile_dir)
    location_hash = hashlib.sha256(
        location.encode("utf-8", "surrogatepass")
    ).hexdigest()[:16]
    return f"NivisViewer-v{protocol_version}-{user_hash}-{location_hash}"


def encode_message(message: InstanceMessage) -> bytes:
    validate_message(message)
    payload = json.dumps(
        {**asdict(message), "paths": list(message.paths)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("IPC message is too large")
    return struct.pack(">I", len(payload)) + payload


def decode_message(frame: bytes) -> InstanceMessage:
    if len(frame) < HEADER_SIZE:
        raise ValueError("IPC message header is incomplete")
    size = struct.unpack(">I", frame[:HEADER_SIZE])[0]
    if size > MAX_MESSAGE_BYTES:
        raise ValueError("IPC message is too large")
    if len(frame) != HEADER_SIZE + size:
        raise ValueError("IPC message length mismatch")
    try:
        data = json.loads(frame[HEADER_SIZE:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("IPC message is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("IPC message must be an object")
    paths = data.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("IPC paths must be a string array")
    message = InstanceMessage(
        protocol_version=data.get("protocol_version"),
        paths=tuple(paths),
        new_window=data.get("new_window", False),
        reuse=data.get("reuse", False),
        browser_only=data.get("browser_only", False),
        no_restore=data.get("no_restore", False),
        sender_pid=data.get("sender_pid", 0),
    )
    validate_message(message)
    return message


def validate_message(message: InstanceMessage) -> None:
    if type(message.protocol_version) is not int or message.protocol_version != PROTOCOL_VERSION:
        raise ValueError("Unsupported IPC protocol version")
    if len(message.paths) > MAX_PATHS:
        raise ValueError("Too many IPC paths")
    if any(not isinstance(path, str) or len(path) > MAX_PATH_LENGTH for path in message.paths):
        raise ValueError("Invalid IPC path")
    if any(
        type(value) is not bool
        for value in (
            message.new_window,
            message.reuse,
            message.browser_only,
            message.no_restore,
        )
    ):
        raise ValueError("IPC flags must be booleans")
    if message.new_window and message.reuse:
        raise ValueError("IPC open modes are mutually exclusive")
    if type(message.sender_pid) is not int or message.sender_pid < 0:
        raise ValueError("Invalid sender PID")


class SingleInstanceBroker(QObject):
    open_request_received = Signal(object)

    def __init__(
        self,
        server_name: str,
        parent: QObject | None = None,
        *,
        timeout_msecs: int = 1500,
    ) -> None:
        super().__init__(parent)
        self.server_name = server_name
        self.timeout_msecs = max(100, min(5000, int(timeout_msecs)))
        self.server = QLocalServer(self)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self._listening = False
        try:
            option = QLocalServer.SocketOption.UserAccessOption
            self.server.setSocketOptions(option)
        except (AttributeError, TypeError):
            pass
        self.server.newConnection.connect(self._accept_connections)

    @property
    def is_listening(self) -> bool:
        return self._listening and self.server.isListening()

    def try_forward_or_listen(self, message: InstanceMessage) -> bool:
        validate_message(message)
        if self._forward(message):
            return False
        if self._listen():
            return True
        if self._forward(message):
            return False
        # Only remove a stale endpoint after two failed connection attempts.
        QLocalServer.removeServer(self.server_name)
        if self._listen():
            return True
        if self._forward(message):
            return False
        raise RuntimeError(
            tr('単一インスタンス用サーバーを開始できません: {p0}', p0=self.server.errorString())
        )

    def close(self) -> None:
        for socket in tuple(self._buffers):
            socket.abort()
        self._buffers.clear()
        if self.server.isListening():
            self.server.close()
        self._listening = False

    def _listen(self) -> bool:
        if self.server.listen(self.server_name):
            self._listening = True
            return True
        return False

    def _forward(self, message: InstanceMessage) -> bool:
        socket = QLocalSocket()
        try:
            socket.connectToServer(self.server_name)
            if not socket.waitForConnected(self.timeout_msecs):
                return False
            socket.write(encode_message(message))
            if not socket.waitForBytesWritten(self.timeout_msecs):
                return False
            if not socket.waitForReadyRead(self.timeout_msecs):
                return False
            response = bytes(socket.readAll())
            try:
                decoded = json.loads(response.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False
            return isinstance(decoded, dict) and decoded.get("status") == "ok"
        finally:
            socket.disconnectFromServer()

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(
                lambda socket=socket: self._read_from_socket(socket)
            )
            socket.disconnected.connect(
                lambda socket=socket: self._buffers.pop(socket, None)
            )

    def _read_from_socket(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) < HEADER_SIZE:
            return
        expected = struct.unpack(">I", buffer[:HEADER_SIZE])[0]
        if expected > MAX_MESSAGE_BYTES:
            self._reply(socket, "error", "message_too_large")
            return
        if len(buffer) < HEADER_SIZE + expected:
            return
        frame = bytes(buffer[: HEADER_SIZE + expected])
        try:
            message = decode_message(frame)
        except ValueError as exc:
            self._reply(socket, "error", str(exc))
            return
        self.open_request_received.emit(message)
        self._reply(socket, "ok")

    def _reply(
        self,
        socket: QLocalSocket,
        status: str,
        error: str | None = None,
    ) -> None:
        response = {"status": status}
        if error:
            response["error"] = error
        socket.write(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        socket.flush()
        socket.waitForBytesWritten(min(250, self.timeout_msecs))
        socket.disconnectFromServer()
        self._buffers.pop(socket, None)


def _normalized_location(path: str | Path) -> str:
    return os.path.normcase(
        os.path.abspath(os.path.normpath(os.fspath(path)))
    ).casefold()
