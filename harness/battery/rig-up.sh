#!/bin/sh
# Bring up the T3 grading rig: headless chrome-for-testing on the GPU box with a
# CDP port, and an ssh tunnel so web_probe.py (default --cdp http://localhost:9223)
# can reach it from the jail. Idempotent. See README.md for the install.
HOST=${WEB_PROBE_REMOTE_HOST:-ollama}
CHROME=${WEB_PROBE_REMOTE_CHROME:-'~/chrome-test/chrome-linux64/chrome'}
ssh -o BatchMode=yes "$HOST" "curl -s -m 2 http://127.0.0.1:9222/json/version >/dev/null || { rm -rf /tmp/web-probe-profile; setsid $CHROME --headless=new --disable-gpu --enable-unsafe-swiftshader --no-sandbox --remote-debugging-port=9222 --user-data-dir=/tmp/web-probe-profile --window-size=1280,800 about:blank > /tmp/web-probe-chrome.log 2>&1 < /dev/null & }; for i in \$(seq 1 20); do curl -s -m 2 http://127.0.0.1:9222/json/version >/dev/null && { echo 'remote chrome up'; exit 0; }; sleep 2; done; echo 'remote chrome did not come up'; exit 1" || exit 1
pgrep -f "[s]sh.*-L 9223:localhost:9222" >/dev/null || daemon -f ssh -o BatchMode=yes -N -L 9223:localhost:9222 "$HOST"
for i in $(seq 1 20); do curl -s -m 2 http://localhost:9223/json/version >/dev/null && { echo "tunnel up"; exit 0; }; sleep 1; done
echo "tunnel never came up"; exit 1
