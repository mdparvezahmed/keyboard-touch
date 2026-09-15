"""macOS input permission: check it, ask for it, and point at the switch.

The failure this exists to prevent: without Accessibility permission,
``CGEventPost`` does not raise, does not warn, and does not work. wkm would
report a healthy encrypted link while the Mac ignored every keystroke. So the
target refuses to pretend, and instead asks for the permission up front and
opens the exact Settings pane.
"""

from __future__ import annotations

import subprocess
import sys

# Deep link straight to Privacy & Security -> Accessibility.
_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def _binary_to_authorise() -> str:
    """The path macOS will actually attribute the request to."""
    return sys.executable


def open_settings_pane() -> bool:
    try:
        subprocess.run(["open", _PANE], check=False, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def check(request: bool = False) -> bool | None:
    """Can we post input events?

    Returns True/False, or None on macOS versions too old to have the
    preflight API -- in which case there is nothing to check and the caller
    should simply proceed.
    """
    try:
        import Quartz
    except ImportError:
        return None
    try:
        preflight = Quartz.CGPreflightPostEventAccess
    except AttributeError:
        return None  # pre-10.15: no separate permission to preflight

    if preflight():
        return True
    if request:
        try:
            # Shows the system dialog, but only the first time this binary
            # ever asks; afterwards it just reports the current answer.
            Quartz.CGRequestPostEventAccess()
        except AttributeError:
            pass
        return bool(preflight())
    return False


def explain(log) -> None:
    """Tell the user exactly which binary to authorise, and open the pane."""
    binary = _binary_to_authorise()
    log("")
    log("=" * 68)
    log("macOS has not granted permission to control this Mac.")
    log("")
    log("Until it does, wkm will connect and nothing will move: macOS")
    log("makes CGEventPost fail silently rather than report an error.")
    log("")
    log("In the window opening now, click + and add EXACTLY this:")
    log("")
    log("    " + binary)
    log("")
    log("(Press Shift-Cmd-G in the file picker to paste that path.)")
    log("Make sure its switch is ON, then start wkm again.")
    log("=" * 68)
    log("")
    if sys.stdout.isatty():
        open_settings_pane()


def wait_until_granted(log, timeout: float = 300.0) -> bool:
    """Poll until the switch is flipped, so nothing has to be re-run.

    macOS applies the change the moment the toggle goes on, but the app has to
    notice; polling is the only way -- there is no notification for it.
    """
    import time

    deadline = time.monotonic() + timeout
    log("waiting for the switch... (Ctrl-C to skip)")
    while time.monotonic() < deadline:
        if check() is not False:
            log("")
            log("permission granted.")
            return True
        time.sleep(2.0)
    return False


def ensure(log) -> bool:
    """Gate startup on the permission. True if we may proceed."""
    state = check(request=True)
    if state is None or state:
        return True
    explain(log)
    return False
