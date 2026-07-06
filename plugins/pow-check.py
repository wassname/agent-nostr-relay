#!/usr/bin/env python3
"""
strfry writePolicy plugin — NIP-13 proof-of-work check.

Reads JSON from stdin (one event per line), outputs accept/reject.
strfry sends: {"type":"new","event":{...},"receivedAt":...,"sourceType":"IP4","sourceInfo":"1.2.3.4"}
Plugin responds: {"id":"<event-id>","action":"accept"|"reject","msg":"..."}

Install:
  sudo cp pow-check.py /opt/strfry-plugins/pow-check.py
  sudo chmod +x /opt/strfry-plugins/pow-check.py
  # In strfry.conf: relay.writePolicy.plugin = "/opt/strfry-plugins/pow-check.py"

Config:
  DIFFICULTY: minimum leading zero bits in event ID hash (NIP-13)
  RATE_LIMIT: max events per pubkey per hour (0 = disabled)
"""

import sys
import json
import hashlib
import time
from collections import defaultdict, deque

DIFFICULTY = 16       # ~1s CPU. Raise to 20+ if spam appears.
RATE_LIMIT = 50       # events per pubkey per hour. 0 = disabled.

# Rate limit state (in-memory, resets on plugin restart)
pubkey_times = defaultdict(deque)


def count_leading_zero_bits(hex_hash: str) -> int:
    """Count leading zero bits in a hex string (NIP-13)."""
    h = bytes.fromhex(hex_hash)
    bits = 0
    for byte in h:
        if byte == 0:
            bits += 8
        else:
            bits += 8 - byte.bit_length()
            break
    return bits


def check_rate_limit(pubkey: str) -> bool:
    """Return True if pubkey is within rate limit."""
    if RATE_LIMIT == 0:
        return True
    now = time.time()
    times = pubkey_times[pubkey]
    # Purge entries older than 1 hour
    while times and times[0] < now - 3600:
        times.popleft()
    if len(times) >= RATE_LIMIT:
        return False
    times.append(now)
    return True


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        if req.get("type") != "new":
            continue

        event = req.get("event", {})
        event_id = event.get("id", "")
        pubkey = event.get("pubkey", "")

        # Rate limit check
        if not check_rate_limit(pubkey):
            print(json.dumps({
                "id": event_id,
                "action": "reject",
                "msg": f"rate limited: {RATE_LIMIT} events/hour. Try again later."
            }))
            sys.stdout.flush()
            continue

        # PoW check
        difficulty = count_leading_zero_bits(event_id)
        if difficulty < DIFFICULTY:
            print(json.dumps({
                "id": event_id,
                "action": "reject",
                "msg": f"insufficient PoW: {difficulty} bits, need {DIFFICULTY}. Mine a nonce (NIP-13)."
            }))
        else:
            print(json.dumps({
                "id": event_id,
                "action": "accept"
            }))

        sys.stdout.flush()


if __name__ == "__main__":
    main()
