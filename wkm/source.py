"""The machine holding the keyboard and touchpad.

Three moving parts:

* the capturer, on the main thread, swallowing local input and pushing events
  into a queue (it must never block, so the queue is all it touches);
* a pump thread that coalesces those events and writes them to the link;
* a connection thread that dials the target and redials when the link drops.

Capture always follows the link. If the connection dies while the Mac has your
keyboard, control snaps back to this machine rather than leaving your input
disappearing into a closed socket.
"""

from __future__ import annotations

import queue
import threading
import time

from . import discovery, net, notify, protocol
from .config import Config
from .platform import make_capturer

_QUEUE_LIMIT = 8192
_PING_INTERVAL = 2.0
_SILENCE_LIMIT = 8.0
_RECONNECT_MIN = 1.0
_RECONNECT_MAX = 10.0
#: How often to say we are still waiting, once the reason has been stated.
_RETRY_NOTICE_SECONDS = 30.0
_DRAIN_BURST = 256

#: Pointer deltas travel as int16, so a coalesced burst has to be split rather
#: than clamped -- clamping would quietly swallow real motion.
_DELTA_MAX = 32000


class Source:
    def __init__(self, cfg: Config, log) -> None:
        self.cfg = cfg
        self.log = log
        self._q: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
        self._stop = threading.Event()
        self._link: net.Link | None = None
        self._link_lock = threading.Lock()
        self._connected = threading.Event()
        self._last_rx = 0.0
        self._last_tx = 0.0
        self._last_ping = 0.0
        self._peer_label = ""
        self._dropped = 0

        self._feedback = notify.Feedback(cfg.indicator, cfg.beep)
        self._capturer = make_capturer(
            sink=self._sink,
            hotkey=cfg.hotkey,
            panic_taps=cfg.panic_taps,
            mouse_source=cfg.mouse_source,
            log=log,
        )

    # ------------------------------------------------------- input callbacks
    def _sink(self, kind: str, *args) -> None:
        """Called on the input thread. Fast, non-blocking, no I/O."""
        if kind == "toggle":
            self._request_capture(not self._capturer.captured)
            return
        if kind == "panic":
            if self._capturer.captured:
                self._request_capture(False)
            return
        try:
            self._q.put_nowait((kind,) + args)
        except queue.Full:
            # Better to lose a pointer sample than to stall the input path and
            # have Windows tear the hook out from under us.
            self._dropped += 1

    def _request_capture(self, want: bool) -> None:
        if want and not self._connected.is_set():
            self._offer(("nolink",))
            return
        self._capturer.set_captured(want)
        self._offer(("capture", want))

    def _offer(self, item: tuple) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            pass

    # ------------------------------------------------------------- pump loop
    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._q.get(timeout=0.25)
            except queue.Empty:
                self._heartbeat()
                continue
            self._process(first)

    def _process(self, first: tuple) -> None:
        items = [first]
        for _ in range(_DRAIN_BURST):
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break

        batch: list[bytes] = []
        acc_x = acc_y = 0

        def flush_motion() -> None:
            nonlocal acc_x, acc_y
            while acc_x or acc_y:
                step_x = max(-_DELTA_MAX, min(_DELTA_MAX, acc_x))
                step_y = max(-_DELTA_MAX, min(_DELTA_MAX, acc_y))
                batch.append(protocol.move(step_x, step_y))
                acc_x -= step_x
                acc_y -= step_y

        for item in items:
            kind = item[0]
            if kind == "move":
                # Merging adjacent moves is the whole reason this pump exists:
                # a touchpad emits far more samples than the link needs.
                acc_x += item[1]
                acc_y += item[2]
                continue
            flush_motion()
            if kind == "key":
                batch.append(protocol.key(item[1], item[2]))
            elif kind == "button":
                batch.append(protocol.button(item[1], item[2]))
            elif kind == "scroll":
                batch.append(protocol.scroll(item[1], item[2]))
            elif kind == "capture":
                self._send(batch)
                batch = []
                self._apply_capture(item[1])
            elif kind == "nolink":
                self._send(batch)
                batch = []
                self._feedback.local()
                self.log("not connected yet -- staying on this machine")
        flush_motion()
        self._send(batch)

    def _apply_capture(self, captured: bool) -> None:
        if captured:
            # Windows/macOS still think the hotkey's modifiers are held and
            # their release is about to be swallowed. Clear them here, off the
            # input thread, where injecting events is safe.
            self._capturer.sync_local_modifiers()
            self._send([protocol.enter(), protocol.mods(self._capturer.mods)])
            self._feedback.remote(self._peer_label)
            self.log("control -> " + (self._peer_label or "remote"))
        else:
            self._send([protocol.leave()])
            self._feedback.local()
            self.log("control -> this machine")

    def _heartbeat(self) -> None:
        link = self._current_link()
        if link is None:
            return
        now = time.monotonic()
        # Ping on its own schedule, not "only when we have been quiet".
        # Keying this off the last send meant a moving pointer -- which
        # refreshes _last_tx every few milliseconds -- suppressed the ping
        # entirely. No ping means no pong, so _last_rx went stale and the
        # silence check below dropped a healthy link after 8s of use. Outbound
        # traffic says nothing about whether the peer is still there; only a
        # round trip does.
        if now - self._last_ping >= _PING_INTERVAL:
            self._last_ping = now
            self._send([protocol.ping()])
        if self._last_rx and now - self._last_rx > _SILENCE_LIMIT:
            # WiFi drops are frequently silent; without this the link can sit
            # half-open and swallow everything you type.
            self.log("no response from target for 8s -- dropping link")
            link.close()

    def _current_link(self) -> net.Link | None:
        with self._link_lock:
            return self._link

    def _send(self, records: list[bytes]) -> None:
        if not records:
            return
        link = self._current_link()
        if link is None:
            return
        try:
            link.send_many(records)
            self._last_tx = time.monotonic()
        except (OSError, net.LinkError) as exc:
            self.log("send failed: " + str(exc))
            link.close()

    # ------------------------------------------------------- connection loop
    def _connect_loop(self) -> None:
        backoff = _RECONNECT_MIN
        last_error = ""
        quiet_since = 0.0
        attempts = 0
        while not self._stop.is_set():
            try:
                link = self._open()
            except Exception as exc:
                # Say it in full once, then stay quiet. Waiting for the other
                # machine to come up is the normal state, not a fault, and
                # repeating the whole explanation every few seconds buries the
                # moment it actually connects.
                message = str(exc)
                if message != last_error:
                    self.log(message)
                    last_error = message
                    quiet_since = time.monotonic()
                    attempts = 1
                else:
                    attempts += 1
                    if time.monotonic() - quiet_since >= _RETRY_NOTICE_SECONDS:
                        self.log("still looking... (" + str(attempts) + " attempts)")
                        quiet_since = time.monotonic()
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 1.7, _RECONNECT_MAX)
                continue

            backoff = _RECONNECT_MIN
            last_error = ""
            self._peer_label = link.peer
            with self._link_lock:
                self._link = link
            self._last_rx = self._last_tx = self._last_ping = time.monotonic()
            self._connected.set()
            self.log("connected to " + link.peer + " -- press " + self.cfg.hotkey + " to hand over control")
            try:
                self._read_loop(link)
            except Exception as exc:
                self.log("link error: " + str(exc))
            finally:
                self._connected.clear()
                with self._link_lock:
                    self._link = None
                link.close()
                # Never leave input trapped on a machine that cannot receive it.
                if self._capturer.captured:
                    self._capturer.set_captured(False)
                    self._offer(("capture", False))
                self.log("disconnected")
            if self._stop.wait(_RECONNECT_MIN):
                return

    def _open(self) -> net.Link:
        port = self.cfg.port
        if self.cfg.host:
            try:
                return net.connect(self.cfg.host, port, self.cfg.passphrase, net.ROLE_SOURCE)
            except OSError as exc:
                # A pinned address is a shortcut, not a commitment. DHCP will
                # eventually move the target, and falling back to discovery
                # means that fixes itself instead of looking like a dead link.
                if not self.cfg.discovery:
                    raise
                self.log(
                    self.cfg.host + " did not answer (" + str(exc) + ")"
                    + " -- looking for the target on the network instead"
                )
            # A LinkError here is a real mismatch (wrong passphrase, wrong
            # protocol version) and must not be retried against someone else.

        if not self.cfg.discovery:
            raise net.LinkError("no host configured and discovery is disabled")
        found = discovery.find(self.cfg.passphrase, timeout=3.0)
        if not found:
            raise net.LinkError(
                "could not find the target on this network. Check it is running "
                "'wkm target', then set host = \"<its IP>\" in wkm.toml if your "
                "WiFi blocks broadcast between devices."
            )
        host, port, name = found
        self.log("found " + (name or host) + " at " + host + ":" + str(port))
        if self.cfg.host:
            self.log("tip: update host in wkm.toml to " + host + " to skip this lookup")
        return net.connect(host, port, self.cfg.passphrase, net.ROLE_SOURCE)

    def _read_loop(self, link: net.Link) -> None:
        while not self._stop.is_set():
            try:
                record = link.recv()
            except (OSError, net.LinkError):
                return
            self._last_rx = time.monotonic()
            try:
                msg = protocol.parse(record)
            except protocol.ProtocolError:
                continue
            if msg[0] == "ping":
                self._send([protocol.pong()])

    # ------------------------------------------------------------------- run
    def run(self) -> None:
        threads = [
            threading.Thread(target=self._connect_loop, name="wkm-connect", daemon=True),
            threading.Thread(target=self._pump, name="wkm-pump", daemon=True),
        ]
        for t in threads:
            t.start()
        self.log("ready. " + self.cfg.hotkey + " toggles control; tap Right Ctrl "
                 + str(self.cfg.panic_taps) + "x to force it back here.")
        try:
            self._capturer.run()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._capturer.set_captured(False)
        except Exception:
            pass
        link = self._current_link()
        if link is not None:
            try:
                link.send(protocol.leave())
            except (OSError, net.LinkError):
                pass
            link.close()
        self._capturer.stop()
        self._feedback.close()
        if self._dropped:
            self.log("dropped " + str(self._dropped) + " input samples under load")


def run(cfg: Config, log) -> int:
    src = Source(cfg, log)
    try:
        src.run()
    except KeyboardInterrupt:
        src.shutdown()
    return 0
