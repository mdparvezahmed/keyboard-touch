"""Telling you where your keyboard is currently pointed.

With both machines sharing one physical screen there is no other cue, so this
matters more here than it would in a two-monitor setup: type into the wrong
one and the keystrokes are simply gone. The badge stays up for as long as the
Mac has your input.
"""

from __future__ import annotations

import queue
import sys
import threading

REMOTE_COLOR = "#1d6f42"
LOCAL_COLOR = "#7a3030"


def _beeper():
    """A short two-tone cue, or a no-op if the platform has no easy sound."""
    if sys.platform == "win32":
        try:
            import winsound
        except ImportError:
            return lambda remote: None

        def beep(remote: bool) -> None:
            try:
                winsound.Beep(880 if remote else 520, 70)
            except RuntimeError:
                pass

        return beep

    if sys.platform == "darwin":
        try:
            from AppKit import NSSound
        except ImportError:
            return lambda remote: None

        def beep(remote: bool) -> None:
            name = "Tink" if remote else "Pop"
            sound = NSSound.soundNamed_(name)
            if sound is not None:
                sound.play()

        return beep

    return lambda remote: None


class Indicator:
    """A small always-on-top badge driven from a private Tk thread.

    Tk is not thread-safe, so nothing here touches a widget directly; state
    changes go through a queue that the Tk thread polls. If Tk is missing or
    refuses to start, every method quietly becomes a no-op rather than taking
    the input path down with it.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._alive = False
        # Tk from a background thread is dependable on Windows and is not on
        # macOS, where the GUI must own the main thread -- and there the main
        # thread is already running the capture run loop.
        if not enabled or sys.platform != "win32":
            return
        try:
            import tkinter  # noqa: F401
        except ImportError:
            return
        self._alive = True
        self._thread = threading.Thread(target=self._run, name="wkm-indicator", daemon=True)
        self._thread.start()

    def set_remote(self, remote: bool, label: str = "") -> None:
        if self._alive:
            self._queue.put(("state", remote, label))

    def close(self) -> None:
        if self._alive:
            self._queue.put(("quit", False, ""))
            self._alive = False

    # ------------------------------------------------------------------ tk
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            self._alive = False
            return
        try:
            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.attributes("-alpha", 0.92)
            except tk.TclError:
                pass
            label = tk.Label(
                root,
                text="",
                font=("Segoe UI", 11, "bold"),
                fg="#ffffff",
                bg=REMOTE_COLOR,
                padx=16,
                pady=6,
            )
            label.pack()
        except Exception:
            self._alive = False
            return

        hide_job = {"id": None}

        def place() -> None:
            root.update_idletasks()
            width = root.winfo_width()
            x = (root.winfo_screenwidth() - width) // 2
            root.geometry("+" + str(x) + "+12")

        def poll() -> None:
            try:
                while True:
                    kind, remote, text = self._queue.get_nowait()
                    if kind == "quit":
                        root.destroy()
                        return
                    if hide_job["id"] is not None:
                        root.after_cancel(hide_job["id"])
                        hide_job["id"] = None
                    if remote:
                        label.configure(
                            text="⌨  " + (text or "REMOTE") + "   •   hotkey to return",
                            bg=REMOTE_COLOR,
                        )
                        root.deiconify()
                        place()
                    else:
                        label.configure(text="⌨  THIS PC", bg=LOCAL_COLOR)
                        root.deiconify()
                        place()
                        # The local badge is only a confirmation, so it goes
                        # away on its own; the remote badge must not.
                        hide_job["id"] = root.after(1400, root.withdraw)
            except queue.Empty:
                pass
            root.after(60, poll)

        root.after(60, poll)
        try:
            root.mainloop()
        except Exception:
            pass
        self._alive = False


class Feedback:
    """Badge plus beep, bundled so callers do not juggle both."""

    def __init__(self, indicator: bool = True, beep: bool = True) -> None:
        self._indicator = Indicator(indicator)
        self._beep = _beeper() if beep else (lambda remote: None)

    def remote(self, label: str = "") -> None:
        self._beep(True)
        self._indicator.set_remote(True, label)

    def local(self) -> None:
        self._beep(False)
        self._indicator.set_remote(False)

    def close(self) -> None:
        self._indicator.close()
