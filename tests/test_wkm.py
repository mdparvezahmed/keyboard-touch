"""Tests that do not need a second machine.

Everything here is platform-independent on purpose: the keymap, the wire
format, the crypto, and a full source-to-target session over loopback with a
recording injector standing in for real input.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wkm import crypto, keymap, net, protocol  # noqa: E402
from wkm import config as wkm_config  # noqa: E402

PASSPHRASE = "correct-horse-battery-staple"


class TestProtocol(unittest.TestCase):
    def test_roundtrip_every_message(self):
        cases = [
            (protocol.move(12, -40), ("move", 12, -40)),
            (protocol.move(-32768, 32767), ("move", -32768, 32767)),
            (protocol.button(protocol.BTN_RIGHT, True), ("button", protocol.BTN_RIGHT, True)),
            (protocol.button(protocol.BTN_X2, False), ("button", protocol.BTN_X2, False)),
            (protocol.scroll(0, 120), ("scroll", 0, 120)),
            (protocol.scroll(-120, 0), ("scroll", -120, 0)),
            (protocol.key(keymap.HID_A, True), ("key", keymap.HID_A, False or True)),
            (protocol.key(keymap.HID_MEDIA_VOLUP, False), ("key", keymap.HID_MEDIA_VOLUP, False)),
            (protocol.mods(protocol.MOD_LSHIFT | protocol.MOD_RGUI),
             ("mods", protocol.MOD_LSHIFT | protocol.MOD_RGUI)),
            (protocol.enter(), ("enter",)),
            (protocol.leave(), ("leave",)),
            (protocol.ping(), ("ping",)),
            (protocol.pong(), ("pong",)),
        ]
        for encoded, expected in cases:
            self.assertEqual(protocol.parse(encoded), expected, msg=repr(expected))

    def test_media_key_survives_16_bit_field(self):
        # Media usages live above 0xF000; a byte-wide field would truncate them.
        parsed = protocol.parse(protocol.key(keymap.HID_MEDIA_PREV, True))
        self.assertEqual(parsed, ("key", keymap.HID_MEDIA_PREV, True))

    def test_move_is_five_bytes(self):
        self.assertEqual(len(protocol.move(1, 1)), 5)

    def test_rejects_garbage(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse(b"")
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse(bytes([0x7F]))
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse(bytes([protocol.T_MOVE, 1]))  # truncated


class TestKeymap(unittest.TestCase):
    def test_windows_scancode_to_hid(self):
        self.assertEqual(keymap.win_to_hid(0x1E, False), keymap.HID_A)   # A
        self.assertEqual(keymap.win_to_hid(0x39, False), keymap.HID_SPACE)
        self.assertEqual(keymap.win_to_hid(0x1D, False), keymap.HID_LCTRL)
        self.assertEqual(keymap.win_to_hid(0x1D, True), keymap.HID_RCTRL)
        self.assertEqual(keymap.win_to_hid(0x48, True), keymap.HID_UP)
        self.assertEqual(keymap.win_to_hid(0x48, False), 0x60)           # keypad 8

    def test_extended_flag_separates_duplicated_keys(self):
        # Enter and keypad-Enter share scancode 0x1C; only the extended bit
        # tells them apart, and they must not collapse together.
        self.assertNotEqual(keymap.win_to_hid(0x1C, False), keymap.win_to_hid(0x1C, True))

    def test_vk_fallback_when_scancode_missing(self):
        self.assertEqual(keymap.win_to_hid(0, False, 0x41), keymap.HID_A)
        self.assertEqual(keymap.win_to_hid(0, False, 0x0D), keymap.HID_ENTER)
        self.assertIsNone(keymap.win_to_hid(0, False, 0x00))

    def test_windows_roundtrip(self):
        for scan, hid in keymap.WIN_SCAN_TO_HID.items():
            extended = scan > 0xFF
            got = keymap.win_to_hid(scan & 0xFF, extended)
            self.assertEqual(got, hid, msg="scancode 0x%X" % scan)
            back = keymap.hid_to_win(hid)
            self.assertIsNotNone(back, msg="no reverse mapping for HID 0x%X" % hid)

    def test_letters_and_digits_reach_macos(self):
        for i in range(26):
            self.assertIsNotNone(keymap.hid_to_mac(keymap.HID_A + i), chr(97 + i))
        for name in ("enter", "space", "tab", "esc", "backspace", "up", "down", "left", "right"):
            hid = keymap._NAME_TO_HID[name]
            self.assertIsNotNone(keymap.hid_to_mac(hid), name)

    def test_mac_keycodes_are_unique(self):
        """No accidental collisions: two physical keys becoming one.

        macOS genuinely lacks a few keys a PC keyboard has, so a short list of
        aliases is intended. Anything outside that list is a typo in the table
        and would make one of the two keys unreachable.
        """
        allowed = {
            (0x31, 0x32),  # backslash and non-US hash are one key on ANSI
            (0x46, 0x68),  # PrintScreen has no Mac equivalent -> F13
            (0x48, 0x6A),  # Pause has no Mac equivalent -> F15
        }
        seen = {}
        collisions = set()
        for hid, mac in sorted(keymap.HID_TO_MAC.items()):
            if mac in seen:
                collisions.add(tuple(sorted((hid, seen[mac]))))
            else:
                seen[mac] = hid
        self.assertEqual(
            collisions - allowed,
            set(),
            "unintended duplicate macOS keycodes: "
            + ", ".join("0x%X/0x%X" % pair for pair in sorted(collisions - allowed)),
        )

    def test_modifiers_map_to_distinct_mac_keys(self):
        codes = {keymap.hid_to_mac(h) for h in keymap.MODIFIER_HIDS}
        self.assertEqual(len(codes), len(keymap.MODIFIER_HIDS))
        self.assertNotIn(None, codes)

    def test_mac_roundtrip(self):
        for hid in list(keymap.MODIFIER_HIDS) + [keymap.HID_A, keymap.HID_ENTER, keymap.HID_UP]:
            mac = keymap.hid_to_mac(hid)
            self.assertEqual(keymap.mac_to_hid(mac), hid)


class TestHotkey(unittest.TestCase):
    def test_parse(self):
        mask, hid = keymap.parse_hotkey("ctrl+alt+m")
        self.assertEqual(mask, protocol.MOD_CTRL | protocol.MOD_ALT)
        self.assertEqual(hid, keymap._NAME_TO_HID["m"])

    def test_aliases_and_case(self):
        self.assertEqual(keymap.parse_hotkey("Cmd+Shift+F5"), keymap.parse_hotkey("win+shift+f5"))

    def test_bad_specs(self):
        for bad in ("", "ctrl+alt", "ctrl+alt+nope", "a+b", "+"):
            with self.assertRaises(keymap.HotkeyError, msg=bad):
                keymap.parse_hotkey(bad)

    def test_matching_requires_exact_modifier_set(self):
        mask, hid = keymap.parse_hotkey("ctrl+alt+m")
        self.assertTrue(
            keymap.hotkey_matches(mask, hid, hid, protocol.MOD_LCTRL | protocol.MOD_LALT)
        )
        self.assertTrue(
            keymap.hotkey_matches(mask, hid, hid, protocol.MOD_RCTRL | protocol.MOD_RALT)
        )
        # An extra modifier must not fire it.
        self.assertFalse(
            keymap.hotkey_matches(
                mask, hid, hid, protocol.MOD_LCTRL | protocol.MOD_LALT | protocol.MOD_LSHIFT
            )
        )
        # A missing one must not either.
        self.assertFalse(keymap.hotkey_matches(mask, hid, hid, protocol.MOD_LCTRL))
        # Right key, wrong key.
        self.assertFalse(
            keymap.hotkey_matches(mask, hid, keymap.HID_A, protocol.MOD_LCTRL | protocol.MOD_LALT)
        )


class TestCrypto(unittest.TestCase):
    def _pair(self):
        a, b = crypto.derive_keys(PASSPHRASE, b"c" * 32, b"s" * 32)
        return crypto.Session(a, b), crypto.Session(b, a)

    def test_seal_open(self):
        client, server = self._pair()
        frame = client.seal(protocol.move(3, 4))
        length = int.from_bytes(frame[:2], "big")
        self.assertEqual(length, len(frame) - 2)
        self.assertEqual(protocol.parse(server.open(frame[2:])), ("move", 3, 4))

    def test_nonce_advances_so_identical_plaintext_differs(self):
        client, _ = self._pair()
        first = client.seal(protocol.move(1, 1))
        second = client.seal(protocol.move(1, 1))
        self.assertNotEqual(first, second)

    def test_out_of_order_frame_is_rejected(self):
        client, server = self._pair()
        first = client.seal(protocol.move(1, 1))
        second = client.seal(protocol.move(2, 2))
        with self.assertRaises(crypto.AuthError):
            server.open(second[2:])  # skipping a frame must not authenticate
        self.assertEqual(protocol.parse(server.open(first[2:])), ("move", 1, 1))

    def test_tamper_is_rejected(self):
        client, server = self._pair()
        frame = bytearray(client.seal(protocol.move(1, 1)))
        frame[-1] ^= 0x01
        with self.assertRaises(crypto.AuthError):
            server.open(bytes(frame[2:]))

    def test_wrong_passphrase_gives_different_keys(self):
        a1, _ = crypto.derive_keys("one-passphrase", b"c" * 32, b"s" * 32)
        a2, _ = crypto.derive_keys("two-passphrase", b"c" * 32, b"s" * 32)
        self.assertNotEqual(a1, a2)

    def test_nonces_change_the_keys(self):
        a1, _ = crypto.derive_keys(PASSPHRASE, b"c" * 32, b"s" * 32)
        a2, _ = crypto.derive_keys(PASSPHRASE, b"c" * 32, b"t" * 32)
        self.assertNotEqual(a1, a2)

    def test_discovery_tag_is_stable_and_secret_dependent(self):
        self.assertEqual(crypto.discovery_tag(PASSPHRASE), crypto.discovery_tag(PASSPHRASE))
        self.assertNotEqual(crypto.discovery_tag(PASSPHRASE), crypto.discovery_tag("other"))


class _Recorder:
    """Stands in for a real injector and remembers what it was told."""

    def __init__(self):
        self.events = []
        self.held_keys = set()
        self.held_buttons = set()

    def move(self, dx, dy):
        self.events.append(("move", dx, dy))

    def button(self, btn, down):
        self.events.append(("button", btn, down))
        (self.held_buttons.add if down else self.held_buttons.discard)(btn)

    def scroll(self, dx, dy):
        self.events.append(("scroll", dx, dy))

    def key(self, hid, down):
        self.events.append(("key", hid, down))
        (self.held_keys.add if down else self.held_keys.discard)(hid)

    def set_mods(self, mask):
        self.events.append(("mods", mask))

    def release_all(self):
        self.events.append(("release_all",))
        self.held_keys.clear()
        self.held_buttons.clear()

    def close(self):
        self.release_all()


class TestLinkOverLoopback(unittest.TestCase):
    """Real sockets, real handshake, real AES, on 127.0.0.1."""

    def _serve(self, passphrase, result, srv):
        try:
            result.append(net.accept(srv, passphrase, net.ROLE_TARGET))
        except Exception as exc:
            result.append(exc)

    def _connected_pair(self, server_pass=PASSPHRASE, client_pass=PASSPHRASE):
        srv = net.listen("127.0.0.1", 0)
        port = srv.getsockname()[1]
        result = []
        thread = threading.Thread(target=self._serve, args=(server_pass, result, srv))
        thread.start()
        try:
            client = net.connect("127.0.0.1", port, client_pass, net.ROLE_SOURCE)
        except Exception as exc:
            thread.join(5)
            srv.close()
            raise exc
        thread.join(5)
        srv.close()
        server = result[0]
        if isinstance(server, Exception):
            client.close()
            raise server
        return client, server

    def test_handshake_and_message_flow(self):
        client, server = self._connected_pair()
        try:
            client.send(protocol.move(7, -3))
            self.assertEqual(protocol.parse(server.recv()), ("move", 7, -3))
            server.send(protocol.pong())
            self.assertEqual(protocol.parse(client.recv()), ("pong",))
        finally:
            client.close()
            server.close()

    def test_send_many_preserves_order(self):
        client, server = self._connected_pair()
        try:
            records = [protocol.key(keymap.HID_A, True), protocol.move(1, 2),
                       protocol.key(keymap.HID_A, False)]
            client.send_many(records)
            got = [protocol.parse(server.recv()) for _ in records]
            self.assertEqual(
                got,
                [("key", keymap.HID_A, True), ("move", 1, 2), ("key", keymap.HID_A, False)],
            )
        finally:
            client.close()
            server.close()

    def test_wrong_passphrase_is_refused(self):
        with self.assertRaises(net.LinkError) as ctx:
            self._connected_pair(server_pass=PASSPHRASE, client_pass="a-different-secret")
        self.assertIn("passphrase", str(ctx.exception).lower())

    def test_plain_tcp_client_is_refused(self):
        srv = net.listen("127.0.0.1", 0)
        port = srv.getsockname()[1]
        result = []
        thread = threading.Thread(target=self._serve, args=(PASSPHRASE, result, srv))
        thread.start()
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.sendall(b"GET / HTTP/1.0\r\n\r\n")
        thread.join(5)
        raw.close()
        srv.close()
        self.assertIsInstance(result[0], Exception)

    def test_traffic_is_not_plaintext_on_the_wire(self):
        """A keystroke must not be readable by anyone sniffing the WiFi."""
        srv = net.listen("127.0.0.1", 0)
        port = srv.getsockname()[1]
        result = []
        thread = threading.Thread(target=self._serve, args=(PASSPHRASE, result, srv))
        thread.start()
        client = net.connect("127.0.0.1", port, PASSPHRASE, net.ROLE_SOURCE)
        thread.join(5)
        srv.close()
        server = result[0]

        try:
            secret = protocol.key(keymap.HID_A, True)
            client.send(secret)
            # Peek at the bytes actually on the wire without consuming them,
            # which is what an attacker sniffing the WiFi would see.
            server.sock.settimeout(5.0)
            on_the_wire = server.sock.recv(4096, socket.MSG_PEEK)
            self.assertTrue(on_the_wire)
            self.assertNotIn(bytes(secret), on_the_wire)
            # The frame still decrypts correctly for the real endpoint.
            self.assertEqual(protocol.parse(server.recv()), ("key", keymap.HID_A, True))
        finally:
            client.close()
            server.close()


class TestSessionSemantics(unittest.TestCase):
    """The target-side rules that keep keys from sticking."""

    def _apply(self, injector, records):
        for record in records:
            msg = protocol.parse(record)
            kind = msg[0]
            if kind == "move":
                injector.move(msg[1], msg[2])
            elif kind == "key":
                injector.key(msg[1], msg[2])
            elif kind == "button":
                injector.button(msg[1], msg[2])
            elif kind == "scroll":
                injector.scroll(msg[1], msg[2])
            elif kind == "mods":
                injector.set_mods(msg[1])
            elif kind in ("enter", "leave"):
                injector.release_all()

    def test_leave_releases_everything_held(self):
        rec = _Recorder()
        self._apply(rec, [
            protocol.enter(),
            protocol.key(keymap.HID_LSHIFT, True),
            protocol.key(keymap.HID_A, True),
            protocol.button(protocol.BTN_LEFT, True),
            protocol.leave(),
        ])
        self.assertEqual(rec.held_keys, set())
        self.assertEqual(rec.held_buttons, set())

    def test_enter_starts_from_a_clean_slate(self):
        rec = _Recorder()
        rec.key(keymap.HID_LCTRL, True)          # left over from a dead session
        self._apply(rec, [protocol.enter()])
        self.assertEqual(rec.held_keys, set())


class TestConfig(unittest.TestCase):
    def test_template_roundtrips(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wkm.toml"
            wkm_config.write_template(path, "a-real-passphrase", "test-box", 27701)
            cfg = wkm_config.load(path)
            cfg.validate()
            self.assertEqual(cfg.passphrase, "a-real-passphrase")
            self.assertEqual(cfg.name, "test-box")
            self.assertEqual(cfg.port, 27701)
            self.assertEqual(cfg.hotkey, "ctrl+alt+m")
            keymap.parse_hotkey(cfg.hotkey)

    def test_validation_rejects_weak_setup(self):
        cfg = wkm_config.Config()
        with self.assertRaises(wkm_config.ConfigError):
            cfg.validate()
        cfg.passphrase = "short"
        with self.assertRaises(wkm_config.ConfigError):
            cfg.validate()
        cfg.passphrase = "long-enough-passphrase"
        cfg.validate()
        cfg.mouse_source = "nonsense"
        with self.assertRaises(wkm_config.ConfigError):
            cfg.validate()


class TestDiscovery(unittest.TestCase):
    def test_responder_answers_matching_tag_only(self):
        responder = discovery_module.Responder(PASSPHRASE, 27701, "mac-mini")
        responder.start()
        time.sleep(0.3)
        try:
            found = discovery_module.find(PASSPHRASE, timeout=2.0)
            if found is None:
                self.skipTest("UDP broadcast is blocked in this environment")
            host, port, name = found
            self.assertEqual(port, 27701)
            self.assertEqual(name, "mac-mini")
            # A different passphrase must not see this responder.
            self.assertIsNone(discovery_module.find("a-totally-different-secret", timeout=1.0))
        finally:
            responder.stop()


from wkm import discovery as discovery_module  # noqa: E402


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(sys.platform == "win32", "Windows hook path")
class TestWindowsHookPath(unittest.TestCase):
    """Drives the real hook callbacks with synthetic structs.

    This exercises scancode translation, modifier tracking, hotkey matching
    and the suppress/pass-through decision without touching the actual
    keyboard, which is the part that is otherwise only testable by hand.
    """

    def setUp(self):
        import ctypes

        from wkm.platform import win_capture

        self.ctypes = ctypes
        self.wc = win_capture
        self.events = []
        self.cap = win_capture.WindowsCapturer(
            sink=lambda kind, *a: self.events.append((kind,) + a),
            hotkey="ctrl+alt+m",
            panic_taps=3,
        )

    def _key(self, scancode, vk, down, extended=False, injected=False):
        flags = 0
        if extended:
            flags |= self.wc.LLKHF_EXTENDED
        if injected:
            flags |= self.wc.LLKHF_INJECTED
        if not down:
            flags |= self.wc.LLKHF_UP
        info = self.wc.KBDLLHOOKSTRUCT(
            vkCode=vk, scanCode=scancode, flags=flags, time=0, dwExtraInfo=0
        )
        msg = self.wc.WM_KEYDOWN if down else self.wc.WM_KEYUP
        # Keep a reference alive: the callback casts this pointer.
        self._last = info
        return self.cap._on_keyboard(self.wc.HC_ACTION, msg, self.ctypes.addressof(info))

    def _mouse(self, msg, mouse_data=0, x=0, y=0, injected=False):
        import ctypes
        from ctypes import wintypes

        info = self.wc.MSLLHOOKSTRUCT(
            pt=wintypes.POINT(x, y),
            mouseData=mouse_data & 0xFFFFFFFF,
            flags=self.wc.LLMHF_INJECTED if injected else 0,
            time=0,
            dwExtraInfo=0,
        )
        self._last = info
        return self.cap._on_mouse(self.wc.HC_ACTION, msg, ctypes.addressof(info))

    SC_LCTRL, SC_LALT, SC_M, SC_A = 0x1D, 0x38, 0x32, 0x1E
    VK_LCTRL, VK_LALT, VK_M, VK_A = 0xA2, 0xA4, 0x4D, 0x41

    def test_hotkey_fires_and_is_swallowed(self):
        self._key(self.SC_LCTRL, self.VK_LCTRL, True)
        self._key(self.SC_LALT, self.VK_LALT, True)
        self.assertEqual(self.events, [], "modifiers alone must not fire the hotkey")
        result = self._key(self.SC_M, self.VK_M, True)
        self.assertEqual(self.events, [("toggle",)])
        self.assertEqual(result, 1, "the hotkey itself must never reach applications")

    def test_hotkey_does_not_fire_with_an_extra_modifier(self):
        self._key(self.SC_LCTRL, self.VK_LCTRL, True)
        self._key(self.SC_LALT, self.VK_LALT, True)
        self._key(0x2A, 0xA0, True)  # left shift
        self._key(self.SC_M, self.VK_M, True)
        self.assertEqual(self.events, [])

    def test_passthrough_when_not_captured(self):
        result = self._key(self.SC_A, self.VK_A, True)
        self.assertEqual(self.events, [])
        self.assertNotEqual(result, 1, "local typing must reach Windows when not captured")

    def test_forwards_and_suppresses_when_captured(self):
        self.cap._captured = True
        result = self._key(self.SC_A, self.VK_A, True)
        self.assertEqual(self.events, [("key", keymap.HID_A, True)])
        self.assertEqual(result, 1, "captured input must not also reach Windows")

    def test_our_own_injected_events_are_never_echoed(self):
        """Otherwise releasing local modifiers would loop back over the link."""
        from wkm.platform.win_inject import WKM_SIGNATURE

        self.cap._captured = True
        info = self.wc.KBDLLHOOKSTRUCT(
            vkCode=self.VK_A, scanCode=self.SC_A, flags=0, time=0, dwExtraInfo=WKM_SIGNATURE
        )
        result = self.cap._on_keyboard(
            self.wc.HC_ACTION, self.wc.WM_KEYDOWN, self.ctypes.addressof(info)
        )
        self.assertEqual(self.events, [])
        self.assertNotEqual(result, 1)

    def test_modifier_state_tracks_up_and_down(self):
        self._key(self.SC_LCTRL, self.VK_LCTRL, True)
        self.assertTrue(self.cap.mods & protocol.MOD_LCTRL)
        self._key(self.SC_LCTRL, self.VK_LCTRL, False)
        self.assertFalse(self.cap.mods & protocol.MOD_LCTRL)

    def test_right_ctrl_panic_taps(self):
        for _ in range(2):
            self._key(0x1D, 0xA3, True, extended=True)
            self._key(0x1D, 0xA3, False, extended=True)
        self.assertEqual(self.events, [])
        self._key(0x1D, 0xA3, True, extended=True)
        self.assertEqual(self.events, [("panic",)])

    def test_wheel_decodes_signed(self):
        self.cap._captured = True
        self._mouse(self.wc.WM_MOUSEWHEEL, mouse_data=(120 << 16))
        self._mouse(self.wc.WM_MOUSEWHEEL, mouse_data=((-120 & 0xFFFF) << 16))
        self._mouse(self.wc.WM_MOUSEHWHEEL, mouse_data=((-240 & 0xFFFF) << 16))
        self.assertEqual(
            self.events,
            [("scroll", 0, 120), ("scroll", 0, -120), ("scroll", -240, 0)],
        )

    def test_buttons_and_xbuttons(self):
        self.cap._captured = True
        self._mouse(self.wc.WM_LBUTTONDOWN)
        self._mouse(self.wc.WM_RBUTTONUP)
        self._mouse(self.wc.WM_XBUTTONDOWN, mouse_data=(2 << 16))
        self.assertEqual(
            self.events,
            [
                ("button", protocol.BTN_LEFT, True),
                ("button", protocol.BTN_RIGHT, False),
                ("button", protocol.BTN_X2, True),
            ],
        )

    def test_mouse_passes_through_when_not_captured(self):
        result = self._mouse(self.wc.WM_LBUTTONDOWN)
        self.assertEqual(self.events, [])
        self.assertNotEqual(result, 1)

    def test_hook_deltas_are_relative_to_the_anchor(self):
        self.cap._captured = True
        self.cap._use_raw = False
        from ctypes import wintypes

        self.cap._anchor = wintypes.POINT(500, 500)
        self._mouse(self.wc.WM_MOUSEMOVE, x=512, y=488)
        self.assertEqual(self.events, [("move", 12, -12)])


@unittest.skipUnless(sys.platform == "win32", "Windows hook lifecycle")
class TestWindowsCapturerLifecycle(unittest.TestCase):
    def test_install_and_teardown(self):
        """Hooks must install and come back out cleanly.

        Capture stays off throughout, so this never swallows real input.
        """
        from wkm.platform import win_capture

        cap = win_capture.WindowsCapturer(sink=lambda *a: None, hotkey="ctrl+alt+m")
        error = []
        thread = threading.Thread(target=lambda: _guard(cap.run, error), daemon=True)
        thread.start()
        time.sleep(1.2)
        self.assertEqual(error, [], "capturer failed to start: " + repr(error))
        self.assertTrue(cap._kb_hook, "keyboard hook did not install")
        self.assertTrue(cap._mouse_hook, "mouse hook did not install")
        self.assertTrue(cap._hwnd, "message window was not created")
        self.assertFalse(cap.captured, "must start in pass-through, never capturing")
        cap.stop()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "message loop did not exit")
        self.assertIsNone(cap._kb_hook, "keyboard hook was not removed")
        self.assertIsNone(cap._mouse_hook, "mouse hook was not removed")


def _guard(fn, sink):
    try:
        fn()
    except Exception as exc:
        sink.append(exc)


@unittest.skipUnless(sys.platform == "win32", "Windows modifier sync")
class TestWindowsModifierSync(unittest.TestCase):
    """sync_local_modifiers must really reach SendInput.

    Releasing a key Windows does not think is down is a no-op, so this is safe
    to run for real -- and running it for real is the point, since a wrong
    ctypes signature here only shows up at the moment you first press the
    hotkey.
    """

    def test_releases_held_modifiers_without_error(self):
        from wkm.platform import win_capture

        cap = win_capture.WindowsCapturer(sink=lambda *a: None, hotkey="ctrl+alt+m")
        cap._down = {keymap.HID_LSHIFT, keymap.HID_LCTRL, keymap.HID_LALT, keymap.HID_A}
        cap.sync_local_modifiers()  # raises WinError if SendInput was rejected

    def test_no_modifiers_held_is_a_no_op(self):
        from wkm.platform import win_capture

        cap = win_capture.WindowsCapturer(sink=lambda *a: None, hotkey="ctrl+alt+m")
        cap._down = {keymap.HID_A}
        cap.sync_local_modifiers()


class TestTomlFallback(unittest.TestCase):
    """The parser used when tomllib is absent (macOS ships Python 3.9).

    It only has to handle wkm.toml, but it must handle it *exactly* like the
    real thing -- a config that half-parses would be worse than one that
    refuses to load.
    """

    def setUp(self):
        from wkm import _toml

        self._toml = _toml
        self.text = wkm_config.TEMPLATE.format(
            passphrase="a-real-passphrase", name="mac-mini", port=27701
        )

    def test_matches_stdlib_tomllib_on_the_real_template(self):
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest("no stdlib tomllib to compare against")
        self.assertEqual(self._toml.loads(self.text), tomllib.loads(self.text))

    def test_produces_a_valid_config(self):
        data = self._toml.loads(self.text)
        cfg = wkm_config.Config()
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        cfg.validate()
        self.assertEqual(cfg.hotkey, "ctrl+alt+m")
        self.assertEqual(cfg.port, 27701)

    def test_value_types(self):
        got = self._toml.loads(
            's = "hi"\nliteral = \'raw\'\ni = 42\nf = 1.5\nt = true\nf2 = false\nempty = ""\n'
        )
        self.assertEqual(
            got,
            {"s": "hi", "literal": "raw", "i": 42, "f": 1.5,
             "t": True, "f2": False, "empty": ""},
        )

    def test_comments_and_hashes_inside_strings(self):
        got = self._toml.loads('a = "x#y"  # trailing comment\n# whole line\nb = 1 # after int\n')
        self.assertEqual(got, {"a": "x#y", "b": 1})

    def test_escapes(self):
        self.assertEqual(self._toml.loads(r'a = "l1\nl2\ttab\"q\b"')["a"],
                         'l1\nl2\ttab"q\b')

    def test_rejects_rather_than_guesses(self):
        for bad in ('x = unquoted', 'x', '[table]', 'x = "unterminated', '= 1'):
            with self.assertRaises(self._toml.TOMLDecodeError, msg=bad):
                self._toml.loads(bad)

    def test_load_from_binary_file(self):
        import io

        self.assertEqual(
            self._toml.load(io.BytesIO(b'a = "\xc3\xa9"\n')), {"a": "é"}
        )
