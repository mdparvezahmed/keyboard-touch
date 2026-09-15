"""Binary wire protocol.

Every message is a small fixed-size record so a mouse move costs 5 bytes on the
wire instead of a JSON object. Records are sealed by :mod:`wkm.crypto` before
they hit the socket.
"""

from __future__ import annotations

import struct

MAGIC = b"WKM1"
VERSION = 1

# --- message types ---------------------------------------------------------
T_MOVE = 0x01      # relative pointer motion
T_BUTTON = 0x02    # pointer button up/down
T_SCROLL = 0x03    # wheel / two-finger scroll
T_KEY = 0x04       # key up/down, by HID usage
T_MODS = 0x05      # authoritative modifier resync
T_ENTER = 0x06     # control handed to this machine
T_LEAVE = 0x07     # control taken back; release everything
T_PING = 0x08
T_PONG = 0x09
T_CLIP = 0x0A      # reserved: clipboard text

# --- pointer buttons -------------------------------------------------------
BTN_LEFT = 1
BTN_RIGHT = 2
BTN_MIDDLE = 3
BTN_X1 = 4
BTN_X2 = 5

# --- modifier bits (position-preserving, left/right distinct) ---------------
MOD_LCTRL = 1 << 0
MOD_LSHIFT = 1 << 1
MOD_LALT = 1 << 2
MOD_LGUI = 1 << 3
MOD_RCTRL = 1 << 4
MOD_RSHIFT = 1 << 5
MOD_RALT = 1 << 6
MOD_RGUI = 1 << 7

MOD_CTRL = MOD_LCTRL | MOD_RCTRL
MOD_SHIFT = MOD_LSHIFT | MOD_RSHIFT
MOD_ALT = MOD_LALT | MOD_RALT
MOD_GUI = MOD_LGUI | MOD_RGUI

#: One wheel notch, matching the Windows ``WHEEL_DELTA`` convention. Precision
#: touchpads emit fractions of this, which is why scroll is not sent in lines.
WHEEL_DELTA = 120

_MOVE = struct.Struct("<Bhh")
_BUTTON = struct.Struct("<BBB")
_SCROLL = struct.Struct("<Bhh")
_KEY = struct.Struct("<BBH")
_MODS = struct.Struct("<BB")
_BARE = struct.Struct("<B")


def _i16(v: int) -> int:
    return -32768 if v < -32768 else (32767 if v > 32767 else int(v))


def move(dx: int, dy: int) -> bytes:
    return _MOVE.pack(T_MOVE, _i16(dx), _i16(dy))


def button(btn: int, down: bool) -> bytes:
    return _BUTTON.pack(T_BUTTON, btn, 1 if down else 0)


def scroll(dx: int, dy: int) -> bytes:
    """Scroll in WHEEL_DELTA units; +dy scrolls away from the user."""
    return _SCROLL.pack(T_SCROLL, _i16(dx), _i16(dy))


def key(hid: int, down: bool) -> bytes:
    return _KEY.pack(T_KEY, 1 if down else 0, hid)


def mods(mask: int) -> bytes:
    return _MODS.pack(T_MODS, mask & 0xFF)


def bare(kind: int) -> bytes:
    return _BARE.pack(kind)


enter = lambda: bare(T_ENTER)      # noqa: E731
leave = lambda: bare(T_LEAVE)      # noqa: E731
ping = lambda: bare(T_PING)        # noqa: E731
pong = lambda: bare(T_PONG)        # noqa: E731


def clip(text: str) -> bytes:
    return bytes([T_CLIP]) + text.encode("utf-8")


class ProtocolError(Exception):
    pass


def parse(buf: bytes) -> tuple:
    """Decode one record into a ``(name, *args)`` tuple."""
    if not buf:
        raise ProtocolError("empty record")
    t = buf[0]
    try:
        if t == T_MOVE:
            _, dx, dy = _MOVE.unpack(buf)
            return ("move", dx, dy)
        if t == T_BUTTON:
            _, btn, down = _BUTTON.unpack(buf)
            return ("button", btn, bool(down))
        if t == T_SCROLL:
            _, dx, dy = _SCROLL.unpack(buf)
            return ("scroll", dx, dy)
        if t == T_KEY:
            _, down, hid = _KEY.unpack(buf)
            return ("key", hid, bool(down))
        if t == T_MODS:
            _, mask = _MODS.unpack(buf)
            return ("mods", mask)
        if t == T_ENTER:
            return ("enter",)
        if t == T_LEAVE:
            return ("leave",)
        if t == T_PING:
            return ("ping",)
        if t == T_PONG:
            return ("pong",)
        if t == T_CLIP:
            return ("clip", buf[1:].decode("utf-8", "replace"))
    except struct.error as exc:
        raise ProtocolError(f"malformed record type 0x{t:02x}") from exc
    raise ProtocolError(f"unknown record type 0x{t:02x}")
