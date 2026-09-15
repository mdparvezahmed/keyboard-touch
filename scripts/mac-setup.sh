#!/usr/bin/env bash
# One-shot setup for the Mac mini. Run with:   bash scripts/mac-setup.sh
#
# Installs into a local venv, walks through the one macOS permission, and sets
# wkm to start automatically at login so you never open it again.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "=============================================="
echo " wkm setup - Mac mini"
echo "=============================================="
echo

# --- 1. find a usable python -------------------------------------------------
# wkm runs on 3.9+, which is what macOS already ships, so there is normally
# nothing to install here.
find_python() {
    local candidates="${PYTHON:-} python3 python3.13 python3.12 python3.11 python3.10 python3.9"
    for cmd in $candidates; do
        [ -n "$cmd" ] || continue
        command -v "$cmd" >/dev/null 2>&1 || continue
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

if ! PY="$(find_python)"; then
    echo "No Python 3.9+ found."
    echo
    echo "macOS provides one with the developer command line tools. Run:"
    echo "    xcode-select --install"
    echo "then run this script again."
    exit 1
fi
echo "[1/4] Using $PY ($("$PY" --version 2>&1))"

# --- 2. dependencies ---------------------------------------------------------
if [ -x ".venv/bin/python" ]; then
    echo "[2/4] Reusing existing .venv"
else
    echo "[2/4] Creating .venv ..."
    "$PY" -m venv .venv
fi
VENV_PY="$ROOT/.venv/bin/python"
"$VENV_PY" -m pip install --upgrade pip --quiet 2>/dev/null || true
echo "      Installing dependencies (this is the slow part) ..."
"$VENV_PY" -m pip install --quiet cryptography "pyobjc-framework-Quartz>=9.0"

if [ ! -f "wkm.toml" ]; then
    echo
    echo "!! wkm.toml is missing."
    echo "   Copy it across from the Windows laptop first -- it carries the"
    echo "   shared passphrase, and both machines must have the same one."
    exit 1
fi

# --- 3. the one permission macOS will not let us grant ourselves -------------
echo
echo "[3/4] Checking permission to control this Mac ..."
if ! "$VENV_PY" -m wkm permit; then
    echo
    echo "Setup stopped: without that permission wkm connects but cannot move"
    echo "anything. Flip the switch, then run this script again."
    exit 1
fi

# --- 4. start at login -------------------------------------------------------
echo
echo "[4/4] Setting wkm to start automatically at login ..."
PLIST="$HOME/Library/LaunchAgents/com.wkm.target.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.wkm.target</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV_PY</string>
    <string>-m</string><string>wkm</string><string>target</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/tmp/wkm.log</string>
  <key>StandardErrorPath</key><string>/tmp/wkm.log</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
sleep 2

echo
echo "=============================================="
if pgrep -f "wkm.*target" >/dev/null 2>&1; then
    echo " Running, and will start itself at every login."
else
    echo " Installed. Check /tmp/wkm.log if it does not connect."
fi
echo "=============================================="
echo
echo "Nothing more to do on this Mac."
echo "On the Windows laptop, double-click:  wkm.bat"
echo "Then press Ctrl+Alt+M."
echo
echo "Log:      tail -f /tmp/wkm.log"
echo "Stop:     launchctl unload $PLIST"
