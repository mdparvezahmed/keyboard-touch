"""Keyboard translation, keyed on physical position.

Windows and macOS number their keys completely differently and neither
numbering survives a layout change. USB HID usage IDs do: they name the
*physical* key, so the key where A sits on the laptop drives the key where
A sits on the Mac. Every platform backend converts to and from HID and
nothing else ever compares raw platform codes.
"""

from __future__ import annotations

from . import protocol as P

# --------------------------------------------------------------------------
# HID usage IDs we care about (USB HID Usage Table, keyboard page 0x07)
# --------------------------------------------------------------------------
HID_A = 0x04
HID_ENTER = 0x28
HID_ESC = 0x29
HID_BACKSPACE = 0x2A
HID_TAB = 0x2B
HID_SPACE = 0x2C
HID_CAPSLOCK = 0x39
HID_F1 = 0x3A
HID_INSERT = 0x49
HID_DELETE = 0x4C
HID_RIGHT = 0x4F
HID_LEFT = 0x50
HID_DOWN = 0x51
HID_UP = 0x52
HID_KP_ENTER = 0x58

HID_LCTRL = 0xE0
HID_LSHIFT = 0xE1
HID_LALT = 0xE2
HID_LGUI = 0xE3
HID_RCTRL = 0xE4
HID_RSHIFT = 0xE5
HID_RALT = 0xE6
HID_RGUI = 0xE7

# Private range for consumer-page keys (volume/media) that have no keyboard
# usage. Kept above the HID keyboard page so the two can never collide.
HID_MEDIA_MUTE = 0xF001
HID_MEDIA_VOLDOWN = 0xF002
HID_MEDIA_VOLUP = 0xF003
HID_MEDIA_PLAY = 0xF004
HID_MEDIA_NEXT = 0xF005
HID_MEDIA_PREV = 0xF006

MODIFIER_HIDS = frozenset(
    {HID_LCTRL, HID_LSHIFT, HID_LALT, HID_LGUI, HID_RCTRL, HID_RSHIFT, HID_RALT, HID_RGUI}
)

_MOD_BIT = {
    HID_LCTRL: P.MOD_LCTRL,
    HID_LSHIFT: P.MOD_LSHIFT,
    HID_LALT: P.MOD_LALT,
    HID_LGUI: P.MOD_LGUI,
    HID_RCTRL: P.MOD_RCTRL,
    HID_RSHIFT: P.MOD_RSHIFT,
    HID_RALT: P.MOD_RALT,
    HID_RGUI: P.MOD_RGUI,
}


def mod_bit(hid: int) -> int:
    """Modifier mask bit for a HID usage, or 0 if it is not a modifier."""
    return _MOD_BIT.get(hid, 0)


