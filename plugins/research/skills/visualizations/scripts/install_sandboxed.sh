#!/usr/bin/env bash
# Install (or update) plot-server as a sandboxed system service. Run from your own account:
#   sudo bash <skill>/scripts/install_sandboxed.sh [--new-token]
# Reads your [plots] table from ~/.config/rigsync/machines.toml, creates the plotserver account,
# installs a root-owned copy of the server, writes /etc/plot-server/plot-server.toml, replaces
# your user-level plot-server service, and starts the sandboxed one. Re-run after a release that
# changes the server. See references/plot-server.md.
set -euo pipefail

NEW_TOKEN=""
[ "${1:-}" = "--new-token" ] && NEW_TOKEN="--new-token"
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
OWNER="${SUDO_USER:-}"
[ -n "$OWNER" ] && [ "$OWNER" != root ] || { echo "run through sudo from your own account" >&2; exit 1; }
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
REGISTRY="$OWNER_HOME/.config/rigsync/machines.toml"
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB=/usr/local/lib/plot-server
ETC=/etc/plot-server
CONF="$ETC/plot-server.toml"
PY=/usr/bin/python3

echo "== account"
if ! id plotserver >/dev/null 2>&1; then
  useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin plotserver
fi
id plotserver

echo "== code (root-owned copy)"
install -d -m 0755 "$LIB" "$LIB/scripts" "$LIB/assets"
install -m 0644 "$SKILL/scripts/plot_server.py" "$LIB/scripts/plot_server.py"
install -m 0644 "$SKILL/assets/plot-browser.html" "$LIB/assets/plot-browser.html"

echo "== config"
install -d -m 0750 -o root -g plotserver "$ETC"
"$PY" "$LIB/scripts/plot_server.py" --registry "$REGISTRY" system-config --out "$CONF" $NEW_TOKEN
chown root:plotserver "$CONF"
chmod 0640 "$CONF"

echo "== units"
install -m 0644 "$SKILL/assets/plot-server.sandboxed.service" /etc/systemd/system/plot-server.service
install -m 0644 "$SKILL/assets/plot-server-sync.service" /etc/systemd/system/plot-server-sync.service
install -m 0644 "$SKILL/assets/plot-server-sync.timer" /etc/systemd/system/plot-server-sync.timer

echo "== retire the user-level service, if any"
systemctl --machine="$OWNER@.host" --user disable --now plot-server.service 2>/dev/null || true
rm -f "$OWNER_HOME/.config/systemd/user/plot-server.service"
systemctl --machine="$OWNER@.host" --user daemon-reload 2>/dev/null || true

echo "== publish plots/ and start"
"$PY" "$LIB/scripts/plot_server.py" --registry "$CONF" sync --no-reload
systemctl daemon-reload
systemctl enable plot-server.service plot-server-sync.timer
systemctl restart plot-server.service
systemctl start plot-server-sync.timer

PORT="$("$PY" -c "import tomllib;print(tomllib.load(open('$CONF','rb'))['plots']['port'])")"
for _ in $(seq 40); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break
  sleep 0.25
done
if ! curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null; then
  echo "plot-server did not come up:" >&2
  journalctl -u plot-server.service -n 40 --no-pager >&2
  exit 1
fi
systemctl --no-pager --lines=0 status plot-server.service
echo
echo "plot-server is sandboxed and up on port $PORT."
echo "Bookmark (keep it private):"
"$PY" - "$CONF" <<'PYEOF'
import sys, tomllib
p = tomllib.load(open(sys.argv[1], "rb"))["plots"]
base = p.get("public_url") or f"http://<public address>:{p['port']}"
print(f"  {base}/?token={p['token']}")
PYEOF
