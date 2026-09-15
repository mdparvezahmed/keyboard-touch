# wkm — one keyboard and touchpad, two machines, one screen

Press **Ctrl+Alt+M** and your Windows laptop's keyboard and touchpad start driving
the Mac mini over WiFi. Press it again and they come back. Nothing is plugged in
between the two machines, and nothing is shared except input.

This is built for the setup where **the Mac mini has no monitor of its own** and
you watch it through a capture card in OBS on the laptop. Both "screens" are the
one laptop display, so there is no edge to shove the pointer through — which is
exactly why software like Barrier / Input Leap doesn't fit. wkm doesn't model
screen positions at all. It just takes your input away from Windows and sends it
to the Mac until you ask for it back.

```
┌─────────────────────────────┐          ┌──────────────────┐
│  Windows laptop             │   WiFi   │  Mac mini        │
│  ┌───────────────────────┐  │  (AES)   │                  │
│  │ OBS window showing ───┼──┼──────────┼── HDMI capture   │
│  │ the Mac's screen      │  │          │                  │
│  └───────────────────────┘  │  input   │                  │
│  keyboard + touchpad ───────┼─────────▶│  injected input  │
└─────────────────────────────┘          └──────────────────┘
```

---

## What it does

- **One hotkey** flips control between the two machines. Works no matter which
  app is focused — it is not a per-window thing.
- **Relative pointer motion**, read from the raw device, so it behaves like a
  trackpad driving the Mac rather than a cursor jumping to coordinates.
- **Physical-position key mapping.** The key where `A` sits drives the key where
  `A` sits, via USB HID usage codes — not scancodes, not virtual-key codes,
  both of which move around with the keyboard layout.
- **Encrypted.** Everything you type crosses your WiFi, passwords included, so
  the link is AES-256-GCM from the first byte with a shared passphrase.
- **Never leaves you stranded.** If the link drops while the Mac has your
  keyboard, control snaps straight back to Windows. Tapping Right Ctrl three
  times forces it back regardless.
- **Same program on both machines**, just started in different roles.

## What it does not do

- No screen sharing — that is what your capture card and OBS already do.
- No file transfer, no clipboard sync yet (the protocol reserves a slot for it).
- No multi-finger trackpad gestures. Windows handles three- and four-finger
  gestures internally and never emits them as mouse events, so there is nothing
  to forward. Two-finger scroll works fine.

---

## Setup

Two steps, one per machine. You need nothing installed in advance on either
side -- not even Homebrew on the Mac, which uses the Python macOS already has.

### Windows laptop

Double-click **`wkm.bat`**.

First run installs itself, then it starts and waits for the Mac. That is the
whole Windows side.

### Mac mini

Copy this folder across, **including `wkm.toml`** -- that file carries the
shared passphrase and both machines must have the same one. Then paste one line
into Terminal:

```bash
cd ~/wirelessmousekeyboard && bash scripts/mac-setup.sh
```

It installs everything, opens the permission window described below, waits for
you to flip the switch, and sets itself to start at every login. After this you
never open anything on the Mac again.

### The one click nobody can automate

Partway through, System Settings opens on **Privacy & Security -> Accessibility**
and the script pauses. Click **+**, add the path it just printed (press
**Shift-Cmd-G** to paste it), and make sure the switch is **on**. The script
notices within a couple of seconds and carries on.

macOS requires this by hand for any program that moves the pointer -- Barrier,
Synergy, Deskflow and wkm alike. It cannot be scripted, and it is worth doing
carefully: without it macOS makes `CGEventPost` fail *silently*, so wkm would
report a healthy connection while the Mac ignored everything. That is why the
target refuses to start rather than letting you debug a phantom.

Need it again later: `bash scripts/permit.sh`

### Then

Press **Ctrl+Alt+M** on the laptop.

A green badge appears at the top of your screen while the Mac has your input,
so you always know where your typing is going.

### Optional: never open anything again

```powershell
.\scripts\install-autostart.ps1
```

Starts wkm with Windows, minimised. The Mac side already starts at login. From
then on both machines are always ready and Ctrl+Alt+M just works.

## Using it

| | |
|---|---|
| **Ctrl+Alt+M** | hand control to the Mac / take it back |
| **Right Ctrl ×3** (fast) | force control back to Windows, whatever state things are in |
| **Ctrl+C** in the source terminal | quit; control returns to Windows automatically |

While the Mac has control, your Windows cursor freezes where it was and Windows
receives nothing at all. Keep the OBS preview visible — a fullscreen projector
(right-click the preview → *Fullscreen Projector*) works well, and the badge
still draws on top of it.

### Modifier keys

By default the mapping is **positional**: the key in the Command position sends
Command. On a PC keyboard that means:

| Windows key | lands on macOS as |
|---|---|
| `Win` | `Command` |
| `Alt` | `Option` |
| `Ctrl` | `Control` |

So copy on the Mac is **Win+C**. If your fingers refuse to accept that, set this
in the Mac's `wkm.toml` and Ctrl becomes Command instead:

```toml
modifier_mode = "swap_ctrl_cmd"
```

---

## Configuration

