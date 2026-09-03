#!/bin/sh
HOST=${WEB_PROBE_REMOTE_HOST:-ollama}
pkill -f "ssh.*-L 9223:localhost:9222" 2>/dev/null
ssh -o BatchMode=yes "$HOST" 'pkill -f remote-debugging-port=9222; rm -rf /tmp/web-probe-profile /tmp/web-probe-*.html' 2>/dev/null
echo "rig down"
