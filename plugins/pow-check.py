#!/usr/bin/env python3
"""
strfry writePolicy plugin — NIP-13 PoW + no-images + persistent rate limiting.

Checks:
1. PoW (NIP-13): event ID must have DIFFICULTY leading zero bits
2. Rate limit: max RATE_LIMIT events per pubkey per hour (persistent in SQLite)
3. No images: reject base64 data URIs, HTML img/svg/video tags, embedded images
   (Markdown and JSON content are both allowed)

Install:
  sudo cp pow-check.py /opt/strfry-plugins/pow-check.py
  sudo chmod +x /opt/strfry-plugins/pow-check.py
  # In strfry.conf: relay.writePolicy.plugin = "/opt/strfry-plugins/pow-check.py"

Config:
  DIFFICULTY: minimum leading zero bits in event ID hash (NIP-13)
  RATE_LIMIT: max events per pubkey per hour (0 = disabled)
  RATE_DB: path to SQLite database for persistent rate limit state
"""

import sys
import json
import re
import time
import sqlite3
import math
import os
from collections import deque

# ─── Config ──────────────────────────────────────────────────────────

BASE_DIFFICULTY = 16       # ~1s CPU. Base level, rises under load.
RATE_LIMIT = 50             # events per pubkey per hour. 0 = disabled.
RATE_DB = os.environ.get("POW_DB", "/var/lib/strfry/pow_state.db")

# Dynamic PoW: difficulty rises when write rate exceeds threshold
LOAD_WINDOW = 300           # seconds — rolling window for load calculation
LOAD_THRESHOLD = 100        # events per LOAD_WINDOW before difficulty rises

# ─── Persistent state ────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(RATE_DB), exist_ok=True)
    conn = sqlite3.connect(RATE_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rate_limit (
            pubkey TEXT,
            ts INTEGER,
            PRIMARY KEY (pubkey, ts)
        );
        CREATE TABLE IF NOT EXISTS write_log (
            ts INTEGER PRIMARY KEY
        );
        CREATE INDEX IF NOT EXISTS idx_rate_pubkey ON rate_limit(pubkey);
    """)
    conn.commit()
    conn.close()

init_db()

# ─── Rate limit (persistent) ─────────────────────────────────────────

def check_rate_limit(pubkey: str) -> bool:
    """Return True if pubkey is within rate limit. Persistent across restarts."""
    if RATE_LIMIT == 0:
        return True
    now = int(time.time())
    cutoff = now - 3600
    conn = sqlite3.connect(RATE_DB)
    # Purge old entries for this pubkey
    conn.execute("DELETE FROM rate_limit WHERE pubkey = ? AND ts < ?", (pubkey, cutoff))
    # Count recent
    count = conn.execute(
        "SELECT COUNT(*) FROM rate_limit WHERE pubkey = ? AND ts >= ?",
        (pubkey, cutoff)
    ).fetchone()[0]
    if count >= RATE_LIMIT:
        conn.close()
        return False
    # Record this event
    conn.execute("INSERT OR REPLACE INTO rate_limit (pubkey, ts) VALUES (?, ?)", (pubkey, now))
    conn.commit()
    conn.close()
    return True


# ─── Dynamic PoW difficulty ──────────────────────────────────────────

def get_current_difficulty() -> int:
    """Calculate dynamic PoW difficulty based on recent write load.
    
    AMM model: base difficulty rises logarithmically when write rate
    exceeds LOAD_THRESHOLD events per LOAD_WINDOW seconds.
    """
    now = int(time.time())
    cutoff = now - LOAD_WINDOW
    conn = sqlite3.connect(RATE_DB)
    count = conn.execute(
        "SELECT COUNT(*) FROM write_log WHERE ts >= ?", (cutoff,)
    ).fetchone()[0]
    conn.close()
    
    if count <= LOAD_THRESHOLD:
        return BASE_DIFFICULTY
    
    # difficulty = base + log2(rate / threshold)
    ratio = count / LOAD_THRESHOLD
    extra = int(math.log2(ratio))
    return BASE_DIFFICULTY + max(extra, 0)


def log_write():
    """Record a write event for load calculation."""
    now = int(time.time())
    conn = sqlite3.connect(RATE_DB)
    conn.execute("INSERT OR REPLACE INTO write_log (ts) VALUES (?)", (now,))
    # Purge old entries
    conn.execute("DELETE FROM write_log WHERE ts < ?", (now - LOAD_WINDOW * 2))
    conn.commit()
    conn.close()


# ─── PoW check (NIP-13) ─────────────────────────────────────────────

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


# ─── No-images content filter ────────────────────────────────────────

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


def check_no_images(content: str) -> str | None:
    """Return rejection message if content contains images/media, else None."""
    for pattern, msg in BANNED_PATTERNS:
        if pattern.search(content):
            return msg
    return None


# ─── Main ────────────────────────────────────────────────────────────

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

        # Rate limit check (persistent)
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

        # Dynamic PoW check
        difficulty = count_leading_zero_bits(event_id)
        required = get_current_difficulty()
        if difficulty < required:
            print(json.dumps({
                "id": event_id,
                "action": "reject",
                "msg": f"insufficient PoW: {difficulty} bits, need {required}. Mine a nonce (NIP-13)."
            }))
        else:
            log_write()
            print(json.dumps({
                "id": event_id,
                "action": "accept"
            }))

        sys.stdout.flush()


if __name__ == "__main__":
    main()
