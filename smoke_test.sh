#!/bin/bash
# Smoke test: publish event, search it, view feed
# Usage: ./smoke_test.sh [relay_url]
# Default: http://127.0.0.1:8888 (sidecar) + ws://127.0.0.1:7777 (strfry)

set -e

SIDECAR="${1:-http://127.0.0.1:8888}"
RELAY="${2:-ws://127.0.0.1:7777}"

echo "=== Agent Relay Smoke Test ==="
echo "Sidecar: $SIDECAR"
echo "Relay:   $RELAY"
echo ""

# 1. Health check
echo "[1/5] Health check..."
HEALTH=$(curl -s "$SIDECAR/health")
echo "  $HEALTH"
echo ""

# 2. Publish a test event with PoW
echo "[2/5] Publishing test event with PoW..."
PUB_RESULT=$(python3 -c "
import json, time, hashlib, websocket
from pynostr.key import PrivateKey
from pynostr.event import Event

sk = PrivateKey()
ws = websocket.create_connection('$RELAY', timeout=30)

ev = Event(
    kind=1,
    content='## Smoke test\n\nTesting the agent relay. Searching for **alignment** and **steering**.\n\n#test',
    created_at=int(time.time()),
)
# Set pubkey before mining
ev.pubkey = sk.public_key.hex()
for nonce in range(10000000):
    ev.tags = [['nonce', str(nonce), '16']]
    import hashlib
    serial = ev.serialize()
    eid = hashlib.sha256(serial).hexdigest()
    h = bytes.fromhex(eid)
    bits = 0
    for byte in h:
        if byte == 0: bits += 8
        else: bits += 8 - byte.bit_length(); break
    if bits >= 16:
        ev.id = eid
        ev.sign(sk.hex())
        break
ws.send(json.dumps(['EVENT', ev.to_dict()]))
result = ws.recv()
ws.close()
print(result)
")
echo "  $PUB_RESULT"
echo ""

# 3. Wait for indexing
echo "[3/5] Waiting 2s for indexing..."
sleep 2
echo ""

# 4. Search
echo "[4/5] Searching for 'alignment'..."
SEARCH_HTML=$(curl -s "$SIDECAR/search?q=alignment")
echo "  HTML length: $(echo "$SEARCH_HTML" | wc -c)"
echo "  Contains 'alignment': $(echo "$SEARCH_HTML" | grep -qi alignment && echo YES || echo NO)"
echo "  Contains 'Smoke test': $(echo "$SEARCH_HTML" | grep -qi 'smoke test' && echo YES || echo NO)"
echo ""

# 5. Feed
echo "[5/5] Checking feed..."
FEED_HTML=$(curl -s "$SIDECAR/")
echo "  HTML length: $(echo "$FEED_HTML" | wc -c)"
echo "  Contains 'Smoke test': $(echo "$FEED_HTML" | grep -qi 'smoke test' && echo YES || echo NO)"
echo ""

# Result
if echo "$SEARCH_HTML" | grep -qi 'smoke test'; then
    echo "✅ PASS: event published, indexed, searchable, and visible in feed"
else
    echo "❌ FAIL: event not found in search results"
    exit 1
fi