# --------------------------------------------------------------------------
# Windows PS/2 set-1 scancode -> HID usage
#
# Extended (0xE0-prefixed) codes are stored as 0xE000 | code. Scancodes are
# used in preference to virtual-key codes because VK codes shift around with
# the active keyboard layout and scancodes do not.
# --------------------------------------------------------------------------
WIN_SCAN_TO_HID: dict[int, int] = {
    0x01: HID_ESC,
    0x02: 0x1E, 0x03: 0x1F, 0x04: 0x20, 0x05: 0x21, 0x06: 0x22,   # 1 2 3 4 5
    0x07: 0x23, 0x08: 0x24, 0x09: 0x25, 0x0A: 0x26, 0x0B: 0x27,   # 6 7 8 9 0
    0x0C: 0x2D, 0x0D: 0x2E,                                        # minus equal
    0x0E: HID_BACKSPACE, 0x0F: HID_TAB,
    0x10: 0x14, 0x11: 0x1A, 0x12: 0x08, 0x13: 0x15, 0x14: 0x17,   # q w e r t
    0x15: 0x1C, 0x16: 0x18, 0x17: 0x0C, 0x18: 0x12, 0x19: 0x13,   # y u i o p
    0x1A: 0x2F, 0x1B: 0x30,                                        # lbracket rbracket
    0x1C: HID_ENTER, 0x1D: HID_LCTRL,
    0x1E: 0x04, 0x1F: 0x16, 0x20: 0x07, 0x21: 0x09, 0x22: 0x0A,   # a s d f g
    0x23: 0x0B, 0x24: 0x0D, 0x25: 0x0E, 0x26: 0x0F,               # h j k l
    0x27: 0x33, 0x28: 0x34, 0x29: 0x35,                            # semicolon quote grave
    0x2A: HID_LSHIFT, 0x2B: 0x31,                                  # lshift backslash
    0x2C: 0x1D, 0x2D: 0x1B, 0x2E: 0x06, 0x2F: 0x19, 0x30: 0x05,   # z x c v b
    0x31: 0x11, 0x32: 0x10,                                        # n m
    0x33: 0x36, 0x34: 0x37, 0x35: 0x38,                            # comma period slash
    0x36: HID_RSHIFT,
    0x37: 0x55,                                                    # keypad multiply
    0x38: HID_LALT, 0x39: HID_SPACE, 0x3A: HID_CAPSLOCK,
    0x3B: 0x3A, 0x3C: 0x3B, 0x3D: 0x3C, 0x3E: 0x3D, 0x3F: 0x3E,   # F1-F5
    0x40: 0x3F, 0x41: 0x40, 0x42: 0x41, 0x43: 0x42, 0x44: 0x43,   # F6-F10
    0x45: 0x53,                                                    # numlock
    0x46: 0x47,                                                    # scrolllock
    0x47: 0x5F, 0x48: 0x60, 0x49: 0x61, 0x4A: 0x56,               # kp7 kp8 kp9 kp-
    0x4B: 0x5C, 0x4C: 0x5D, 0x4D: 0x5E, 0x4E: 0x57,               # kp4 kp5 kp6 kp+
    0x4F: 0x59, 0x50: 0x5A, 0x51: 0x5B, 0x52: 0x62, 0x53: 0x63,   # kp1 kp2 kp3 kp0 kp.
    0x56: 0x64,                                                    # non-US backslash
    0x57: 0x44, 0x58: 0x45,                                        # F11 F12
    0x64: 0x68, 0x65: 0x69, 0x66: 0x6A, 0x67: 0x6B,               # F13-F16
    0x68: 0x6C, 0x69: 0x6D, 0x6A: 0x6E, 0x6B: 0x6F,               # F17-F20
    0x6C: 0x70, 0x6D: 0x71, 0x6E: 0x72, 0x76: 0x73,               # F21-F24
    # extended (0xE0-prefixed) codes
    0xE01C: HID_KP_ENTER,
    0xE01D: HID_RCTRL,
    0xE035: 0x54,                                                  # keypad divide
    0xE037: 0x46,                                                  # print screen
    0xE038: HID_RALT,
    0xE046: 0x48,                                                  # pause / break
    0xE047: 0x4A, 0xE048: HID_UP, 0xE049: 0x4B,                    # home up pgup
    0xE04B: HID_LEFT, 0xE04D: HID_RIGHT,
    0xE04F: 0x4D, 0xE050: HID_DOWN, 0xE051: 0x4E,                  # end down pgdn
    0xE052: HID_INSERT, 0xE053: HID_DELETE,
    0xE05B: HID_LGUI, 0xE05C: HID_RGUI, 0xE05D: 0x65,              # lwin rwin menu
    0xE020: HID_MEDIA_MUTE,
    0xE02E: HID_MEDIA_VOLDOWN,
    0xE030: HID_MEDIA_VOLUP,
    0xE022: HID_MEDIA_PLAY,
    0xE019: HID_MEDIA_NEXT,
    0xE010: HID_MEDIA_PREV,
}

