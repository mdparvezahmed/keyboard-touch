"""The machine being driven.

Accepts one source at a time and replays its input locally. The important
property here is that nothing stays held: whenever a session ends, cleanly or
otherwise, every key and button we were holding is released. Otherwise a link
that drops mid-keystroke leaves a modifier stuck down on a machine whose
keyboard you may not be able to reach.
"""

from __future__ import annotations

import socket
import sys
import threading
import time

from . import discovery, net, protocol
from .config import Config
from .platform import make_injector

_RECV_TIMEOUT = 4.0
_SILENCE_LIMIT = 12.0


class Target:
    def __init__(self, cfg: Config, log) -> None:
        self.cfg = cfg
        self.log = log
        self._stop = threading.Event()
        self._srv: socket.socket | None = None
        self._injector = make_injector(
            modifier_mode=cfg.modifier_mode,
            pointer_speed=cfg.pointer_speed,
            scroll_speed=cfg.scroll_speed,
            natural_scroll=cfg.natural_scroll,
        )

    def run(self) -> None:
        if sys.platform == "darwin":
            from . import macperm

            # Check before listening, not after connecting. Without this the
            # link comes up looking perfectly healthy while every injected
            # event is silently discarded.
            if not macperm.ensure(self.log):
                raise SystemExit(1)

        responder = None
        if self.cfg.discovery:
            responder = discovery.Responder(self.cfg.passphrase, self.cfg.port, self.cfg.name)
            responder.start()

        try:
            self._srv = net.listen(self.cfg.bind, self.cfg.port)
        except OSError as exc:
            if responder is not None:
                responder.stop()
            raise SystemExit(
                "cannot listen on " + self.cfg.bind + ":" + str(self.cfg.port)
                + " -- " + str(exc)
                + "\nAnother copy of 'wkm target' is probably already running."
            )
        self.log("listening on " + self.cfg.bind + ":" + str(self.cfg.port))
        self.log("waiting for the other machine to connect...")
        try:
            while not self._stop.is_set():
                try:
                    link = net.accept(self._srv, self.cfg.passphrase, net.ROLE_TARGET)
                except OSError:
                    if self._stop.is_set():
                        break
                    continue
                except net.LinkError as exc:
                    # A bad passphrase or a port scanner; log and keep serving.
                    self.log("rejected connection: " + str(exc))
                    continue
                self.log("connected: " + link.peer)
                try:
                    self._serve(link)
                except Exception as exc:
                    self.log("session ended: " + str(exc))
                finally:
                    self._injector.release_all()
                    link.close()
                    self.log("disconnected; waiting for the next connection")
        finally:
            if responder is not None:
                responder.stop()
            self._injector.close()
            if self._srv is not None:
                self._srv.close()

    def _serve(self, link: net.Link) -> None:
        link.sock.settimeout(_RECV_TIMEOUT)
        last_rx = time.monotonic()
        inject = self._injector
        while not self._stop.is_set():
            try:
                record = link.recv()
            except socket.timeout:
                if time.monotonic() - last_rx > _SILENCE_LIMIT:
                    raise net.LinkError("source went quiet")
                continue
            except (OSError, net.LinkError) as exc:
                # Say why. Returning silently here made a source-side drop
                # indistinguishable from a normal disconnect in the log.
                self.log("link lost: " + (str(exc) or exc.__class__.__name__))
                return
            last_rx = time.monotonic()

            try:
                msg = protocol.parse(record)
            except protocol.ProtocolError as exc:
                self.log("ignoring bad record: " + str(exc))
                continue

            kind = msg[0]
            if kind == "move":
                inject.move(msg[1], msg[2])
            elif kind == "key":
                inject.key(msg[1], msg[2])
            elif kind == "button":
                inject.button(msg[1], msg[2])
            elif kind == "scroll":
                inject.scroll(msg[1], msg[2])
            elif kind == "mods":
                inject.set_mods(msg[1])
            elif kind == "enter":
                # Start every handover from a clean slate rather than trusting
                # whatever state the previous one left behind.
                inject.release_all()
                self.log("control received")
            elif kind == "leave":
                inject.release_all()
                self.log("control returned to the source")
            elif kind == "ping":
                link.send(protocol.pong())

    def shutdown(self) -> None:
        self._stop.set()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass


def run(cfg: Config, log) -> int:
    tgt = Target(cfg, log)
    try:
        tgt.run()
    except KeyboardInterrupt:
        tgt.shutdown()
    return 0
