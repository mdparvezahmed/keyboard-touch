"""macOS input capture via a CoreGraphics event tap.

Needs both Accessibility and Input Monitoring permission. An event tap created
without them either fails outright or gets disabled the moment it tries to
swallow something, so the failure is reported loudly rather than as a pointer
that mysteriously keeps working locally.
"""

from __future__ import annotations

import threading
import time

import Quartz

try:
    # Normally from pyobjc-framework-Cocoa, which Quartz depends on. Some
    # pyobjc layouts re-export these through Quartz instead, so fall back
    # rather than failing at import time on the user's first run.
    from CoreFoundation import (
        CFMachPortCreateRunLoopSource,
        CFRunLoopAddSource,
        CFRunLoopGetCurrent,
        CFRunLoopRun,
        CFRunLoopStop,
        kCFRunLoopCommonModes,
    )
except ImportError:  # pragma: no cover - depends on the pyobjc build
    CFMachPortCreateRunLoopSource = Quartz.CFMachPortCreateRunLoopSource
    CFRunLoopAddSource = Quartz.CFRunLoopAddSource
    CFRunLoopGetCurrent = Quartz.CFRunLoopGetCurrent
    CFRunLoopRun = Quartz.CFRunLoopRun
    CFRunLoopStop = Quartz.CFRunLoopStop
    kCFRunLoopCommonModes = Quartz.kCFRunLoopCommonModes

from .. import keymap, protocol

_LINES_PER_NOTCH = 3.0

_BUTTON_EVENTS = {
    Quartz.kCGEventLeftMouseDown: (protocol.BTN_LEFT, True),
    Quartz.kCGEventLeftMouseUp: (protocol.BTN_LEFT, False),
    Quartz.kCGEventRightMouseDown: (protocol.BTN_RIGHT, True),
    Quartz.kCGEventRightMouseUp: (protocol.BTN_RIGHT, False),
}

_MOVE_EVENTS = frozenset(
    {
        Quartz.kCGEventMouseMoved,
        Quartz.kCGEventLeftMouseDragged,
        Quartz.kCGEventRightMouseDragged,
        Quartz.kCGEventOtherMouseDragged,
    }
)

_OTHER_BUTTON = {3: protocol.BTN_X1, 4: protocol.BTN_X2}

_FLAG_FOR_HID = {
    keymap.HID_LSHIFT: Quartz.kCGEventFlagMaskShift,
    keymap.HID_RSHIFT: Quartz.kCGEventFlagMaskShift,
    keymap.HID_LCTRL: Quartz.kCGEventFlagMaskControl,
    keymap.HID_RCTRL: Quartz.kCGEventFlagMaskControl,
    keymap.HID_LALT: Quartz.kCGEventFlagMaskAlternate,
    keymap.HID_RALT: Quartz.kCGEventFlagMaskAlternate,
    keymap.HID_LGUI: Quartz.kCGEventFlagMaskCommand,
    keymap.HID_RGUI: Quartz.kCGEventFlagMaskCommand,
}

_NX_DEVICE_BIT = {
    keymap.HID_LCTRL: 0x00000001,
    keymap.HID_LSHIFT: 0x00000002,
    keymap.HID_RSHIFT: 0x00000004,
    keymap.HID_LGUI: 0x00000008,
    keymap.HID_RGUI: 0x00000010,
    keymap.HID_LALT: 0x00000020,
    keymap.HID_RALT: 0x00000040,
    keymap.HID_RCTRL: 0x00002000,
}


class PermissionError_(Exception):
    """Raised when macOS refuses to give us an event tap."""


def _event_mask(*types: int) -> int:
    mask = 0
    for t in types:
        mask |= 1 << t
    return mask


