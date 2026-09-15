"""Platform backend selection."""

from __future__ import annotations

import sys


class UnsupportedPlatform(Exception):
    pass


def make_injector(**kwargs):
    """Build the input injector for whichever OS we are running on."""
    if sys.platform == "win32":
        from .win_inject import WindowsInjector

        return WindowsInjector(**kwargs)
    if sys.platform == "darwin":
        from .mac_inject import MacInjector

        return MacInjector(**kwargs)
    raise UnsupportedPlatform(
        "wkm targets Windows and macOS; this is " + sys.platform
    )


def make_capturer(**kwargs):
    """Build the input capturer for whichever OS we are running on."""
    if sys.platform == "win32":
        from .win_capture import WindowsCapturer

        return WindowsCapturer(**kwargs)
    if sys.platform == "darwin":
        from .mac_capture import MacCapturer

        return MacCapturer(**kwargs)
    raise UnsupportedPlatform(
        "wkm targets Windows and macOS; this is " + sys.platform
    )
