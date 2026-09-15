"""Command line entry point."""

from __future__ import annotations

import argparse
import secrets
import socket
import sys
import time
from pathlib import Path

from . import __version__, config, discovery, keymap, net

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def make_logger(level: str):
    threshold = _LEVELS.get(level, 20)
    start = time.monotonic()

    def log(message: str, level_name: str = "info") -> None:
        if _LEVELS.get(level_name, 20) < threshold:
            return
        stamp = "%7.2fs" % (time.monotonic() - start)
        print("[" + stamp + "] " + message, flush=True)

    return log


def _load(args) -> config.Config:
    path = Path(args.config) if args.config else None
    cfg = config.load(path)
    for name in ("host", "port", "hotkey", "passphrase", "modifier_mode", "mouse_source"):
        value = getattr(args, name, None)
        if value:
            setattr(cfg, name, value)
    if getattr(args, "no_indicator", False):
        cfg.indicator = False
    if getattr(args, "quiet", False):
        cfg.beep = False
    if getattr(args, "verbose", False):
        cfg.log_level = "debug"
    cfg.validate()
    keymap.parse_hotkey(cfg.hotkey)  # fail now, not on the first keypress
    return cfg


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_init(args) -> int:
    path = Path(args.config) if args.config else (Path.cwd() / config.CONFIG_NAME)
    if path.exists() and not args.force:
        print("refusing to overwrite " + str(path) + " (pass --force to replace it)")
        return 1
    passphrase = args.passphrase or secrets.token_urlsafe(18)
    name = args.name or socket.gethostname()
    config.write_template(path, passphrase, name, args.port or config.DEFAULT_PORT)
    print("wrote " + str(path))
    print()
    print("Shared passphrase:  " + passphrase)
    print()
    print("Copy this exact file to the other machine too -- the passphrase must match.")
    print("Then run 'wkm target' on the Mac mini and 'wkm source' on this laptop.")
    return 0


def cmd_source(args) -> int:
    from . import source

    cfg = _load(args)
    log = make_logger(cfg.log_level)
    if sys.platform not in ("win32", "darwin"):
        print("wkm source needs Windows or macOS", file=sys.stderr)
        return 2
    return source.run(cfg, log)


def cmd_target(args) -> int:
    from . import target

    cfg = _load(args)
    log = make_logger(cfg.log_level)
    return target.run(cfg, log)


def cmd_permit(args) -> int:
    """Walk through the one macOS permission wkm cannot grant itself."""
    if sys.platform != "darwin":
        print("Nothing to grant on this platform.")
        return 0
    from . import macperm

    log = lambda m: print(m, flush=True)  # noqa: E731
    state = macperm.check(request=True)
    if state is None:
        print("This macOS is old enough that there is no separate permission to grant.")
        return 0
    if state:
        print("Already granted -- wkm can control this Mac.")
        return 0
    macperm.explain(log)
    if not macperm.open_settings_pane():
        print("(could not open System Settings; open it by hand)")
    if macperm.wait_until_granted(log):
        return 0
    print("")
    print("Still not granted. Re-run this once the switch is on:")
    print("    ./scripts/permit.sh")
    return 1


