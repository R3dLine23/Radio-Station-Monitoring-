#!/usr/bin/env bash
# Install (or update) the radio monitor on a Debian/Ubuntu VM, e.g. a Google
# Compute Engine instance, as a systemd service that runs 24/7.
#
# Runs automatically as the VM's startup script (see README "Run it on Google
# Cloud"), or by hand on the VM:
#
#   curl -fsSL https://raw.githubusercontent.com/R3dLine23/Radio-Station-Monitoring-/main/deploy/gcp/install.sh | sudo NTFY_TOPIC=your-topic bash
#
# Safe to run again: it pulls the latest code, keeps your config.toml, and
# restarts the service.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/R3dLine23/Radio-Station-Monitoring-.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/radio-monitor}"
APP_USER=radiomon
SERVICE=radio-monitor

log() { echo "[radio-monitor install] $*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (use sudo)." >&2
  exit 1
fi

# On Compute Engine, let instance metadata supply the ntfy topic so the
# whole setup is a single "gcloud compute instances create" command.
gce_attr() {
  curl -fs -m 2 -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null || true
}
NTFY_TOPIC="${NTFY_TOPIC:-$(gce_attr ntfy-topic)}"

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
command -v git >/dev/null && command -v python3 >/dev/null || {
  apt-get update -qq
  apt-get install -y -qq git python3 ca-certificates
}

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11+ is required (found $(python3 --version)). Use a Debian 12 or Ubuntu 24.04 image." >&2
  exit 1
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

if [[ -d "$APP_DIR/.git" ]]; then
  log "updating code in $APP_DIR"
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch -q origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  log "cloning $REPO_URL into $APP_DIR"
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  chown -R "$APP_USER:" "$APP_DIR"
fi

CONFIG="$APP_DIR/config.toml"
if [[ ! -f "$CONFIG" ]]; then
  log "creating $CONFIG from the example"
  cp "$APP_DIR/config.example.toml" "$CONFIG"
  # Console alerts would only reach the system log here, so turn off the bell.
  sed -i '/^\[notify\.console\]/,/^\[/ s/^bell = true/bell = false/' "$CONFIG"
  if [[ -n "$NTFY_TOPIC" ]]; then
    log "enabling ntfy push alerts on topic '$NTFY_TOPIC'"
    sed -i '/^\[notify\.ntfy\]/,/^\[/ {
      s/^enabled = false/enabled = true/
      s/^topic = .*/topic = "'"$NTFY_TOPIC"'"/
    }' "$CONFIG"
  else
    log "WARNING: no NTFY_TOPIC given; edit $CONFIG to turn on phone alerts"
  fi
  chown "$APP_USER:" "$CONFIG"
  chmod 600 "$CONFIG"
fi

log "installing systemd service"
sed -e "s|^User=.*|User=$APP_USER|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$APP_DIR|" \
    "$APP_DIR/deploy/radio-monitor.service" > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable -q "$SERVICE"

log "checking that the stations are reachable and sending song titles"
sudo -u "$APP_USER" sh -c "cd '$APP_DIR' && python3 -m radio_monitor --probe --probe-seconds 20" || \
  log "WARNING: probe failed (see above). The service will keep retrying."

systemctl restart "$SERVICE"
log "done. Service status: $(systemctl is-active "$SERVICE")"
log "watch the live log with:   sudo journalctl -u $SERVICE -f"
log "edit settings with:        sudo nano $CONFIG && sudo systemctl restart $SERVICE"