HID_TO_WIN_SCAN: dict[int, int] = {}
for _scan, _hid in WIN_SCAN_TO_HID.items():
    HID_TO_WIN_SCAN.setdefault(_hid, _scan)

# Last-resort map for keys whose scancode arrives as 0 (injected events, some
# vendor hotkeys). Virtual-key codes are layout-sensitive, so this is only
# consulted when the scancode lookup misses.
VK_TO_HID: dict[int, int] = {
    0x08: HID_BACKSPACE, 0x09: HID_TAB, 0x0D: HID_ENTER, 0x1B: HID_ESC,
    0x20: HID_SPACE, 0x14: HID_CAPSLOCK,
    0x21: 0x4B, 0x22: 0x4E, 0x23: 0x4D, 0x24: 0x4A,               # pgup pgdn end home
    0x25: HID_LEFT, 0x26: HID_UP, 0x27: HID_RIGHT, 0x28: HID_DOWN,
    0x2D: HID_INSERT, 0x2E: HID_DELETE,
    0xA0: HID_LSHIFT, 0xA1: HID_RSHIFT,
    0xA2: HID_LCTRL, 0xA3: HID_RCTRL,
    0xA4: HID_LALT, 0xA5: HID_RALT,
    0x5B: HID_LGUI, 0x5C: HID_RGUI,
    0xAD: HID_MEDIA_MUTE, 0xAE: HID_MEDIA_VOLDOWN, 0xAF: HID_MEDIA_VOLUP,
    0xB0: HID_MEDIA_NEXT, 0xB1: HID_MEDIA_PREV, 0xB3: HID_MEDIA_PLAY,
}
for _i in range(10):  # digit row: VK 0x30-0x39
    VK_TO_HID[0x30 + _i] = 0x27 if _i == 0 else 0x1E + _i - 1
for _i in range(26):  # A-Z: VK 0x41-0x5A
    VK_TO_HID[0x41 + _i] = HID_A + _i
for _i in range(12):  # F1-F12: VK 0x70-0x7B
    VK_TO_HID[0x70 + _i] = HID_F1 + _i


def win_to_hid(scancode: int, extended: bool, vk: int = 0) -> int | None:
    """Translate a Windows key event to a HID usage, or None if unmappable."""
    if scancode:
        code = (0xE000 | scancode) if extended else scancode
        hid = WIN_SCAN_TO_HID.get(code)
        if hid is not None:
            return hid
        if extended:  # some drivers flag keys extended that are not
            hid = WIN_SCAN_TO_HID.get(scancode)
            if hid is not None:
                return hid
    return VK_TO_HID.get(vk)


def hid_to_win(hid: int) -> tuple[int, bool] | None:
    """Translate a HID usage to (scancode, extended) for SendInput."""
    code = HID_TO_WIN_SCAN.get(hid)
    if code is None:
        return None
    return (code & 0xFF, code > 0xFF)


