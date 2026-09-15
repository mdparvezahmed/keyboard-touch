"""Link setup: handshake, framing, and a background reader.

The link is plain TCP with Nagle disabled. Pointer motion is latency-critical
and tiny, so buffering it to fill a segment is exactly the wrong trade.
"""

from __future__ import annotations

import socket
import struct
import sys
import threading

from . import crypto, protocol

HANDSHAKE_TIMEOUT = 10.0
_HELLO = struct.Struct("<4sBB")  # magic, version, role
ROLE_SOURCE = 1
ROLE_TARGET = 2


class LinkError(Exception):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            raise LinkError("connection closed by peer")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def tune(sock: socket.socket) -> None:
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)


class Link:
    """An authenticated, encrypted, message-oriented connection."""

    def __init__(self, sock: socket.socket, session: crypto.Session, peer: str) -> None:
        self.sock = sock
        self.session = session
        self.peer = peer
        self._send_lock = threading.Lock()
        self._closed = False
        self._buf = bytearray()

    # -- framing ------------------------------------------------------------
    def send(self, record: bytes) -> None:
        frame = self.session.seal(record)
        with self._send_lock:
            if self._closed:
                raise LinkError("link is closed")
            self.sock.sendall(frame)

    def send_many(self, records: list[bytes]) -> None:
        """Seal several records and push them in one syscall.

        Each record keeps its own nonce and tag; coalescing here only saves the
        write, never the authentication.
        """
        if not records:
            return
        frames = b"".join(self.session.seal(r) for r in records)
        with self._send_lock:
            if self._closed:
                raise LinkError("link is closed")
            self.sock.sendall(frames)

    def recv(self) -> bytes:
        """Return the next record, or raise on timeout at a frame boundary.

        Reads are buffered rather than read-exactly, because the target sets a
        socket timeout to notice a dead peer. A timeout landing between a
        frame's length prefix and its body would otherwise consume half a
        frame and desync the stream permanently; here the partial data simply
        stays in the buffer until the rest arrives.
        """
        while True:
            if len(self._buf) >= 2:
                length = int.from_bytes(self._buf[:2], "big")
                if length == 0 or length > 4096:
                    raise LinkError("implausible frame length " + str(length))
                if len(self._buf) >= 2 + length:
                    frame = bytes(self._buf[2 : 2 + length])
                    del self._buf[: 2 + length]
                    return self.session.open(frame)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise LinkError("connection closed by peer")
            self._buf.extend(chunk)

    def close(self) -> None:
        with self._send_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    @property
    def closed(self) -> bool:
        return self._closed


def _handshake(sock: socket.socket, passphrase: str, *, is_client: bool, role: int) -> Link:
    """Exchange nonces, derive per-direction keys, prove the shared secret.

    Both sides send a nonce in the clear, derive keys from the passphrase plus
    both nonces, then each encrypts a fixed token. A wrong passphrase fails the
    AEAD tag here rather than producing a session that silently misbehaves.
    """
    sock.settimeout(HANDSHAKE_TIMEOUT)
    my_nonce = crypto.new_nonce()
    sock.sendall(_HELLO.pack(protocol.MAGIC, protocol.VERSION, role) + my_nonce)

    head = _recv_exact(sock, _HELLO.size)
    magic, version, peer_role = _HELLO.unpack(head)
    if magic != protocol.MAGIC:
        raise LinkError("peer is not a wkm endpoint")
    if version != protocol.VERSION:
        raise LinkError(
            "protocol version mismatch: this end speaks v"
            + str(protocol.VERSION)
            + ", peer speaks v"
            + str(version)
            + ". Update wkm on both machines."
        )
    if peer_role == role:
        raise LinkError("both ends are running in the same role")
    peer_nonce = _recv_exact(sock, crypto.NONCE_LEN)

    client_nonce = my_nonce if is_client else peer_nonce
    server_nonce = peer_nonce if is_client else my_nonce
    k_c2s, k_s2c = crypto.derive_keys(passphrase, client_nonce, server_nonce)
    session = crypto.Session(*( (k_c2s, k_s2c) if is_client else (k_s2c, k_c2s) ))

    # Prove possession of the passphrase in both directions before any real
    # input crosses the wire.
    sock.sendall(session.seal(crypto.CONFIRM))
    header = _recv_exact(sock, 2)
    (length,) = struct.unpack(">H", header)
    if length > 256:
        raise LinkError("malformed confirmation")
    try:
        token = session.open(_recv_exact(sock, length))
    except crypto.AuthError:
        raise LinkError("passphrase mismatch: the two machines are not using the same secret")
    if token != crypto.CONFIRM:
        raise LinkError("peer failed confirmation")

    sock.settimeout(None)
    tune(sock)
    peer_name = "%s:%d" % sock.getpeername()[:2]
    return Link(sock, session, peer_name)


def connect(host: str, port: int, passphrase: str, role: int = ROLE_SOURCE) -> Link:
    sock = socket.create_connection((host, port), timeout=HANDSHAKE_TIMEOUT)
    tune(sock)
    try:
        return _handshake(sock, passphrase, is_client=True, role=role)
    except Exception:
        sock.close()
        raise


def listen(bind: str, port: int) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        # Windows SO_REUSEADDR is not the POSIX one: it lets a second process
        # bind a port that is already being listened on, after which incoming
        # connections go to whichever socket the stack feels like. A duplicate
        # target must fail loudly here instead of silently stealing sessions.
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, port))
    srv.listen(1)
    return srv


def accept(srv: socket.socket, passphrase: str, role: int = ROLE_TARGET) -> Link:
    sock, _addr = srv.accept()
    tune(sock)
    try:
        return _handshake(sock, passphrase, is_client=False, role=role)
    except Exception:
        sock.close()
        raise