`wkm.toml` lives next to the code, or in `%APPDATA%\wkm\` / `~/.config/wkm/`.
The same file works on both machines; each side reads the parts that apply to it.

| Key | Default | What it does |
|---|---|---|
| `passphrase` | — | **Must match on both machines.** Encrypts the link. |
| `hotkey` | `ctrl+alt+m` | e.g. `ctrl+shift+f12`, `win+grave` |
| `panic_taps` | `3` | Right Ctrl taps that force control back. `0` disables |
| `mouse_source` | `auto` | `raw` / `hook` / `auto` — see Troubleshooting |
| `pointer_speed` | `1.0` | Pointer gain on the remote machine |
| `scroll_speed` | `1.0` | Scroll gain |
| `natural_scroll` | `false` | Invert scroll to match macOS natural scrolling |
| `modifier_mode` | `positional` | `positional` or `swap_ctrl_cmd` |
| `indicator` | `true` | On-screen badge (Windows) |
| `beep` | `true` | Tone on each toggle |
| `host` | `""` | Pin the target's IP; empty means auto-discover |
| `port` | `27701` | TCP port |

The passphrase can also come from the `WKM_PASSPHRASE` environment variable,
which keeps it out of the file.

---

## Troubleshooting

Run `wkm doctor` on either machine first — it checks dependencies, permissions,
discovery and the encrypted handshake, and reports the round-trip time. Add
`--local` to check just that machine and skip the network tests, which is what
you want before the other side is running.

**"Connected" but nothing moves on the Mac.**
Accessibility permission, essentially always. See step 3. `wkm doctor` on the Mac
tells you directly.

**The two machines can't find each other.**
Plenty of WiFi networks (guest networks, some mesh systems, "AP isolation")
block broadcast between clients. Find the Mac's IP with `ipconfig getifaddr en0`
and pin it in `wkm.toml`:

```toml
host = "192.168.1.42"
```

**Windows firewall.** The first run usually prompts. Allow Python on **Private**
networks. If you dismissed it, the connection will just time out.

**The pointer moves but stops at the screen edge.**
Your touchpad isn't feeding the raw input stream, so wkm fell back to hook
deltas. `auto` normally detects this on its own; force it with
`mouse_source = "hook"`.

**Keystrokes leak into Windows when they should be going to the Mac.**
A low-level hook cannot intercept input destined for a process running at higher
privilege. If the focused Windows app is elevated (Task Manager, an admin
terminal), run `wkm source` as Administrator too.

**A modifier key sticks down.**
Shouldn't happen — every handover and disconnect releases everything held — but
tapping the stuck key on the affected machine clears it.

**It feels laggy.**
Check `wkm doctor`'s round-trip figure. On a normal LAN it is 1–3 ms, and that
is not where the lag is coming from: your HDMI capture card plus OBS adds
50–200 ms, and you are watching the Mac through that pipeline. Lower the OBS
preview latency or use the capture card's own low-latency preview if it has one.
2.4 GHz WiFi with a busy channel also adds jitter; 5 GHz is noticeably steadier.

**Pointer distance doesn't match between machines.**
Windows applies "Enhance pointer precision" acceleration to injected motion.
Tune `pointer_speed`, or turn that setting off in Mouse Properties.

---

## Run the Mac side automatically at boot

Handy when the Mac mini has no keyboard attached. Create
`~/Library/LaunchAgents/com.local.wkm.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.local.wkm</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/wirelessmousekeyboard/.venv/bin/python3</string>
    <string>-m</string><string>wkm</string><string>target</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/YOU/wirelessmousekeyboard</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/wkm.log</string>
  <key>StandardErrorPath</key><string>/tmp/wkm.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.local.wkm.plist
```

Replace `YOU` with your username. The Accessibility permission must be granted
to that exact python binary, and a launch agent is a different launch context
than Terminal — so check `/tmp/wkm.err` and re-run `wkm doctor` if input stops
working after switching to this.

---

## How it works

```
Windows (source)                             Mac mini (target)
─────────────────────────────                ─────────────────────────
WH_KEYBOARD_LL / WH_MOUSE_LL   swallow local input
Raw Input (WM_INPUT)           true relative deltas
        │
        ▼
    queue  ──▶ pump thread: merge adjacent moves, batch
                      │
                      ▼
           AES-256-GCM over TCP (Nagle off)  ──────▶  decrypt, parse
                                                          │
                                                          ▼
                                              CGEventPost: moves, clicks,
                                              scroll, flagsChanged
```

A few decisions worth knowing if you come back to this later:

- **Two capture mechanisms, not one.** The low-level hook is what *suppresses*
  input, but it reports a proposed cursor *position*, and the system clamps that
  to the desktop — so a frozen cursor sitting against a screen edge silently
  loses all motion heading further that way. Raw Input supplies honest relative
  deltas. Each does the half the other can't.
- **Hook callbacks only translate and enqueue.** Windows silently uninstalls a
  low-level hook whose callback takes too long, so no I/O ever happens on that
  path.
- **Modifiers on macOS are `flagsChanged` events**, not key events. A plain
  keyDown for Shift does nothing; the system only reacts to a new flag mask.
- **Keys are identified by HID usage**, so neither end ever sees the other's
  platform keycodes.
- **Handing over releases local modifiers first.** The Ctrl+Alt of the hotkey
  reached Windows normally, and their release is about to be swallowed by
  capture — without an explicit release they would stay stuck down for the whole
  session.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

47 tests: keymap tables (including a check that no two physical keys collapse
onto the same macOS keycode), the wire format, the crypto and handshake over
real loopback sockets, and — on Windows — the actual hook callbacks driven with
synthetic event structs, so hotkey detection and the suppress/pass-through
decision are covered without hijacking your keyboard.
