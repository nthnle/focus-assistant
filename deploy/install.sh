#!/usr/bin/env bash
# Install or update on the Pi. Run from the repo root: sudo deploy/install.sh
set -euo pipefail

USER_NAME="${SUDO_USER:?run with sudo from your normal user}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
STATE_DIR="$HOME_DIR/.local/state/focus-assistant"
PREFIX=/opt/focus-assistant
ETC=/etc/focus-assistant

echo "==> venv in $PREFIX"
install -d -m 0755 "$PREFIX"
[ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet .
ln -sf "$PREFIX/venv/bin/focus" /usr/local/bin/focus

echo "==> state in $STATE_DIR"
sudo -u "$USER_NAME" mkdir -p "$STATE_DIR"
chmod 0700 "$STATE_DIR"

echo "==> secrets in $ETC"
install -d -m 0700 -o root -g root "$ETC"
if [ ! -f "$ETC/focus.env" ]; then
    install -m 0600 -o root -g root deploy/focus.env.example "$ETC/focus.env"
    NEED_KEY=1
fi
chown root:root "$ETC/focus.env"
chmod 0600 "$ETC/focus.env"

echo "==> systemd units"
for unit in focus-checkin.service focus-checkin.timer; do
    sed -e "s|@USER@|$USER_NAME|g" -e "s|@STATE_DIR@|$STATE_DIR|g" \
        "deploy/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now focus-checkin.timer

echo
if [ "${NEED_KEY:-0}" = 1 ]; then
    echo "Next: put your API key in $ETC/focus.env  (sudo nano $ETC/focus.env)"
fi
echo "Try a check-in now:  sudo systemctl start focus-checkin && journalctl -u focus-checkin -n 20"
systemctl list-timers focus-checkin.timer --no-pager
