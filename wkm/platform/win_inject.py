"""Windows input injection via SendInput.

Used when Windows is the machine being driven. Keys go in by scancode rather
than virtual-key code so the physical-position mapping survives whatever
keyboard layout the target happens to have active.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .. import keymap, protocol

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

#: Stamped into dwExtraInfo on everything we inject, so our own capture hook
#: can recognise and ignore it instead of echoing it back over the link.
WKM_SIGNATURE = 0x57484D01

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

_BUTTON_FLAGS = {
    protocol.BTN_LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    protocol.BTN_RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    protocol.BTN_MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    protocol.BTN_X1: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    protocol.BTN_X2: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}

_MOD_BIT_TO_HID = {
    protocol.MOD_LCTRL: keymap.HID_LCTRL,
    protocol.MOD_LSHIFT: keymap.HID_LSHIFT,
    protocol.MOD_LALT: keymap.HID_LALT,
    protocol.MOD_LGUI: keymap.HID_LGUI,
    protocol.MOD_RCTRL: keymap.HID_RCTRL,
    protocol.MOD_RSHIFT: keymap.HID_RSHIFT,
    protocol.MOD_RALT: keymap.HID_RALT,
    protocol.MOD_RGUI: keymap.HID_RGUI,
}


class WindowsInjector:
    """Turns wkm records into real Windows input events."""

    def __init__(
        self,
        modifier_mode: str = "positional",
        pointer_speed: float = 1.0,
        scroll_speed: float = 1.0,
        natural_scroll: bool = False,
        **_ignored,
    ) -> None:
        self._pointer_speed = max(0.05, float(pointer_speed))
        self._scroll_speed = max(0.05, float(scroll_speed))
        self._natural = bool(natural_scroll)
        self._swap = modifier_mode == "swap_ctrl_cmd"
        self._held_keys: set[int] = set()
        self._held_buttons: set[int] = set()
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._scroll_acc_x = 0.0
        self._scroll_acc_y = 0.0

    # -- helpers ------------------------------------------------------------
    def _remap(self, hid: int) -> int:
        if not self._swap:
            return hid
        return {
            keymap.HID_LGUI: keymap.HID_LCTRL,
            keymap.HID_LCTRL: keymap.HID_LGUI,
            keymap.HID_RGUI: keymap.HID_RCTRL,
            keymap.HID_RCTRL: keymap.HID_RGUI,
        }.get(hid, hid)

    @staticmethod
    def _send(*inputs: INPUT) -> None:
        if not inputs:
            return
        array = (INPUT * len(inputs))(*inputs)
        sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _mouse(dx: int, dy: int, data: int, flags: int) -> INPUT:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(
            dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF, dwFlags=flags,
            time=0, dwExtraInfo=WKM_SIGNATURE,
        )
        return inp

    @staticmethod
    def _kbd(scan: int, flags: int) -> INPUT:
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(
            wVk=0, wScan=scan, dwFlags=flags | KEYEVENTF_SCANCODE,
            time=0, dwExtraInfo=WKM_SIGNATURE,
        )
        return inp

    # -- Injector protocol --------------------------------------------------
    def move(self, dx: int, dy: int) -> None:
        self._acc_x += dx * self._pointer_speed
        self._acc_y += dy * self._pointer_speed
        step_x = int(self._acc_x)
        step_y = int(self._acc_y)
        self._acc_x -= step_x
        self._acc_y -= step_y
        if step_x or step_y:
            self._send(self._mouse(step_x, step_y, 0, MOUSEEVENTF_MOVE))

    def button(self, btn: int, down: bool) -> None:
        spec = _BUTTON_FLAGS.get(btn)
        if spec is None:
            return
        down_flag, up_flag, data = spec
        if down:
            self._held_buttons.add(btn)
        else:
            self._held_buttons.discard(btn)
        self._send(self._mouse(0, 0, data, down_flag if down else up_flag))

    def scroll(self, dx: int, dy: int) -> None:
        self._scroll_acc_y += dy * self._scroll_speed
        self._scroll_acc_x += dx * self._scroll_speed
        step_y = int(self._scroll_acc_y)
        step_x = int(self._scroll_acc_x)
        self._scroll_acc_y -= step_y
        self._scroll_acc_x -= step_x
        if self._natural:
            step_x, step_y = -step_x, -step_y
        events = []
        if step_y:
            events.append(self._mouse(0, 0, step_y, MOUSEEVENTF_WHEEL))
        if step_x:
            events.append(self._mouse(0, 0, step_x, MOUSEEVENTF_HWHEEL))
        if events:
            self._send(*events)

    def key(self, hid: int, down: bool) -> None:
        hid = self._remap(hid)
        mapped = keymap.hid_to_win(hid)
        if mapped is None:
            return
        scan, extended = mapped
        if down:
            self._held_keys.add(hid)
        else:
            self._held_keys.discard(hid)
        flags = 0 if down else KEYEVENTF_KEYUP
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        self._send(self._kbd(scan, flags))

    def set_mods(self, mask: int) -> None:
        for bit, hid in _MOD_BIT_TO_HID.items():
            target = self._remap(hid)
            want = bool(mask & bit)
            if want != (target in self._held_keys):
                self.key(hid, want)

    def release_all(self) -> None:
        for hid in list(self._held_keys):
            self.key(hid, False)
        self._held_keys.clear()
        for btn in list(self._held_buttons):
            self.button(btn, False)
        self._held_buttons.clear()
        self._acc_x = self._acc_y = 0.0
        self._scroll_acc_x = self._scroll_acc_y = 0.0

    def close(self) -> None:
        self.release_all()
