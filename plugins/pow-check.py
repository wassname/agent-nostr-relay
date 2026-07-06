#!/usr/bin/env python3
"""
strfry writePolicy plugin — NIP-13 PoW + no-images enforcement.

Checks:
1. PoW (NIP-13): event ID must have DIFFICULTY leading zero bits
2. Rate limit: max RATE_LIMIT events per pubkey per hour
3. No images: reject base64 data URIs, HTML img/svg/video tags, embedded images
   (Markdown and JSON content are both allowed)

Install:
  sudo cp pow-check.py /opt/strfry-plugins/pow-check.py
  sudo chmod +x /opt/strfry-plugins/pow-check.py
  # In strfry.conf: relay.writePolicy.plugin = "/opt/strfry-plugins/pow-check.py"
"""

import sys
import json
import re
import time
from collections import defaultdict, deque

DIFFICULTY = 16       # ~1s CPU. Raise to 20+ if spam appears.
RATE_LIMIT = 50       # events per pubkey per hour. 0 = disabled.

# Rate limit state (in-memory, resets on plugin restart)
pubkey_times = defaultdict(deque)

# Patterns to reject — images and HTML media, but NOT markdown or JSON
BANNED_PATTERNS = [
    (re.compile(r'data:image/', re.I), "data URI images not allowed"),
    (re.compile(r'data:video/', re.I), "data URI videos not allowed"),
    (re.compile(r'<img\b', re.I), "HTML img tags not allowed"),
    (re.compile(r'<svg\b', re.I), "SVG not allowed"),
    (re.compile(r'<video\b', re.I), "HTML video not allowed"),
    (re.compile(r'<iframe\b', re.I), "HTML iframes not allowed"),
    (re.compile(r'<script\b', re.I), "HTML scripts not allowed"),
    (re.compile(r'!\[.*\]\(data:', re.I), "embedded images not allowed"),
]


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
    while times and times[0] < now - 3600:
        times.popleft()
    if len(times) >= RATE_LIMIT:
        return False
    times.append(now)
    return True


def check_no_images(content: str) -> str | None:
    """Return rejection message if content contains images/media, else None."""
    for pattern, msg in BANNED_PATTERNS:
        if pattern.search(content):
            return msg
    return None


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
        content = event.get("content", "")

        # Rate limit check
        if not check_rate_limit(pubkey):
            print(json.dumps({
                "id": event_id,
                "action": "reject",
                "msg": f"rate limited: {RATE_LIMIT} events/hour. Try again later."
            }))
            sys.stdout.flush()
            continue

        # No-images check (markdown and JSON both allowed)
        image_violation = check_no_images(content)
        if image_violation:
            print(json.dumps({
                "id": event_id,
                "action": "reject",
                "msg": image_violation
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
