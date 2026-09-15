"""Windows input capture.

Two mechanisms working together, because neither is sufficient alone:

* Low-level hooks (WH_KEYBOARD_LL / WH_MOUSE_LL) *swallow* input so Windows
  never sees it while control is on the other machine. They also give us
  buttons and wheel, and they are where the toggle hotkey is detected.
* Raw Input gives true relative pointer deltas. The hook cannot: it reports a
  proposed cursor position, and the system clamps that to the desktop, so a
  suppressed cursor sitting against a screen edge silently loses every bit of
  motion heading further that way.

A hook callback runs on the input path and Windows silently uninstalls a hook
that takes too long, so callbacks here only ever translate and enqueue.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .. import keymap, protocol
from .win_inject import (
    INPUT,
    INPUT_KEYBOARD,
    KEYBDINPUT,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE,
    ULONG_PTR,
    WKM_SIGNATURE,
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_QUIT = 0x0012
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80
LLMHF_INJECTED = 0x01

HWND_MESSAGE = -3
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x01

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK_NONAME = 0xFC


# --------------------------------------------------------------------------
# structures
# --------------------------------------------------------------------------
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _RAWMOUSE_UNION(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", _RAWMOUSE_BUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_UNION),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Declaring restype/argtypes is not optional here. ctypes defaults a return
# value to C int, which silently truncates a 64-bit HANDLE to 32 bits, and the
# resulting garbage handle fails with "the specified module could not be found".
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.argtypes = ()
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = (
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.restype = LRESULT
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetRawInputData.argtypes = (
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT), wintypes.UINT,
)
user32.GetRawInputData.restype = wintypes.UINT
user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
user32.CreateWindowExW.restype = wintypes.HWND
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_MOUSE_BUTTON_MSGS = {
    WM_LBUTTONDOWN: (protocol.BTN_LEFT, True),
    WM_LBUTTONUP: (protocol.BTN_LEFT, False),
    WM_RBUTTONDOWN: (protocol.BTN_RIGHT, True),
    WM_RBUTTONUP: (protocol.BTN_RIGHT, False),
    WM_MBUTTONDOWN: (protocol.BTN_MIDDLE, True),
    WM_MBUTTONUP: (protocol.BTN_MIDDLE, False),
}

#: Hook events must fall back to hook-derived deltas only after enough
#: movement has gone by with the raw stream staying silent.
_RAW_PROBE_EVENTS = 24


def set_dpi_aware() -> None:
    """Report real pixels from GetCursorPos on a scaled display."""
    try:
        # Per-monitor v2 if available, so mixed-DPI setups stay honest.
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


class WindowsCapturer:
    """Swallows local input and reports it as wkm events.

    ``sink(kind, *args)`` is called from the input thread and must not block.
    """

    def __init__(
        self,
        sink,
        hotkey: str = "ctrl+alt+m",
        panic_taps: int = 3,
        mouse_source: str = "auto",
        log=None,
    ) -> None:
        self._sink = sink
        self._hotkey_mask, self._hotkey_hid = keymap.parse_hotkey(hotkey)
        self._panic_taps = max(0, int(panic_taps))
        self._mode = mouse_source
        self._log = log or (lambda *a, **k: None)

        self._captured = False
        self._mods = 0
        self._down: set[int] = set()

        self._thread_id = 0
        self._hwnd = None
        self._wndclass = None
        self._kb_hook = None
        self._mouse_hook = None
        self._running = False

        # Keep ctypes trampolines alive for the process lifetime; if Python
        # collects one, Windows calls into freed memory.
        self._kb_proc = HOOKPROC(self._on_keyboard)
        self._mouse_proc = HOOKPROC(self._on_mouse)
        self._wnd_proc = WNDPROC(self._on_message)

        self._use_raw = mouse_source in ("auto", "raw")
        self._raw_seen = 0
        self._hook_moves = 0

        self._anchor = wintypes.POINT(0, 0)
        self._restore = wintypes.POINT(0, 0)
        self._panic_times: list[float] = []

    # ----------------------------------------------------------------- state
    @property
    def captured(self) -> bool:
        return self._captured

    @property
    def mods(self) -> int:
        """Modifier mask currently held down, for resyncing the remote end."""
        return self._mods

    def set_captured(self, captured: bool) -> None:
        """Flip capture state. Safe to call from any thread."""
        captured = bool(captured)
        if captured == self._captured:
            return
        if captured:
            self._enter_capture()
        else:
            self._leave_capture()

    def _enter_capture(self) -> None:
        user32.GetCursorPos(ctypes.byref(self._restore))
        # Park the cursor mid-desktop. It is frozen while captured anyway, and
        # centring keeps hook-derived deltas away from the edge clamp.
        cx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN) + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) // 2
        cy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN) + user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) // 2
        user32.SetCursorPos(cx, cy)
        self._anchor = wintypes.POINT(cx, cy)
        self._raw_seen = 0
        self._hook_moves = 0
        self._captured = True

    def _leave_capture(self) -> None:
        self._captured = False
        user32.SetCursorPos(self._restore.x, self._restore.y)

    def sync_local_modifiers(self) -> None:
        """Release modifiers Windows still thinks are held.

        The hotkey's own Ctrl/Alt reached Windows normally, but their release
        is about to be swallowed by capture, so they would stay stuck down for
        as long as capture lasts. Call this from a worker thread, never from
        inside a hook callback: SendInput on the input path re-enters the very
        hook that is still running.
        """
        events = []
        alt_held = any(h in self._down for h in (keymap.HID_LALT, keymap.HID_RALT))
        if alt_held:
            # A lone Alt release pops the window menu in classic apps. A
            # VK_NONAME tap in between is the documented way to defuse it.
            events.append(_vk_input(VK_NONAME, False))
            events.append(_vk_input(VK_NONAME, True))
        for hid in list(self._down):
            if keymap.mod_bit(hid) == 0:
                continue
            mapped = keymap.hid_to_win(hid)
            if mapped is None:
                continue
            scan, extended = mapped
            flags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
            if extended:
                flags |= KEYEVENTF_EXTENDEDKEY
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=WKM_SIGNATURE)
            events.append(inp)
        if events:
            array = (INPUT * len(events))(*events)
            user32.SendInput(len(events), array, ctypes.sizeof(INPUT))

    # ------------------------------------------------------------- callbacks
    def _on_keyboard(self, ncode, wparam, lparam):
        if ncode != HC_ACTION:
            return user32.CallNextHookEx(None, ncode, wparam, lparam)
        info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents

        # Never eat our own injected events, and never echo anyone else's.
        if info.dwExtraInfo == WKM_SIGNATURE or (info.flags & LLKHF_INJECTED):
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        hid = keymap.win_to_hid(info.scanCode, bool(info.flags & LLKHF_EXTENDED), info.vkCode)
        if hid is None:
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        bit = keymap.mod_bit(hid)
        repeat = down and hid in self._down
        if down:
            self._down.add(hid)
            self._mods |= bit
        else:
            self._down.discard(hid)
            self._mods &= ~bit

        if down and not repeat:
            if keymap.hotkey_matches(self._hotkey_mask, self._hotkey_hid, hid, self._mods):
                self._sink("toggle")
                return 1
            if self._panic_taps and hid == keymap.HID_RCTRL and self._check_panic():
                self._sink("panic")
                return 1

        if not self._captured:
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        self._sink("key", hid, down)
        return 1

    def _check_panic(self) -> bool:
        now = time.monotonic()
        self._panic_times = [t for t in self._panic_times if now - t < 0.7]
        self._panic_times.append(now)
        if len(self._panic_times) >= self._panic_taps:
            self._panic_times.clear()
            return True
        return False

    def _on_mouse(self, ncode, wparam, lparam):
        if ncode != HC_ACTION:
            return user32.CallNextHookEx(None, ncode, wparam, lparam)
        info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents

        if info.dwExtraInfo == WKM_SIGNATURE or (info.flags & LLMHF_INJECTED):
            return user32.CallNextHookEx(None, ncode, wparam, lparam)
        if not self._captured:
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        msg = wparam
        if msg == WM_MOUSEMOVE:
            self._hook_moves += 1
            if self._use_raw and self._mode == "auto" and self._hook_moves >= _RAW_PROBE_EVENTS:
                if self._raw_seen == 0:
                    # This pointer does not feed the raw mouse stream.
                    self._use_raw = False
                    self._log("no raw pointer stream; falling back to hook deltas")
                self._hook_moves = 0
            if not self._use_raw:
                dx = info.pt.x - self._anchor.x
                dy = info.pt.y - self._anchor.y
                if dx or dy:
                    self._sink("move", dx, dy)
            return 1

        button = _MOUSE_BUTTON_MSGS.get(msg)
        if button is not None:
            self._sink("button", button[0], button[1])
            return 1

        if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            which = (info.mouseData >> 16) & 0xFFFF
            btn = protocol.BTN_X1 if which == 1 else protocol.BTN_X2
            self._sink("button", btn, msg == WM_XBUTTONDOWN)
            return 1

        if msg == WM_MOUSEWHEEL:
            self._sink("scroll", 0, _signed16(info.mouseData >> 16))
            return 1
        if msg == WM_MOUSEHWHEEL:
            self._sink("scroll", _signed16(info.mouseData >> 16), 0)
            return 1

        return 1

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT and self._captured and self._use_raw:
            self._read_raw(lparam)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _read_raw(self, lparam) -> None:
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        if user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size) != 0:
            return
        if size.value == 0 or size.value > ctypes.sizeof(RAWINPUT) * 4:
            return
        buf = ctypes.create_string_buffer(size.value)
        got = user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), header_size)
        if got != size.value:
            return
        raw = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType != RIM_TYPEMOUSE:
            return
        if raw.mouse.usFlags & MOUSE_MOVE_ABSOLUTE:
            # Tablets and some VM pointers report absolute positions, which
            # carry no usable delta. Those fall back to the hook.
            return
        dx, dy = raw.mouse.lLastX, raw.mouse.lLastY
        if dx or dy:
            self._raw_seen += 1
            self._sink("move", dx, dy)

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        set_dpi_aware()
        self._thread_id = kernel32.GetCurrentThreadId()
        hinst = kernel32.GetModuleHandleW(None)

        # Held on the instance, not a local: the struct owns the only
        # reference to the class-name buffer the window keeps pointing at.
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._wnd_proc
        cls.hInstance = hinst
        cls.lpszClassName = "wkmCaptureWindow"
        self._wndclass = cls
        if not user32.RegisterClassW(ctypes.byref(cls)):
            err = ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(err)

        self._hwnd = user32.CreateWindowExW(
            0, "wkmCaptureWindow", "wkm", 0, 0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, hinst, None,
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        if self._use_raw:
            rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, wintypes.HWND(self._hwnd))
            if not user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                self._log("raw input unavailable; using hook deltas")
                self._use_raw = False

        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hinst, 0)
        if not self._kb_hook:
            raise ctypes.WinError(ctypes.get_last_error())
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hinst, 0)
        if not self._mouse_hook:
            err = ctypes.get_last_error()
            user32.UnhookWindowsHookEx(self._kb_hook)
            raise ctypes.WinError(err)

        self._running = True
        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._teardown()

    def _teardown(self) -> None:
        self._running = False
        if self._captured:
            self._leave_capture()
        for hook in (self._kb_hook, self._mouse_hook):
            if hook:
                user32.UnhookWindowsHookEx(hook)
        self._kb_hook = self._mouse_hook = None
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    def stop(self) -> None:
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


def _vk_input(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(
        wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0,
        time=0, dwExtraInfo=WKM_SIGNATURE,
    )
    return inp


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value
