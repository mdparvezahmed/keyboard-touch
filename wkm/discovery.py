"""Finding the other machine on the LAN.

A UDP broadcast query and reply, tagged with an HMAC of the passphrase so a
laptop only ever offers to connect to its own Mac. Plenty of WiFi networks
block client-to-client broadcast, so every path here degrades to "just set
host = ... in the config" rather than failing mysteriously.
"""

from __future__ import annotations

import json
import socket
import threading
import time

from . import crypto
from .config import DISCOVERY_PORT

QUERY = b"WKM-DISCOVER?"
REPLY = b"WKM-HERE!"
_MAX_DGRAM = 512


def _local_addresses() -> list[str]:
    """Broadcast addresses worth trying, most-likely first."""
    addrs = ["255.255.255.255"]
    try:
        # Does not send anything; just asks the routing table which interface
        # would be used to reach the outside world.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 53))
            ip = probe.getsockname()[0]
        finally:
            probe.close()
        octets = ip.split(".")
        if len(octets) == 4:
            addrs.insert(0, ".".join(octets[:3]) + ".255")
    except OSError:
        pass
    return addrs


class Responder(threading.Thread):
    """Runs on the target: answers discovery queries with our port."""

    daemon = True

    def __init__(self, passphrase: str, port: int, name: str) -> None:
        super().__init__(name="wkm-discovery-responder")
        self._tag = crypto.discovery_tag(passphrase).hex()
        self._port = port
        self._name = name or socket.gethostname()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            # Another wkm is already answering on this machine; harmless.
            return
        sock.settimeout(0.5)
        self._sock = sock
        payload = json.dumps(
            {"tag": self._tag, "port": self._port, "name": self._name}
        ).encode("utf-8")
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(_MAX_DGRAM)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data.startswith(QUERY):
                    continue
                # Only answer a query carrying our own tag, so unrelated wkm
                # pairs on the same network stay invisible to each other.
                if data[len(QUERY):].decode("utf-8", "replace").strip() != self._tag:
                    continue
                try:
                    sock.sendto(REPLY + payload, addr)
                except OSError:
                    pass
        finally:
            sock.close()

    def stop(self) -> None:
        self._stop.set()


def find(passphrase: str, timeout: float = 3.0) -> tuple[str, int, str] | None:
    """Broadcast a query and return (host, port, name) for the first match."""
    tag = crypto.discovery_tag(passphrase).hex()
    query = QUERY + tag.encode("ascii")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.4)
    try:
        deadline = time.monotonic() + timeout
        targets = _local_addresses()
        while time.monotonic() < deadline:
            for addr in targets:
                try:
                    sock.sendto(query, (addr, DISCOVERY_PORT))
                except OSError:
                    continue
            try:
                data, src = sock.recvfrom(_MAX_DGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(REPLY):
                continue
            try:
                info = json.loads(data[len(REPLY):].decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if info.get("tag") != tag:
                continue
            port = info.get("port")
            if not isinstance(port, int) or not (1 <= port <= 65535):
                continue
            return (src[0], port, str(info.get("name", "")))
        return None
    finally:
        sock.close()
