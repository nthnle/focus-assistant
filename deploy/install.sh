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
# One token per network service, so each can be rotated on its own.
for name in FOCUS_CHAT_TOKEN FOCUS_DISPLAY_TOKEN; do
    if ! grep -q "^$name=" "$ETC/focus.env"; then
        echo "$name=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >> "$ETC/focus.env"
    fi
done
# The iPhone's charger reports are gone (the desk camera replaced them).
sed -i '/^FOCUS_DOCK_TOKEN=/d' "$ETC/focus.env"
chown root:root "$ETC/focus.env"
chmod 0600 "$ETC/focus.env"

echo "==> udev rule (speakerphone call button)"
install -m 0644 deploy/70-focus-powerconf.rules /etc/udev/rules.d/70-focus-powerconf.rules
udevadm control --reload
udevadm trigger --subsystem-match=hidraw

echo "==> systemd units"
# focus-dock (charger reports and the status page) became focus-page (the page only).
if [ -f /etc/systemd/system/focus-dock.service ]; then
    if systemctl is-enabled -q focus-dock.service; then ENABLE_PAGE=1; fi
    systemctl disable --now focus-dock.service || true
    rm -f /etc/systemd/system/focus-dock.service
    rm -f "$STATE_DIR/charger.json"
fi
for unit in focus-checkin.service focus-checkin.timer focus-page.service focus-chat.service \
            focus-voice.service focus-display.service focus-camera.service \
            focus-stretch.service focus-stretch.timer \
            focus-back.service focus-back.timer \
            focus-voice-sleep.service focus-voice-sleep.timer \
            focus-voice-wake.service focus-voice-wake.timer; do
    sed -e "s|@USER@|$USER_NAME|g" -e "s|@STATE_DIR@|$STATE_DIR|g" \
        "deploy/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now focus-checkin.timer
# Voice off overnight (00:00-08:00); harmless if voice isn't set up.
systemctl enable --now focus-voice-sleep.timer focus-voice-wake.timer
# These open ports or the microphone, or need the speaker set up first, so they
# are enabled by hand (see README); restart them if they run. The stretch timer
# picks up a changed unit on the daemon-reload above.
systemctl try-restart focus-page.service focus-chat.service focus-voice.service \
                      focus-display.service focus-camera.service
if [ "${ENABLE_PAGE:-0}" = 1 ]; then
    systemctl enable --now focus-page.service
fi
# The back-to-work line follows the stretch reminders: on when they are.
if systemctl is-enabled -q focus-stretch.timer; then
    systemctl enable --now focus-back.timer
fi

echo
if [ "${NEED_KEY:-0}" = 1 ]; then
    echo "Next: set FOCUS_LLM_BASE_URL, FOCUS_LLM_API_KEY and FOCUS_MODEL in $ETC/focus.env  (sudo nano $ETC/focus.env)"
elif ! grep -q "^FOCUS_LLM_API_KEY=" "$ETC/focus.env"; then
    # An install from before the switch to open models still has the Anthropic key.
    echo "Note: the coach now uses an open model. Add FOCUS_LLM_BASE_URL, FOCUS_LLM_API_KEY"
    echo "and FOCUS_MODEL to $ETC/focus.env (see deploy/focus.env.example); until then"
    echo "check-ins are the offline ones and chat says the coach is offline."
fi
echo "Try a check-in now:  sudo systemctl start focus-checkin && journalctl -u focus-checkin -n 20"
systemctl list-timers 'focus-*' --no-pager
