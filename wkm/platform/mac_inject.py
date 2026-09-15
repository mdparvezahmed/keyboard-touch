"""macOS input injection via CoreGraphics event posting.

Requires Accessibility permission (System Settings -> Privacy & Security ->
Accessibility). Without it CGEventPost silently does nothing, which is the
single most common reason this appears to connect but not work.
"""

from __future__ import annotations

import time

import Quartz

from .. import keymap, protocol

# --- event types -----------------------------------------------------------
_LEFT_DOWN = Quartz.kCGEventLeftMouseDown
_LEFT_UP = Quartz.kCGEventLeftMouseUp
_RIGHT_DOWN = Quartz.kCGEventRightMouseDown
_RIGHT_UP = Quartz.kCGEventRightMouseUp
_OTHER_DOWN = Quartz.kCGEventOtherMouseDown
_OTHER_UP = Quartz.kCGEventOtherMouseUp
_MOUSE_MOVED = Quartz.kCGEventMouseMoved
_LEFT_DRAG = Quartz.kCGEventLeftMouseDragged
_RIGHT_DRAG = Quartz.kCGEventRightMouseDragged
_OTHER_DRAG = Quartz.kCGEventOtherMouseDragged

_POST_TAP = Quartz.kCGHIDEventTap

# protocol button -> (mac button number, down type, up type, drag type)
_BUTTONS = {
    protocol.BTN_LEFT: (Quartz.kCGMouseButtonLeft, _LEFT_DOWN, _LEFT_UP, _LEFT_DRAG),
    protocol.BTN_RIGHT: (Quartz.kCGMouseButtonRight, _RIGHT_DOWN, _RIGHT_UP, _RIGHT_DRAG),
    protocol.BTN_MIDDLE: (Quartz.kCGMouseButtonCenter, _OTHER_DOWN, _OTHER_UP, _OTHER_DRAG),
    protocol.BTN_X1: (3, _OTHER_DOWN, _OTHER_UP, _OTHER_DRAG),
    protocol.BTN_X2: (4, _OTHER_DOWN, _OTHER_UP, _OTHER_DRAG),
}

# --- modifier flags --------------------------------------------------------
_FLAG_SHIFT = Quartz.kCGEventFlagMaskShift
_FLAG_CONTROL = Quartz.kCGEventFlagMaskControl
_FLAG_ALT = Quartz.kCGEventFlagMaskAlternate
_FLAG_CMD = Quartz.kCGEventFlagMaskCommand
_FLAG_NONCOALESCED = Quartz.kCGEventFlagMaskNonCoalesced

# Per-side device bits. Apps that care which Shift you pressed read these, and
# without them a right-modifier arrives looking like a left one.
_NX_LCTRL = 0x00000001
_NX_LSHIFT = 0x00000002
_NX_RSHIFT = 0x00000004
_NX_LCMD = 0x00000008
_NX_RCMD = 0x00000010
_NX_LALT = 0x00000020
_NX_RALT = 0x00000040
_NX_RCTRL = 0x00002000

_MOD_FLAGS = {
    keymap.HID_LCTRL: (_FLAG_CONTROL, _NX_LCTRL),
    keymap.HID_RCTRL: (_FLAG_CONTROL, _NX_RCTRL),
    keymap.HID_LSHIFT: (_FLAG_SHIFT, _NX_LSHIFT),
    keymap.HID_RSHIFT: (_FLAG_SHIFT, _NX_RSHIFT),
    keymap.HID_LALT: (_FLAG_ALT, _NX_LALT),
    keymap.HID_RALT: (_FLAG_ALT, _NX_RALT),
    keymap.HID_LGUI: (_FLAG_CMD, _NX_LCMD),
    keymap.HID_RGUI: (_FLAG_CMD, _NX_RCMD),
}

