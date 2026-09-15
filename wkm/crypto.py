"""Authenticated encryption for the link.

Everything you type crosses the WiFi, passwords included, so the link is
AES-256-GCM from the first record. Both ends prove they know the shared
passphrase during the handshake; a wrong passphrase fails as an auth error
rather than a garbled session.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

NONCE_LEN = 32
CONFIRM = b"wkm-confirm-v1"

# scrypt cost. ~60ms once per connection, which is invisible at connect time
# and still expensive enough to make a captured handshake unpleasant to grind.
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1

# scrypt comes from `cryptography`, not hashlib. The python macOS ships is
# linked against LibreSSL, which does not expose scrypt at all, so
# hashlib.scrypt is simply missing there -- and that is the interpreter the
# Mac side is meant to run on. Both are the same standard KDF, so a Mac and a
# Windows machine still derive identical keys.


class AuthError(Exception):
    """Wrong passphrase, or a tampered/replayed frame."""


def _subkey(master: bytes, label: bytes) -> bytes:
    return hashlib.blake2b(master, key=label, digest_size=32).digest()


def derive_keys(passphrase: str, client_nonce: bytes, server_nonce: bytes) -> tuple[bytes, bytes]:
    """Return ``(client->server key, server->client key)``.

    Both nonces feed the salt, so two runs with the same passphrase never reuse
    a key and a recorded session cannot be replayed against a live one.
    """
    master = Scrypt(
        salt=client_nonce + server_nonce,
        length=32,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    ).derive(passphrase.encode("utf-8"))
    return _subkey(master, b"wkm-c2s"), _subkey(master, b"wkm-s2c")


def discovery_tag(passphrase: str) -> bytes:
    """Short public tag so discovery only answers our own pairs.

    Deliberately not secret-equivalent: it identifies a pair on the LAN, it
    does not authenticate one. The real check is the handshake.
    """
    return hmac.new(passphrase.encode("utf-8"), b"wkm-discovery-v1", hashlib.sha256).digest()[:8]


class Session:
    """One direction-pair of AES-GCM streams with implicit nonce counters.

    TCP already gives us ordering and reliability, so the counter never has to
    travel; a gap or reorder shows up as an auth failure and kills the link,
    which is the behaviour we want.
    """

    __slots__ = ("_tx", "_rx", "_ctr_tx", "_ctr_rx")

    def __init__(self, key_tx: bytes, key_rx: bytes) -> None:
        self._tx = AESGCM(key_tx)
        self._rx = AESGCM(key_rx)
        self._ctr_tx = 0
        self._ctr_rx = 0

    @staticmethod
    def _nonce(counter: int) -> bytes:
        return b"\x00\x00\x00\x00" + struct.pack("<Q", counter)

    def seal(self, plaintext: bytes) -> bytes:
        """Encrypt one record and prefix it with a 2-byte length."""
        ct = self._tx.encrypt(self._nonce(self._ctr_tx), plaintext, None)
        self._ctr_tx += 1
        return struct.pack(">H", len(ct)) + ct

    def open(self, ciphertext: bytes) -> bytes:
        try:
            pt = self._rx.decrypt(self._nonce(self._ctr_rx), ciphertext, None)
        except InvalidTag as exc:
            raise AuthError("frame failed authentication") from exc
        self._ctr_rx += 1
        return pt


def new_nonce() -> bytes:
    return os.urandom(NONCE_LEN)
