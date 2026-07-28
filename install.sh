#!/usr/bin/env bash
# alx-whisper — full setup, assumes sudoers.d/alx-apt is already in place
# (i.e. `apt-get` runs without password). Idempotent. Safe to re-run.

set -euo pipefail

NAME="alx-whisper"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$NAME"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$NAME"
VENV="$DATA_DIR/.venv"

echo "=== alx-whisper bootstrap ==="
echo "DATA_DIR=$DATA_DIR"
echo "CONFIG_DIR=$CONFIG_DIR"

# 0. Sanity: do we have passwordless apt?
if ! sudo -n /usr/bin/apt-get --version >/dev/null 2>&1; then
    echo "ERROR: /etc/sudoers.d/alx-apt is not in place yet."
    echo "See ~/.config/alx-whisper/SUDOERS-HOWTO.txt for the one-liner to enable it."
    exit 1
fi
echo "[ok] sudoers rule active"

# 1. System packages
echo "[1/4] installing system packages (sudo)..."
sudo -n /usr/bin/apt-get update -y
sudo -n /usr/bin/apt-get install -y \
    libportaudio2 \
    wl-clipboard \
    xdotool \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential

# 2. Python venv
echo "[2/4] creating venv..."
mkdir -p "$DATA_DIR"
if [[ ! -x "$VENV/bin/python3" ]]; then
    python3 -m venv "$VENV"
fi
echo "[ok] venv at $VENV"

# 3. Pip deps
echo "[3/4] installing pip deps..."
"$VENV/bin/pip" install --upgrade pip wheel setuptools
"$VENV/bin/pip" install -r "$DATA_DIR/requirements.txt"

# 4. .env sanity
echo "[4/4] checking .env..."
if grep -q "sk-your-key-here" "$CONFIG_DIR/.env" 2>/dev/null; then
    echo "WARN: $CONFIG_DIR/.env still has placeholder API key. Edit it before running."
else
    echo "[ok] .env has been edited (no placeholder)"
fi

# 5. Reload systemd-user and enable service
echo "[5/5] enabling systemd --user service..."
systemctl --user daemon-reload
systemctl --user enable "$NAME.service"
systemctl --user restart "$NAME.service"
sleep 1
systemctl --user --no-pager status "$NAME.service" | head -10

echo
echo "=== done. tail logs with: journalctl --user -u $NAME.service -f ==="
