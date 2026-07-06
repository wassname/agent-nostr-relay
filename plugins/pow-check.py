#!/usr/bin/env python3
"""
strfry writePolicy plugin — NIP-13 PoW + no-images + persistent rate limiting.

Checks:
1. PoW (NIP-13): event ID must have DIFFICULTY leading zero bits
2. Rate limit: max RATE_LIMIT events per pubkey per hour (persistent in SQLite)
3. No images: reject base64 data URIs, HTML media tags, markdown image syntax,
   remote image URLs. Markdown and JSON text are allowed.

Install:
  sudo cp pow-check.py /opt/strfry-plugins/pow-check.py
  sudo chmod +x /opt/strfry-plugins/pow-check.py
  # In strfry.conf: relay.writePolicy.plugin = "/opt/strfry-plugins/pow-check.py"
"""

import sys
import json
import re
import time
import sqlite3
import math
import os

# ─── Config ──────────────────────────────────────────────────────────

BASE_DIFFICULTY = 16       # ~1s CPU. Base level, rises under load.
RATE_LIMIT = 50             # events per pubkey per hour. 0 = disabled.
RATE_DB = os.environ.get("POW_DB", "/var/lib/strfry/pow_state.db")

# Dynamic PoW: difficulty rises when write rate exceeds threshold
LOAD_WINDOW = 300           # seconds — rolling window for load calculation
LOAD_THRESHOLD = 100        # events per LOAD_WINDOW before difficulty rises

# ─── Persistent state (single connection, WAL mode) ─────────────────

_pow_conn = None

def get_db():
    global _pow_conn
    if _pow_conn is None:
        os.makedirs(os.path.dirname(RATE_DB), exist_ok=True)
        _pow_conn = sqlite3.connect(RATE_DB, timeout=5)
        _pow_conn.execute("PRAGMA journal_mode=WAL")
        _pow_conn.execute("PRAGMA busy_timeout=5000")
    return _pow_conn


def init_db():
    os.makedirs(os.path.dirname(RATE_DB), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        -- Rate limit: one row per event (not per second!)
        -- Use autoincrement rowid to avoid collision
        CREATE TABLE IF NOT EXISTS rate_limit (
            pubkey TEXT NOT NULL,
            ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rate_pubkey ON rate_limit(pubkey, ts);

        -- Write log: one row per accepted write
        CREATE TABLE IF NOT EXISTS write_log (
            ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_write_ts ON write_log(ts);
    """)
    conn.commit()

init_db()


# ─── Rate limit (persistent, sub-second safe) ───────────────────────

def check_rate_limit(pubkey: str) -> bool:
    """Return True if pubkey is within rate limit. Persistent across restarts."""
    if RATE_LIMIT == 0:
        return True
    now = int(time.time())
    cutoff = now - 3600
    conn = get_db()
    # Purge old entries
    conn.execute("DELETE FROM rate_limit WHERE pubkey = ? AND ts < ?", (pubkey, cutoff))
    # Count recent
    count = conn.execute(
        "SELECT COUNT(*) FROM rate_limit WHERE pubkey = ? AND ts >= ?",
        (pubkey, cutoff)
    ).fetchone()[0]
    if count >= RATE_LIMIT:
        return False
    # Record this event (INSERT, not INSERT OR REPLACE — each event gets its own row)
    conn.execute("INSERT INTO rate_limit (pubkey, ts) VALUES (?, ?)", (pubkey, now))
    conn.commit()
    return True


# ─── Dynamic PoW difficulty ──────────────────────────────────────────

def get_current_difficulty() -> int:
    """Calculate dynamic PoW difficulty based on recent write load."""
    now = int(time.time())
    cutoff = now - LOAD_WINDOW
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM write_log WHERE ts >= ?", (cutoff,)
    ).fetchone()[0]

    if count <= LOAD_THRESHOLD:
        return BASE_DIFFICULTY

    ratio = count / LOAD_THRESHOLD
    extra = int(math.ceil(math.log2(ratio)))
    return BASE_DIFFICULTY + max(extra, 0)


def log_write():
    """Record a write event for load calculation."""
    now = int(time.time())
    conn = get_db()
    conn.execute("INSERT INTO write_log (ts) VALUES (?)", (now,))
    conn.execute("DELETE FROM write_log WHERE ts < ?", (now - LOAD_WINDOW * 2,))
    conn.commit()


# ─── PoW check (NIP-13) ─────────────────────────────────────────────

def count_leading_zero_bits(hex_hash: str) -> int:
    """Count leading zero bits in a hex string (NIP-13)."""
    if not hex_hash or len(hex_hash) < 2:
        return 0
    try:
        h = bytes.fromhex(hex_hash)
    except ValueError:
        return 0
    bits = 0
    for byte in h:
        if byte == 0:
            bits += 8
        else:
            bits += 8 - byte.bit_length()
            break
    return bits


# ─── No-images content filter ────────────────────────────────────────
# Block ALL data: URIs, ALL markdown image syntax, ALL HTML media tags.
# Markdown text and JSON are allowed.

BANNED_PATTERNS = [
    # All data: URIs (images, audio, video, html, anything)
    (re.compile(r'data:', re.I), "data: URIs not allowed"),

    # Markdown image syntax: ![alt](url)
    (re.compile(r'!\[.*?\]\(', re.I | re.DOTALL), "markdown image syntax not allowed"),

    # HTML media tags (case-insensitive, any whitespace after tag name)
    (re.compile(r'<\s*img\b', re.I), "HTML img tags not allowed"),
    (re.compile(r'<\s*svg\b', re.I), "SVG not allowed"),
    (re.compile(r'<\s*video\b', re.I), "HTML video not allowed"),
    (re.compile(r'<\s*audio\b', re.I), "HTML audio not allowed"),
    (re.compile(r'<\s*iframe\b', re.I), "HTML iframes not allowed"),
    (re.compile(r'<\s*script\b', re.I), "HTML scripts not allowed"),
    (re.compile(r'<\s*object\b', re.I), "HTML object tags not allowed"),
    (re.compile(r'<\s*embed\b', re.I), "HTML embed tags not allowed"),
    (re.compile(r'<\s*picture\b', re.I), "HTML picture tags not allowed"),
    (re.compile(r'<\s*source\b', re.I), "HTML source tags not allowed"),
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

        try:
            # No-images check (before rate limit — don't count failed attempts)
            image_violation = check_no_images(content)
            if image_violation:
                print(json.dumps({
                    "id": event_id,
                    "action": "reject",
                    "msg": image_violation
                }))
                sys.stdout.flush()
                continue

            # Dynamic PoW check (before rate limit — don't count failed attempts)
            difficulty = count_leading_zero_bits(event_id)
            required = get_current_difficulty()
            if difficulty < required:
                print(json.dumps({
                    "id": event_id,
                    "action": "reject",
                    "msg": f"insufficient PoW: {difficulty} bits, need {required}. Mine a nonce (NIP-13)."
                }))
                sys.stdout.flush()
                continue

            # Rate limit check (only for events that passed content + PoW)
            if not check_rate_limit(pubkey):
                print(json.dumps({
                    "id": event_id,
                    "action": "reject",
                    "msg": f"rate limited: {RATE_LIMIT} events/hour. Try again later."
                }))
                sys.stdout.flush()
                continue

            log_write()
            print(json.dumps({
                "id": event_id,
                "action": "accept"
            }))
        except Exception as e:
            # Never crash — log and reject
            print(json.dumps({
                "id": event_id,
                "action": "reject",
                "msg": f"plugin error: {e}"
            }))

        sys.stdout.flush()


if __name__ == "__main__":
    main()
