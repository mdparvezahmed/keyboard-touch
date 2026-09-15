"""Configuration loading.

Config is a small TOML file, read with the stdlib tomllib. Every value has a
working default, so a fresh config only really needs the shared passphrase.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    # Python 3.9/3.10 -- notably the python3 that macOS ships, which is the
    # difference between "paste one line" and "install Homebrew first".
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        from . import _toml as tomllib  # type: ignore[no-redef]

DEFAULT_PORT = 27701
DISCOVERY_PORT = 27700

CONFIG_NAME = "wkm.toml"


def default_config_path() -> Path:
    """Prefer a config beside the project, then the per-user config dir."""
    local = Path.cwd() / CONFIG_NAME
    if local.exists():
        return local
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    return base / "wkm" / CONFIG_NAME


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # --- identity / link ---
    passphrase: str = ""
    port: int = DEFAULT_PORT
    host: str = ""            # empty means "find the target by discovery"
    name: str = ""            # friendly name announced during discovery
    discovery: bool = True

    # --- source-side behaviour (the machine holding the keyboard) ---
    hotkey: str = "ctrl+alt+m"
    panic_taps: int = 3       # taps of Right Ctrl that force control back
    mouse_source: str = "auto"  # auto | raw | hook
    indicator: bool = True
    beep: bool = True
    grab_on_connect: bool = False

    # --- pointer feel ---
    pointer_speed: float = 1.0
    scroll_speed: float = 1.0
    natural_scroll: bool = False

    # --- target-side behaviour (the machine being driven) ---
    modifier_mode: str = "positional"  # positional | mac_layout | swap_ctrl_cmd
    modifier_map: str = ""             # e.g. "ctrl=control, win=option, alt=command"
    bind: str = "0.0.0.0"

    # --- diagnostics ---
    log_level: str = "info"

    extras: dict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.passphrase:
            raise ConfigError(
                "no passphrase set. Put the same 'passphrase' in wkm.toml on both "
                "machines, or set the WKM_PASSPHRASE environment variable."
            )
        if len(self.passphrase) < 8:
            raise ConfigError("passphrase must be at least 8 characters")
        if self.mouse_source not in ("auto", "raw", "hook"):
            raise ConfigError("mouse_source must be auto, raw or hook")
        from . import keymap

        try:
            keymap.modifier_remap(self.modifier_mode, self.modifier_map)
        except keymap.ModifierMapError as exc:
            raise ConfigError(str(exc))
        if not (1 <= self.port <= 65535):
            raise ConfigError("port must be between 1 and 65535")


_FIELDS = {f.name for f in Config.__dataclass_fields__.values() if f.name != "extras"}


def load(path: Path | None = None) -> Config:
    """Load config from TOML, then let environment variables win.

    WKM_PASSPHRASE is honoured specially so the secret can stay out of the
    file on shared machines.
    """
    cfg = Config()
    path = path or default_config_path()
    if path.exists():
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError("could not parse " + str(path) + ": " + str(exc)) from exc
        for key, value in data.items():
            if key in _FIELDS:
                current = getattr(cfg, key)
                # Coerce ints written as floats and vice versa, but reject a
                # type mismatch that would only fail much later.
                if isinstance(current, bool) != isinstance(value, bool):
                    # bool is a subclass of int, so without this an accidental
                    # `port = true` would quietly become port 1.
                    raise ConfigError(
                        key + " must be " + ("true or false" if isinstance(current, bool)
                                             else type(current).__name__)
                    )
                if isinstance(current, float) and isinstance(value, int):
                    value = float(value)
                if not isinstance(value, type(current)):
                    raise ConfigError(
                        key + " must be " + type(current).__name__ + ", got " + type(value).__name__
                    )
                setattr(cfg, key, value)
            else:
                cfg.extras[key] = value

    env_pass = os.environ.get("WKM_PASSPHRASE")
    if env_pass:
        cfg.passphrase = env_pass
    env_host = os.environ.get("WKM_HOST")
    if env_host:
        cfg.host = env_host
    return cfg


TEMPLATE = """\
# wkm configuration -- put this SAME file on both machines.
# Only `passphrase` has to match; everything else is per-machine taste.

# Shared secret for the encrypted link. Both machines must use the same one.
# Anyone on your WiFi who knows this can drive your Mac, so make it a real one.
passphrase = "{passphrase}"

# Friendly name shown when the two ends find each other.
name = "{name}"

# ---------------------------------------------------------------------------
# Source side -- the machine whose keyboard and touchpad you actually use
# ---------------------------------------------------------------------------

# Press this to hand control to the Mac; press it again to take it back.
hotkey = "ctrl+alt+m"

# Escape hatch: tap Right Ctrl this many times quickly to force control back
# to this machine, even if the link is wedged. 0 disables it.
panic_taps = 3

# Where pointer motion comes from. "raw" reads the true device deltas and is
# both smoother and immune to screen-edge clipping; "hook" is the fallback for
# touchpads that do not surface a raw mouse stream. "auto" tries raw, notices
# if nothing arrives, and switches itself over.
mouse_source = "auto"

# Small on-screen badge and a beep when control flips, so you can tell at a
# glance which machine your keyboard is talking to.
indicator = true
beep = true

# Pointer and scroll feel on the remote machine. 1.0 is one-to-one.
pointer_speed = 1.0
scroll_speed = 1.0
natural_scroll = false

# ---------------------------------------------------------------------------
# Target side -- the machine being driven (your Mac mini)
# ---------------------------------------------------------------------------

# How the Windows modifier keys land on macOS. The two keyboards order their
# modifiers differently, so there is no neutral answer -- only the one your
# hands already expect:
#
#     PC    [Ctrl] [Win]    [Alt]     [Space]
#     Mac   [Ctrl] [Option] [Command] [Space]
#
#   "positional"    Ctrl -> Control, Win -> Command, Alt -> Option
#                   Each key keeps its own slot. Copy is Win+C.
#   "mac_layout"    Ctrl -> Control, Win -> Option,  Alt -> Command
#                   Matches the physical order above: the key beside the
#                   spacebar is Command on both keyboards, so your thumb
#                   finds it where a Mac keyboard would put it. Copy is Alt+C.
#   "swap_ctrl_cmd" Ctrl -> Command, Win -> Control, Alt -> Option
#                   For fingers that insist Ctrl+C is copy.
modifier_mode = "positional"

# Or spell it out yourself, which overrides modifier_mode entirely.
# Sources: ctrl, win, alt.  Targets: control, option, command.
# modifier_map = "ctrl=control, win=option, alt=command"

# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------

port = {port}

# Leave host empty to find the other machine automatically on the LAN. Set it
# to the target's IP if your router blocks broadcast between clients, which
# many guest and mesh networks do.
host = ""

discovery = true
bind = "0.0.0.0"
log_level = "info"
"""


def write_template(path: Path, passphrase: str, name: str, port: int = DEFAULT_PORT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = TEMPLATE.format(passphrase=passphrase, name=name, port=port)
    path.write_text(text, encoding="utf-8")
    if sys.platform != "win32":
        # The passphrase lives here; do not leave it world-readable.
        path.chmod(0o600)