# Ctrl and Command trade places, for fingers that refuse to relearn copy/paste.
_SWAP_CTRL_CMD = {
    keymap.HID_LCTRL: keymap.HID_LGUI,
    keymap.HID_LGUI: keymap.HID_LCTRL,
    keymap.HID_RCTRL: keymap.HID_RGUI,
    keymap.HID_RGUI: keymap.HID_RCTRL,
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

_DOUBLE_CLICK_SECONDS = 0.45
_DOUBLE_CLICK_SLOP = 6.0


def _display_bounds() -> tuple[float, float, float, float]:
    """Union of every active display, so the pointer can reach all of them."""
    err, displays, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err != 0 or not count:
        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return (
            bounds.origin.x,
            bounds.origin.y,
            bounds.origin.x + bounds.size.width,
            bounds.origin.y + bounds.size.height,
        )
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for did in displays[:count]:
        b = Quartz.CGDisplayBounds(did)
        x0 = min(x0, b.origin.x)
        y0 = min(y0, b.origin.y)
        x1 = max(x1, b.origin.x + b.size.width)
        y1 = max(y1, b.origin.y + b.size.height)
    return (x0, y0, x1, y1)


class MacInjector:
    """Turns wkm records into real macOS input events."""

    def __init__(
        self,
        modifier_mode: str = "positional",
        pointer_speed: float = 1.0,
        scroll_speed: float = 1.0,
        natural_scroll: bool = False,
        scroll_pixels_per_notch: float = 50.0,
    ) -> None:
        self._swap = modifier_mode == "swap_ctrl_cmd"
        self._pointer_speed = max(0.05, float(pointer_speed))
        self._scroll_speed = max(0.05, float(scroll_speed))
        self._natural = bool(natural_scroll)
        self._px_per_notch = float(scroll_pixels_per_notch)

        self._flags = 0
        self._held_keys: set[int] = set()
        self._held_buttons: set[int] = set()

        # Sub-pixel scroll and sub-count click bookkeeping.
        self._scroll_acc_x = 0.0
        self._scroll_acc_y = 0.0
        self._move_acc_x = 0.0
        self._move_acc_y = 0.0
        self._last_click: dict[int, tuple[float, float, float, int]] = {}

        self._bounds = _display_bounds()
        self._bounds_checked = time.monotonic()

    # -- helpers ------------------------------------------------------------
    def _remap(self, hid: int) -> int:
        if self._swap:
            return _SWAP_CTRL_CMD.get(hid, hid)
        return hid

    def _cursor(self) -> tuple[float, float]:
        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return (loc.x, loc.y)

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        # Displays can come and go (the Mac mini's capture card is hot-plug),
        # so re-read the layout occasionally rather than caching forever.
        now = time.monotonic()
        if now - self._bounds_checked > 5.0:
            self._bounds = _display_bounds()
            self._bounds_checked = now
        x0, y0, x1, y1 = self._bounds
        return (
            min(max(x, x0), x1 - 1.0),
            min(max(y, y0), y1 - 1.0),
        )

    def _drag(self) -> tuple[int, int] | None:
        """Held button as (drag event type, mac button number), if any.

        While a button is down, motion has to be posted as a drag of that
        exact button; a plain mouseMoved leaves the drag never starting, and a
        drag type that disagrees with the button number drops the events.
        """
        for btn in (protocol.BTN_LEFT, protocol.BTN_RIGHT, protocol.BTN_MIDDLE,
                    protocol.BTN_X1, protocol.BTN_X2):
            if btn in self._held_buttons:
                mac_btn, _down, _up, drag = _BUTTONS[btn]
                return (drag, mac_btn)
        return None

    def _post(self, event) -> None:
        if event is not None:
            Quartz.CGEventPost(_POST_TAP, event)

    # -- Injector protocol --------------------------------------------------
    def move(self, dx: int, dy: int) -> None:
        # Scale in floating point and carry the remainder, so a slow drag at
        # pointer_speed 0.5 still moves instead of rounding away to nothing.
        self._move_acc_x += dx * self._pointer_speed
        self._move_acc_y += dy * self._pointer_speed
        step_x = int(self._move_acc_x)
        step_y = int(self._move_acc_y)
        self._move_acc_x -= step_x
        self._move_acc_y -= step_y
        if step_x == 0 and step_y == 0:
            return

        cx, cy = self._cursor()
        nx, ny = self._clamp(cx + step_x, cy + step_y)

        drag = self._drag()
        if drag is not None:
            drag_type, mac_btn = drag
            event = Quartz.CGEventCreateMouseEvent(None, drag_type, (nx, ny), mac_btn)
        else:
            event = Quartz.CGEventCreateMouseEvent(None, _MOUSE_MOVED, (nx, ny), 0)

        # Games and anything reading raw deltas want the actual movement, not
        # the difference between two clamped absolute positions.
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, step_x)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, step_y)
        Quartz.CGEventSetFlags(event, self._flags | _FLAG_NONCOALESCED)
        self._post(event)

    def button(self, btn: int, down: bool) -> None:
        spec = _BUTTONS.get(btn)
        if spec is None:
            return
        mac_btn, down_type, up_type, _drag = spec
        x, y = self._cursor()

        click_state = 1
        if down:
            prev = self._last_click.get(btn)
            now = time.monotonic()
            if prev is not None:
                pt, px, py, count = prev
                near = abs(px - x) <= _DOUBLE_CLICK_SLOP and abs(py - y) <= _DOUBLE_CLICK_SLOP
                if now - pt <= _DOUBLE_CLICK_SECONDS and near:
                    click_state = count + 1
            self._last_click[btn] = (now, x, y, click_state)
            self._held_buttons.add(btn)
        else:
            self._held_buttons.discard(btn)
            prev = self._last_click.get(btn)
            click_state = prev[3] if prev else 1

        event = Quartz.CGEventCreateMouseEvent(
            None, down_type if down else up_type, (x, y), mac_btn
        )
        # macOS derives double- and triple-click purely from this field.
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, click_state)
        Quartz.CGEventSetFlags(event, self._flags)
        self._post(event)

    def scroll(self, dx: int, dy: int) -> None:
        scale = self._px_per_notch * self._scroll_speed / protocol.WHEEL_DELTA
        self._scroll_acc_y += dy * scale
        self._scroll_acc_x += dx * scale
        step_y = int(self._scroll_acc_y)
        step_x = int(self._scroll_acc_x)
        self._scroll_acc_y -= step_y
        self._scroll_acc_x -= step_x
        if step_x == 0 and step_y == 0:
            return
        if self._natural:
            # Both platforms already agree that positive means "scroll away
            # from you", so this flag exists purely to match the Mac's own
            # natural-scrolling setting if you have it on.
            step_x, step_y = -step_x, -step_y
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitPixel, 2, step_y, step_x
        )
        Quartz.CGEventSetFlags(event, self._flags)
        self._post(event)

    def key(self, hid: int, down: bool) -> None:
        hid = self._remap(hid)
        mac_code = keymap.hid_to_mac(hid)
        if mac_code is None:
            return

        if hid in _MOD_FLAGS:
            self._modifier(hid, mac_code, down)
            return

        if down:
            repeat = hid in self._held_keys
            self._held_keys.add(hid)
        else:
            repeat = False
            self._held_keys.discard(hid)

        event = Quartz.CGEventCreateKeyboardEvent(None, mac_code, down)
        Quartz.CGEventSetFlags(event, self._flags)
        if repeat:
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat, 1)
        self._post(event)

    def _modifier(self, hid: int, mac_code: int, down: bool) -> None:
        """Modifiers are flagsChanged events, not key events.

        Posting a plain keyDown for Shift does nothing useful on macOS; the
        system only reacts to a flagsChanged carrying the new mask.
        """
        flag, device_bit = _MOD_FLAGS[hid]
        if down:
            self._held_keys.add(hid)
            self._flags |= flag | device_bit
        else:
            self._held_keys.discard(hid)
            self._flags &= ~device_bit
            # Only drop the shared flag once neither side is still held.
            if not any(
                other in self._held_keys
                for other, (other_flag, _) in _MOD_FLAGS.items()
                if other_flag == flag
            ):
                self._flags &= ~flag
        event = Quartz.CGEventCreateKeyboardEvent(None, mac_code, down)
        Quartz.CGEventSetType(event, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(event, self._flags)
        self._post(event)

    def set_mods(self, mask: int) -> None:
        """Make the remote modifier state match the source exactly.

        Sent on every handover, because a modifier released while control was
        on the other machine would otherwise stay latched here.
        """
        for bit, hid in _MOD_BIT_TO_HID.items():
            target = self._remap(hid)
            want = bool(mask & bit)
            have = target in self._held_keys
            if want != have:
                mac_code = keymap.hid_to_mac(target)
                if mac_code is not None:
                    self._modifier(target, mac_code, want)

    def release_all(self) -> None:
        for hid in sorted(self._held_keys, key=lambda h: h in _MOD_FLAGS):
            mac_code = keymap.hid_to_mac(hid)
            if mac_code is None:
                continue
            if hid in _MOD_FLAGS:
                self._modifier(hid, mac_code, False)
            else:
                event = Quartz.CGEventCreateKeyboardEvent(None, mac_code, False)
                Quartz.CGEventSetFlags(event, self._flags)
                self._post(event)
        self._held_keys.clear()

        for btn in list(self._held_buttons):
            self.button(btn, False)
        self._held_buttons.clear()

        self._flags = 0
        self._move_acc_x = self._move_acc_y = 0.0
        self._scroll_acc_x = self._scroll_acc_y = 0.0

    def close(self) -> None:
        self.release_all()