class MacCapturer:
    """Swallows local input on macOS and reports it as wkm events."""

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
        self._log = log or (lambda *a, **k: None)

        self._captured = False
        self._mods = 0
        self._down: set[int] = set()
        self._panic_times: list[float] = []
        self._scroll_acc_x = 0.0
        self._scroll_acc_y = 0.0

        self._tap = None
        self._cb = None
        self._runloop = None
        self._stopped = threading.Event()

    @property
    def captured(self) -> bool:
        return self._captured

    @property
    def mods(self) -> int:
        """Modifier mask currently held down, for resyncing the remote end."""
        return self._mods

    def set_captured(self, captured: bool) -> None:
        captured = bool(captured)
        if captured == self._captured:
            return
        self._captured = captured

    def sync_local_modifiers(self) -> None:
        """Drop any modifier the system still believes is held.

        The hotkey's modifiers were delivered normally, but their release is
        about to be swallowed, so tell the system they are up already. Called
        from a worker thread, matching the Windows backend.
        """
        event = Quartz.CGEventCreate(None)
        Quartz.CGEventSetType(event, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(event, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    # ------------------------------------------------------------- callbacks
    def _callback(self, proxy, etype, event, refcon):
        # A tap that blocks for too long gets switched off; re-arm it rather
        # than silently losing the keyboard.
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
                self._log("event tap was disabled by the system; re-enabled")
            return event

        try:
            if etype in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                return self._on_key(etype, event)
            if etype == Quartz.kCGEventFlagsChanged:
                return self._on_flags(event)
            if not self._captured:
                return event
            if etype in _MOVE_EVENTS:
                dx = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaX)
                dy = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaY)
                if dx or dy:
                    self._sink("move", int(dx), int(dy))
                return None
            if etype in _BUTTON_EVENTS:
                btn, down = _BUTTON_EVENTS[etype]
                self._sink("button", btn, down)
                return None
            if etype in (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp):
                number = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGMouseEventButtonNumber
                )
                btn = protocol.BTN_MIDDLE if number == 2 else _OTHER_BUTTON.get(int(number))
                if btn is not None:
                    self._sink("button", btn, etype == Quartz.kCGEventOtherMouseDown)
                return None
            if etype == Quartz.kCGEventScrollWheel:
                self._on_scroll(event)
                return None
        except Exception as exc:  # never let a callback exception kill the tap
            self._log("capture callback error: " + repr(exc))
            return event
        return event

    def _on_scroll(self, event) -> None:
        # Fixed-point deltas keep trackpad momentum smooth; the integer field
        # rounds sub-line scrolling away to nothing.
        dy = Quartz.CGEventGetDoubleValueField(
            event, Quartz.kCGScrollWheelEventFixedPtDeltaAxis1
        )
        dx = Quartz.CGEventGetDoubleValueField(
            event, Quartz.kCGScrollWheelEventFixedPtDeltaAxis2
        )
        self._scroll_acc_y += dy * protocol.WHEEL_DELTA / _LINES_PER_NOTCH
        self._scroll_acc_x += dx * protocol.WHEEL_DELTA / _LINES_PER_NOTCH
        step_y = int(self._scroll_acc_y)
        step_x = int(self._scroll_acc_x)
        self._scroll_acc_y -= step_y
        self._scroll_acc_x -= step_x
        if step_x or step_y:
            self._sink("scroll", step_x, step_y)

    def _on_key(self, etype, event):
        keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        hid = keymap.mac_to_hid(keycode)
        if hid is None:
            return event
        down = etype == Quartz.kCGEventKeyDown
        repeat = bool(
            Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
        )
        if down:
            self._down.add(hid)
        else:
            self._down.discard(hid)

        if down and not repeat:
            if keymap.hotkey_matches(self._hotkey_mask, self._hotkey_hid, hid, self._mods):
                self._sink("toggle")
                return None
        if not self._captured:
            return event
        self._sink("key", hid, down)
        return None

    def _on_flags(self, event):
        """Modifiers arrive as a new flag mask, not as up/down events."""
        keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        hid = keymap.mac_to_hid(keycode)
        if hid is None or hid not in _FLAG_FOR_HID:
            return event
        flags = Quartz.CGEventGetFlags(event)
        device_bit = _NX_DEVICE_BIT.get(hid, 0)
        down = bool(flags & device_bit) if device_bit else bool(flags & _FLAG_FOR_HID[hid])

        bit = keymap.mod_bit(hid)
        if down:
            self._down.add(hid)
            self._mods |= bit
        else:
            self._down.discard(hid)
            self._mods &= ~bit

        if down and self._panic_taps and hid == keymap.HID_RCTRL and self._check_panic():
            self._sink("panic")
            return None

        if not self._captured:
            return event
        self._sink("key", hid, down)
        return None

    def _check_panic(self) -> bool:
        now = time.monotonic()
        self._panic_times = [t for t in self._panic_times if now - t < 0.7]
        self._panic_times.append(now)
        if len(self._panic_times) >= self._panic_taps:
            self._panic_times.clear()
            return True
        return False

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        mask = _event_mask(
            Quartz.kCGEventKeyDown,
            Quartz.kCGEventKeyUp,
            Quartz.kCGEventFlagsChanged,
            Quartz.kCGEventMouseMoved,
            Quartz.kCGEventLeftMouseDown,
            Quartz.kCGEventLeftMouseUp,
            Quartz.kCGEventRightMouseDown,
            Quartz.kCGEventRightMouseUp,
            Quartz.kCGEventOtherMouseDown,
            Quartz.kCGEventOtherMouseUp,
            Quartz.kCGEventLeftMouseDragged,
            Quartz.kCGEventRightMouseDragged,
            Quartz.kCGEventOtherMouseDragged,
            Quartz.kCGEventScrollWheel,
        )
        # Hold the bound method on the instance: a fresh bound-method object is
        # created on every attribute access, and the tap must not be left
        # holding the only reference to one that can be collected.
        self._cb = self._callback
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            self._cb,
            None,
        )
        if tap is None:
            raise PermissionError_(
                "macOS refused the event tap. Grant this program Accessibility "
                "AND Input Monitoring in System Settings -> Privacy & Security, "
                "then run it again."
            )
        self._tap = tap
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        self._runloop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(self._runloop, source, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        try:
            CFRunLoopRun()
        finally:
            Quartz.CGEventTapEnable(tap, False)
            self._tap = None
            self._stopped.set()

    def stop(self) -> None:
        if self._runloop is not None:
            CFRunLoopStop(self._runloop)
        self._stopped.wait(timeout=2.0)