def cmd_doctor(args) -> int:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "  ok  " if good else " FAIL "
        print("[" + mark + "] " + label + (("  -- " + detail) if detail else ""))
        if not good:
            ok = False

    print("wkm " + __version__ + " on " + sys.platform + ", python " + sys.version.split()[0])
    print()

    check(
        "python >= 3.9",
        sys.version_info >= (3, 9),
        "the python3 macOS ships with is fine",
    )

    try:
        import cryptography  # noqa: F401

        check("cryptography installed", True)
    except ImportError:
        check("cryptography installed", False, "pip install cryptography")

    if sys.platform == "darwin":
        try:
            import Quartz

            check("pyobjc Quartz installed", True)
        except ImportError:
            Quartz = None
            check("pyobjc Quartz installed", False, "pip install pyobjc-framework-Quartz")
        if Quartz is not None:
            # These preflight calls are the only reliable way to tell whether
            # macOS will actually let us post and read events; without the
            # permission, CGEventPost fails silently.
            try:
                post_ok = bool(Quartz.CGPreflightPostEventAccess())
                check(
                    "Accessibility permission (inject input)",
                    post_ok,
                    "System Settings > Privacy & Security > Accessibility",
                )
            except AttributeError:
                print("[ note ] could not check Accessibility on this macOS version")
            try:
                listen_ok = bool(Quartz.CGPreflightListenEventAccess())
                check(
                    "Input Monitoring permission (capture input)",
                    listen_ok,
                    "only needed when this Mac is the SOURCE",
                )
            except AttributeError:
                pass

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.WinDLL("user32", use_last_error=True)
            check("user32 reachable", True)
        except OSError as exc:
            check("user32 reachable", False, str(exc))
        try:
            import tkinter  # noqa: F401

            check("tkinter available (on-screen badge)", True)
        except ImportError:
            check("tkinter available (on-screen badge)", False, "badge disabled; not fatal")

    path = Path(args.config) if args.config else config.default_config_path()
    print()
    print("config: " + str(path) + ("" if path.exists() else "  (not found)"))
    try:
        cfg = config.load(Path(args.config) if args.config else None)
        cfg.validate()
        check("config valid", True)
        keymap.parse_hotkey(cfg.hotkey)
        check("hotkey parses", True, cfg.hotkey)
    except (config.ConfigError, keymap.HotkeyError) as exc:
        check("config valid", False, str(exc))
        return 1 if not ok else 1

    if getattr(args, "local", False):
        print()
        print("local checks only." if ok else "some local checks failed; see above.")
        print("Run 'wkm doctor' without --local once the other machine is running")
        print("to test discovery and the encrypted link.")
        return 0 if ok else 1

    print()
    if cfg.host:
        print("host is pinned to " + cfg.host + "; skipping discovery")
        target_host, target_port = cfg.host, cfg.port
    else:
        print("searching the LAN for a wkm target...")
        found = discovery.find(cfg.passphrase, timeout=3.0)
        if found:
            target_host, target_port, name = found
            check("discovery", True, (name or "target") + " at " + target_host + ":" + str(target_port))
        else:
            check(
                "discovery",
                False,
                "nothing answered. Start 'wkm target' on the other machine, or set host = ... "
                "if your WiFi blocks client-to-client broadcast",
            )
            return 1

    print()
    print("trying the encrypted handshake against " + target_host + ":" + str(target_port) + "...")
    try:
        link = net.connect(target_host, target_port, cfg.passphrase, net.ROLE_SOURCE)
    except net.LinkError as exc:
        check("handshake", False, str(exc))
        return 1
    except OSError as exc:
        check(
            "handshake",
            False,
            str(exc) + "  (firewall? on Windows allow python.exe on Private networks)",
        )
        return 1
    rtt_start = time.monotonic()
    try:
        from . import protocol

        link.send(protocol.ping())
        link.sock.settimeout(3.0)
        link.recv()
        rtt = (time.monotonic() - rtt_start) * 1000
        check("handshake + round trip", True, "%.2f ms" % rtt)
    except Exception as exc:
        check("round trip", False, str(exc))
    finally:
        link.close()

    print()
    print("all good." if ok else "some checks failed; see above.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wkm",
        description="Hand your laptop's keyboard and touchpad to another machine over WiFi.",
    )
    parser.add_argument("--version", action="version", version="wkm " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("-c", "--config", help="path to wkm.toml")
        p.add_argument("--passphrase", help="override the shared passphrase")
        p.add_argument("--port", type=int, help="TCP port")
        p.add_argument("-v", "--verbose", action="store_true")

    p_init = sub.add_parser("init", help="write a starter wkm.toml with a fresh passphrase")
    p_init.add_argument("-c", "--config", help="where to write it")
    p_init.add_argument("--passphrase", help="use this instead of generating one")
    p_init.add_argument("--name", help="friendly name for this machine")
    p_init.add_argument("--port", type=int, help="TCP port")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")
    p_init.set_defaults(func=cmd_init)

    p_src = sub.add_parser(
        "source",
        aliases=["control"],
        help="run on the machine whose keyboard and touchpad you use",
    )
    common(p_src)
    p_src.add_argument("--host", help="target address; omit to discover it")
    p_src.add_argument("--hotkey", help="e.g. ctrl+alt+m")
    p_src.add_argument("--mouse-source", choices=["auto", "raw", "hook"], dest="mouse_source")
    p_src.add_argument("--no-indicator", action="store_true", help="hide the on-screen badge")
    p_src.add_argument("--quiet", action="store_true", help="no beep on toggle")
    p_src.set_defaults(func=cmd_source)

    p_tgt = sub.add_parser(
        "target", aliases=["listen"], help="run on the machine being driven"
    )
    common(p_tgt)
    p_tgt.add_argument(
        "--modifier-mode",
        choices=["positional", "swap_ctrl_cmd"],
        dest="modifier_mode",
        help="how Windows modifiers land on macOS",
    )
    p_tgt.set_defaults(func=cmd_target)

    p_permit = sub.add_parser(
        "permit", help="grant wkm permission to control this Mac (macOS only)"
    )
    p_permit.set_defaults(func=cmd_permit)

    p_doc = sub.add_parser("doctor", help="check dependencies, permissions and connectivity")
    p_doc.add_argument("-c", "--config", help="path to wkm.toml")
    p_doc.add_argument(
        "--local",
        action="store_true",
        help="check this machine only; skip discovery and the connection test",
    )
    p_doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as exc:
        print("config error: " + str(exc), file=sys.stderr)
        return 2
    except keymap.HotkeyError as exc:
        print("hotkey error: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
