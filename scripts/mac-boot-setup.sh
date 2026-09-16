#!/usr/bin/env bash
# Start wkm on the Mac at power-on, so it already works at the login screen.
# Run with:   bash scripts/mac-boot-setup.sh
#
# mac-setup.sh's agent only starts once someone logs in. Starting earlier takes
# a pre-login agent in /Library/LaunchAgents, which brings two constraints:
#
#  * It cannot run from ~/Desktop, ~/Documents or ~/Downloads. macOS blocks
#    background processes from reading those, and there is nobody at the login
#    window to click Allow. So the code is installed to /usr/local/wkm.
#    Re-run this script whenever you change the code or wkm.toml.
#  * FileVault has to be off. With it on, the first thing on screen after
#    power-on is the disk unlock prompt, and nothing on the disk runs until
#    somebody gets past it.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Installing for the login screen needs admin rights."
    exec sudo bash "$0" "$@"
fi

cd "$(dirname "$0")/.."
SRC="$(pwd)"
DEST="/usr/local/wkm"
AGENTS="/Library/LaunchAgents"
PRELOGIN="com.wkm.target.prelogin"
SESSION="com.wkm.target.session"
LOG="/tmp/wkm.log"
PRELOGIN_LOG="/tmp/wkm-prelogin.log"
PANE="x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

OWNER="${SUDO_USER:-}"
if [ -z "$OWNER" ] || [ "$OWNER" = "root" ]; then
    echo "Run this from your own account:  bash scripts/mac-boot-setup.sh"
    exit 1
fi
OWNER_UID="$(id -u "$OWNER")"
OWNER_HOME="$(dscl . -read "/Users/$OWNER" NFSHomeDirectory | awk '{print $2}')"

echo "=============================================="
echo " wkm boot setup - Mac mini"
echo "=============================================="
echo

if [ ! -f "$SRC/wkm.toml" ]; then
    echo "!! wkm.toml is missing -- copy it across from the Windows laptop first."
    exit 1
fi

if fdesetup isactive >/dev/null 2>&1; then
    echo "!! FileVault is on. After a power-on the Mac waits at the disk unlock"
    echo "   prompt, where nothing can run, so wkm will only start once that is"
    echo "   passed. Turn FileVault off for the login-screen part to work."
    echo
fi

# --- 1. install a copy outside the protected folders -------------------------
echo "[1/4] Installing to $DEST ..."
mkdir -p "$DEST"
rsync -a --delete --exclude __pycache__ "$SRC/wkm/" "$DEST/wkm/"
# Owned by you, so the logged-in copy can read it too. Root reads it anyway.
install -m 600 -o "$OWNER" "$SRC/wkm.toml" "$DEST/wkm.toml"

if [ ! -x "$DEST/.venv/bin/python" ]; then
    # Build from the Python the project already uses, so there is only one
    # binary to grant permission to.
    BASE_PY="/usr/bin/python3"
    if [ -x "$SRC/.venv/bin/python" ]; then
        BASE_PY="$("$SRC/.venv/bin/python" -c 'import sys; print(sys.base_prefix)')/bin/python3"
    fi
    "$BASE_PY" -m venv "$DEST/.venv"
fi
VENV_PY="$DEST/.venv/bin/python"
echo "      Installing dependencies ..."
PIP_ROOT_USER_ACTION=ignore "$VENV_PY" -m pip install --quiet --disable-pip-version-check \
    -r "$SRC/requirements.txt"
# This runs as root before login, so only root may change what it executes.
chown -R root:wheel "$DEST/wkm" "$DEST/.venv"

# A framework Python re-executes itself as the Python.app inside the framework,
# and that bundle -- not the venv symlink -- is what the permission applies to.
PY_APP="$("$VENV_PY" -c 'import os, sys; print(os.path.join(sys.base_prefix, "Resources", "Python.app"))')"
if [ ! -d "$PY_APP" ]; then
    PY_APP="$("$VENV_PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
fi

# --- 2. launch agents: one for the login screen, one once logged in ----------
echo "[2/4] Installing launch agents ..."

# mac-setup.sh's per-user agent would fight these for the port.
OLD="$OWNER_HOME/Library/LaunchAgents/com.wkm.target.plist"
if [ -f "$OLD" ]; then
    launchctl bootout "gui/$OWNER_UID" "$OLD" 2>/dev/null || true
    rm -f "$OLD"
    echo "      Removed the login-only agent from mac-setup.sh"
fi

write_agent() {  # label, session type, log file
    cat > "$AGENTS/$1.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>LimitLoadToSessionType</key><string>$2</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV_PY</string>
    <string>-m</string><string>wkm</string><string>target</string>
  </array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$3</string>
  <key>StandardErrorPath</key><string>$3</string>
</dict>
</plist>
PLISTEOF
    chown root:wheel "$AGENTS/$1.plist"
    chmod 644 "$AGENTS/$1.plist"
    plutil -lint -s "$AGENTS/$1.plist"
}
# Runs as root in front of the login window; macOS stops it when you log in.
write_agent "$PRELOGIN" LoginWindow "$PRELOGIN_LOG"
# Runs as whoever logs in, and keeps going while the screen is locked.
write_agent "$SESSION" Aqua "$LOG"

# --- 3. start the logged-in copy now -----------------------------------------
echo "[3/4] Starting ..."
launchctl bootout "gui/$OWNER_UID/$SESSION" 2>/dev/null && sleep 1 || true

if pgrep -f "wkm target" >/dev/null 2>&1; then
    echo
    echo "!! Another 'wkm target' is running, probably in a Terminal window."
    echo "   Stop it with Ctrl-C. The installed copy takes over within 10 seconds."
    echo
fi

: > "$LOG"
chown "$OWNER" "$LOG"
launchctl bootstrap "gui/$OWNER_UID" "$AGENTS/$SESSION.plist"

# --- 4. permission -----------------------------------------------------------
echo "[4/4] Checking permission to control this Mac ..."
sleep 3
asked=0
deadline=$((SECONDS + 300))
until grep -q "listening on" "$LOG" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo
        echo "Not listening after 5 minutes. See:  tail -f $LOG"
        exit 1
    fi
    if [ "$asked" -eq 0 ] && grep -q "has not granted permission" "$LOG" 2>/dev/null; then
        asked=1
        echo
        echo "  macOS needs to allow this Python to control the Mac. In the window"
        echo "  opening now, switch on 'Python'. If it is not listed, click + and"
        echo "  add EXACTLY this (Shift-Cmd-G to paste the path):"
        echo
        echo "      $PY_APP"
        echo
        echo "  The same switch covers the login screen. Waiting ..."
        sudo -u "$OWNER" open "$PANE" || true
    fi
    sleep 2
done

echo
echo "=============================================="
echo " Running. From now on it starts at power-on,"
echo " before anyone logs in."
echo "=============================================="
echo
echo "Try the login screen:  Apple menu > Log Out, wait about 15 seconds,"
echo "then press Ctrl+Alt+M on the laptop and type your password with it."
echo
echo "Logs:     tail -f $LOG             (logged in)"
echo "          cat $PRELOGIN_LOG     (login screen)"
echo "Update:   bash scripts/mac-boot-setup.sh   (after changing code or wkm.toml)"
echo "Remove:   sudo launchctl bootout gui/$OWNER_UID/$SESSION"
echo "          sudo rm $AGENTS/$PRELOGIN.plist $AGENTS/$SESSION.plist"
echo "          sudo rm -rf $DEST"