# --------------------------------------------------------------------------
# macOS virtual keycodes (Carbon kVK_*) <-> HID usage
# --------------------------------------------------------------------------
HID_TO_MAC: dict[int, int] = {
    0x04: 0x00, 0x05: 0x0B, 0x06: 0x08, 0x07: 0x02, 0x08: 0x0E,   # a b c d e
    0x09: 0x03, 0x0A: 0x05, 0x0B: 0x04, 0x0C: 0x22, 0x0D: 0x26,   # f g h i j
    0x0E: 0x28, 0x0F: 0x25, 0x10: 0x2E, 0x11: 0x2D, 0x12: 0x1F,   # k l m n o
    0x13: 0x23, 0x14: 0x0C, 0x15: 0x0F, 0x16: 0x01, 0x17: 0x11,   # p q r s t
    0x18: 0x20, 0x19: 0x09, 0x1A: 0x0D, 0x1B: 0x07, 0x1C: 0x10,   # u v w x y
    0x1D: 0x06,                                                    # z
    0x1E: 0x12, 0x1F: 0x13, 0x20: 0x14, 0x21: 0x15, 0x22: 0x17,   # 1 2 3 4 5
    0x23: 0x16, 0x24: 0x1A, 0x25: 0x1C, 0x26: 0x19, 0x27: 0x1D,   # 6 7 8 9 0
    HID_ENTER: 0x24, HID_ESC: 0x35, HID_BACKSPACE: 0x33,
    HID_TAB: 0x30, HID_SPACE: 0x31,
    0x2D: 0x1B, 0x2E: 0x18, 0x2F: 0x21, 0x30: 0x1E, 0x31: 0x2A,   # minus equal [ ] backslash
    0x32: 0x2A,                                                    # non-US hash -> backslash
    0x33: 0x29, 0x34: 0x27, 0x35: 0x32,                            # semicolon quote grave
    0x36: 0x2B, 0x37: 0x2F, 0x38: 0x2C,                            # comma period slash
    HID_CAPSLOCK: 0x39,
    0x3A: 0x7A, 0x3B: 0x78, 0x3C: 0x63, 0x3D: 0x76, 0x3E: 0x60,   # F1-F5
    0x3F: 0x61, 0x40: 0x62, 0x41: 0x64, 0x42: 0x65, 0x43: 0x6D,   # F6-F10
    0x44: 0x67, 0x45: 0x6F,                                        # F11 F12
    0x46: 0x69,                                                    # printscreen -> F13
    0x48: 0x71,                                                    # pause -> F15
    HID_INSERT: 0x72,                                              # insert -> Help
    0x4A: 0x73, 0x4B: 0x74, HID_DELETE: 0x75, 0x4D: 0x77, 0x4E: 0x79,
    HID_RIGHT: 0x7C, HID_LEFT: 0x7B, HID_DOWN: 0x7D, HID_UP: 0x7E,
    0x53: 0x47,                                                    # numlock -> keypad clear
    0x54: 0x4B, 0x55: 0x43, 0x56: 0x4E, 0x57: 0x45,               # kp divide multiply minus plus
    HID_KP_ENTER: 0x4C,
    0x59: 0x53, 0x5A: 0x54, 0x5B: 0x55, 0x5C: 0x56, 0x5D: 0x57,   # kp1-kp5
    0x5E: 0x58, 0x5F: 0x59, 0x60: 0x5B, 0x61: 0x5C, 0x62: 0x52,   # kp6-kp9 kp0
    0x63: 0x41,                                                    # kp period
    0x64: 0x0A,                                                    # non-US backslash -> ISO section
    0x67: 0x51,                                                    # kp equal
    0x68: 0x69, 0x69: 0x6B, 0x6A: 0x71, 0x6B: 0x6A,               # F13-F16
    0x6C: 0x40, 0x6D: 0x4F, 0x6E: 0x50, 0x6F: 0x5A,               # F17-F20
    HID_LCTRL: 0x3B, HID_LSHIFT: 0x38, HID_LALT: 0x3A, HID_LGUI: 0x37,
    HID_RCTRL: 0x3E, HID_RSHIFT: 0x3C, HID_RALT: 0x3D, HID_RGUI: 0x36,
    HID_MEDIA_VOLUP: 0x48, HID_MEDIA_VOLDOWN: 0x49, HID_MEDIA_MUTE: 0x4A,
}

MAC_TO_HID: dict[int, int] = {}
for _hid, _mac in HID_TO_MAC.items():
    MAC_TO_HID.setdefault(_mac, _hid)


def hid_to_mac(hid: int) -> int | None:
    return HID_TO_MAC.get(hid)


def mac_to_hid(keycode: int) -> int | None:
    return MAC_TO_HID.get(keycode)


