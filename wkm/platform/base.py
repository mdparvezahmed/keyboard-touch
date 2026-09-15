"""Interfaces every platform backend implements.

Backends speak only HID usages and relative pointer deltas. Anything
OS-specific -- scancodes, virtual keycodes, event flags -- stops here.
"""

from __future__ import annotations

from typing import Callable, Protocol


class Injector(Protocol):
    """Applies remote input to this machine."""

    def move(self, dx: int, dy: int) -> None:
        """Move the pointer by a relative delta."""

    def button(self, btn: int, down: bool) -> None:
        """Press or release a pointer button (protocol.BTN_*)."""

    def scroll(self, dx: int, dy: int) -> None:
        """Scroll, in WHEEL_DELTA units; +dy scrolls away from the user."""

    def key(self, hid: int, down: bool) -> None:
        """Press or release the physical key with this HID usage."""

    def set_mods(self, mask: int) -> None:
        """Force modifier state to match the source exactly."""

    def release_all(self) -> None:
        """Let go of every key and button we are currently holding.

        Called whenever control leaves or the link drops, so a key held at the
        moment of a disconnect cannot stay stuck down on the remote machine.
        """

    def close(self) -> None:
        ...


#: ``(kind, *args)`` where kind is one of move/button/scroll/key.
EventSink = Callable[..., None]


class Capturer(Protocol):
    """Takes this machine's input away from it and reports the events."""

    def run(self) -> None:
        """Install hooks and pump events until :meth:`stop`. Blocking."""

    def stop(self) -> None:
        ...

    def set_captured(self, captured: bool) -> None:
        """Start or stop swallowing local input."""
