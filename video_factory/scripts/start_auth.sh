#!/bin/bash
set -e

export DISPLAY=:99
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

echo "Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1920x1080x24 -ac &
sleep 2

echo "Starting Fluxbox window manager..."
fluxbox &
sleep 1

echo "Starting x11vnc..."
x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -quiet &
sleep 1

echo "Starting noVNC web interface on port 6080..."
websockify --web /usr/share/novnc 6080 localhost:5900 &

CHROME_BIN=$(find /root/.cache/ms-playwright -name chrome | head -n 1)
echo "Found Chrome at $CHROME_BIN"

echo "Launching Chrome with persistent profile..."
python3 /app/scripts/browser_proxy_bridge.py &
mkdir -p /data/profile /data/output

# Launch Chrome with remote debugging AND display on :99
exec "$CHROME_BIN" \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --disable-dev-shm-usage \
    --disable-quic \
    --proxy-pac-url=http://127.0.0.1:17891/browser-proxy.pac \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --remote-allow-origins=* \
    --user-data-dir=/data/profile \
    --window-size=1920,1080 \
    --start-maximized \
    "https://myaccount.google.com/"  # static page: the Flow landing page animates and burns ~1.7 of 2 cores while idle