# --------------------------------------------------------------------------
# Hotkey parsing
# --------------------------------------------------------------------------
_NAME_TO_HID: dict[str, int] = {
    "esc": HID_ESC, "escape": HID_ESC, "tab": HID_TAB, "space": HID_SPACE,
    "enter": HID_ENTER, "return": HID_ENTER, "backspace": HID_BACKSPACE,
    "insert": HID_INSERT, "delete": HID_DELETE, "del": HID_DELETE,
    "home": 0x4A, "end": 0x4D, "pageup": 0x4B, "pagedown": 0x4E,
    "up": HID_UP, "down": HID_DOWN, "left": HID_LEFT, "right": HID_RIGHT,
    "capslock": HID_CAPSLOCK, "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "minus": 0x2D, "equal": 0x2E, "lbracket": 0x2F, "rbracket": 0x30,
    "backslash": 0x31, "semicolon": 0x33, "quote": 0x34, "grave": 0x35,
    "comma": 0x36, "period": 0x37, "slash": 0x38,
}
for _i in range(26):
    _NAME_TO_HID[chr(ord("a") + _i)] = HID_A + _i
for _i in range(10):
    _NAME_TO_HID[str(_i)] = 0x27 if _i == 0 else 0x1E + _i - 1
for _i in range(24):
    _NAME_TO_HID["f" + str(_i + 1)] = (HID_F1 + _i) if _i < 12 else (0x68 + _i - 12)

_MOD_ALIASES = {
    "ctrl": P.MOD_CTRL, "control": P.MOD_CTRL,
    "shift": P.MOD_SHIFT,
    "alt": P.MOD_ALT, "option": P.MOD_ALT, "opt": P.MOD_ALT,
    "win": P.MOD_GUI, "cmd": P.MOD_GUI, "command": P.MOD_GUI,
    "super": P.MOD_GUI, "meta": P.MOD_GUI, "gui": P.MOD_GUI,
}

HID_TO_NAME = {v: k for k, v in _NAME_TO_HID.items()}


class HotkeyError(ValueError):
    pass


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse "ctrl+alt+m" into (required modifier mask, HID usage).

    The mask uses the side-agnostic MOD_CTRL/MOD_ALT/... groups, so either
    Ctrl key satisfies a ctrl requirement.
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise HotkeyError("empty hotkey: " + repr(spec))
    mask = 0
    keyname = None
    for part in parts:
        if part in _MOD_ALIASES:
            mask |= _MOD_ALIASES[part]
        elif keyname is None:
            keyname = part
        else:
            raise HotkeyError("hotkey " + repr(spec) + " has more than one non-modifier key")
    if keyname is None:
        raise HotkeyError("hotkey " + repr(spec) + " needs a non-modifier key, e.g. ctrl+alt+m")
    hid = _NAME_TO_HID.get(keyname)
    if hid is None:
        raise HotkeyError("unknown key " + repr(keyname) + " in hotkey " + repr(spec))
    return mask, hid


def hotkey_matches(required_mask: int, required_hid: int, hid: int, live_mask: int) -> bool:
    """True when the pressed key plus currently-held modifiers fire the hotkey.

    Each required modifier group must have at least one side held, and no
    modifier group outside the requirement may be held, so ctrl+alt+m does
    not fire when ctrl+shift+alt+m is pressed.
    """
    if hid != required_hid:
        return False
    for group in (P.MOD_CTRL, P.MOD_SHIFT, P.MOD_ALT, P.MOD_GUI):
        if bool(required_mask & group) != bool(live_mask & group):
            return False
    return True

# --------------------------------------------------------------------------
# Modifier remapping
#
# A PC and a Mac put their modifiers in a different order:
#
#     PC    [Ctrl] [Win]    [Alt]     [Space]
#     Mac   [Ctrl] [Option] [Command] [Space]
#
# so there is no single correct answer, only the one your hands expect. These
# are expressed as substitutions applied before the key is looked up, so the
# platform backends stay ignorant of the whole question.
# --------------------------------------------------------------------------
_MOD_SOURCE_KEYS = {
    "ctrl": (HID_LCTRL, HID_RCTRL),
    "control": (HID_LCTRL, HID_RCTRL),
    "win": (HID_LGUI, HID_RGUI),
    "super": (HID_LGUI, HID_RGUI),
    "meta": (HID_LGUI, HID_RGUI),
    "alt": (HID_LALT, HID_RALT),
}

# What the destination key should behave as. On macOS, GUI is Command and ALT
# is Option; on Windows, GUI is the Windows key and ALT is Alt.
_MOD_TARGET_KEYS = {
    "control": (HID_LCTRL, HID_RCTRL),
    "ctrl": (HID_LCTRL, HID_RCTRL),
    "option": (HID_LALT, HID_RALT),
    "alt": (HID_LALT, HID_RALT),
    "command": (HID_LGUI, HID_RGUI),
    "cmd": (HID_LGUI, HID_RGUI),
    "win": (HID_LGUI, HID_RGUI),
}

#: Named arrangements. "positional" keeps each key in its own modifier slot;
#: "mac_layout" matches the physical order above, so the key beside the
#: spacebar is Command on both keyboards.
MODIFIER_MODES = {
    "positional": "ctrl=control, win=command, alt=option",
    "mac_layout": "ctrl=control, win=option, alt=command",
    "swap_ctrl_cmd": "ctrl=command, win=control, alt=option",
}


class ModifierMapError(ValueError):
    pass


def parse_modifier_map(spec: str) -> dict:
    """Parse "ctrl=control, win=option, alt=command" into a HID substitution."""
    table: dict = {}
    for clause in spec.replace(";", ",").split(","):
        clause = clause.strip().lower()
        if not clause:
            continue
        source, sep, target = clause.partition("=")
        if not sep:
            raise ModifierMapError(
                "modifier_map entry " + repr(clause) + " should look like alt=command"
            )
        source = source.strip()
        target = target.strip()
        if source not in _MOD_SOURCE_KEYS:
            raise ModifierMapError(
                "unknown key " + repr(source) + " in modifier_map; use ctrl, win or alt"
            )
        if target not in _MOD_TARGET_KEYS:
            raise ModifierMapError(
                "unknown target " + repr(target) + " in modifier_map; "
                "use control, option or command"
            )
        for src_hid, dst_hid in zip(_MOD_SOURCE_KEYS[source], _MOD_TARGET_KEYS[target]):
            table[src_hid] = dst_hid
    if not table:
        raise ModifierMapError("modifier_map is empty")
    return table


def modifier_remap(mode: str = "positional", custom: str = "") -> dict:
    """Substitution table for modifiers; custom wins over the named mode."""
    if custom.strip():
        return parse_modifier_map(custom)
    try:
        return parse_modifier_map(MODIFIER_MODES[mode])
    except KeyError:
        raise ModifierMapError(
            "unknown modifier_mode " + repr(mode) + "; choose one of "
            + ", ".join(sorted(MODIFIER_MODES))
        )


def describe_modifier_map(table: dict, target_os: str) -> list:
    """Human-readable rows for logs and diagnostics."""
    mac = {HID_LCTRL: "Control", HID_LALT: "Option", HID_LGUI: "Command"}
    win = {HID_LCTRL: "Ctrl", HID_LALT: "Alt", HID_LGUI: "Win"}
    names = mac if target_os == "darwin" else win
    rows = []
    for label, hid in (("Ctrl", HID_LCTRL), ("Win", HID_LGUI), ("Alt", HID_LALT)):
        rows.append((label, names.get(table.get(hid, hid), "?")))
    return rows

def remap_mod_mask(mask: int, table: dict) -> int:
    """Apply a modifier substitution to a packed modifier mask."""
    if not table:
        return mask
    out = 0
    for hid, bit in _MOD_BIT.items():
        if mask & bit:
            out |= _MOD_BIT.get(table.get(hid, hid), bit)
    return out
