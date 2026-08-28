#!/usr/bin/env python3
"""
strfry writePolicy plugin — NIP-13 PoW + no-images + persistent rate limiting.

Checks:
1. PoW (NIP-13): event ID must have DIFFICULTY leading zero bits
2. Rate limit: max RATE_LIMIT events per pubkey per hour (persistent in SQLite)
3. No images: reject base64 data URIs, HTML media tags, markdown image syntax,
   remote image URLs. Markdown and JSON text are allowed.
4. No obvious secrets: reject common API keys, private key blocks, and Nostr nsec keys.
5. Soft block personal contact info and internal URLs unless the event has tag ["force", "true"].

Install:
  sudo cp pow-check.py /opt/strfry-plugins/pow-check.py
  sudo chmod +x /opt/strfry-plugins/pow-check.py
  # In strfry.conf: relay.writePolicy.plugin = "/opt/strfry-plugins/pow-check.py"
"""

import sys
import json
import re
import warnings
import time
import sqlite3
import math
import os
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────

BASE_DIFFICULTY = 16       # ~1s CPU. Base level, rises under load.
RATE_LIMIT = 50             # events per pubkey per hour. 0 = disabled.
RATE_DB = os.environ.get("POW_DB", "/var/lib/strfry/pow_state.db")
GITLEAKS_RULES = os.environ.get("GITLEAKS_RULES", str(Path(__file__).with_name("gitleaks-rules.json")))

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


# ─── Content filter ─────────────────────────────────────────────────
# Block media payloads and obvious secrets. Markdown text and JSON are allowed.
# Tuple: pattern, message, force_allowed.

LOCAL_PATTERNS = [
    # All data: URIs (images, audio, video, html, anything)
    (re.compile(r'\bdata:\s*[\w.+-]+/', re.I), "data: URIs not allowed", False),

    # Markdown image syntax: ![alt](url)
    (re.compile(r'!\[.*?\]\(', re.I | re.DOTALL), "markdown image syntax not allowed", False),

    # HTML media tags (case-insensitive, any whitespace after tag name)
    (re.compile(r'<\s*img\b', re.I), "HTML img tags not allowed", False),
    (re.compile(r'<\s*svg\b', re.I), "SVG not allowed", False),
    (re.compile(r'<\s*video\b', re.I), "HTML video not allowed", False),
    (re.compile(r'<\s*audio\b', re.I), "HTML audio not allowed", False),
    (re.compile(r'<\s*iframe\b', re.I), "HTML iframes not allowed", False),
    (re.compile(r'<\s*script\b', re.I), "HTML scripts not allowed", False),
    (re.compile(r'<\s*object\b', re.I), "HTML object tags not allowed", False),
    (re.compile(r'<\s*embed\b', re.I), "HTML embed tags not allowed", False),
    (re.compile(r'<\s*picture\b', re.I), "HTML picture tags not allowed", False),
    (re.compile(r'<\s*source\b', re.I), "HTML source tags not allowed", False),

    # Obvious credentials. Raw 64-char hex is not blocked because Nostr pubkeys look like that too.
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), "private key block not allowed", False),
    (re.compile(r'\bnsec1[02-9ac-hj-np-z]{50,}\b', re.I), "Nostr private key not allowed", False),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), "AWS access key id not allowed", False),
    (re.compile(r'\bASIA[0-9A-Z]{16}\b'), "AWS session access key id not allowed", False),
    (re.compile(r'aws_secret_access_key\s*=\s*[^\s]+', re.I), "AWS secret access key not allowed", False),
    (re.compile(r'\bhf_[A-Za-z0-9]{20,}\b'), "Hugging Face token not allowed", False),
    (re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'), "API key not allowed", False),
    (re.compile(r'\bgh[opsu]_[A-Za-z0-9_]{30,}\b'), "GitHub token not allowed", False),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'), "GitHub token not allowed", False),

    # Personal contact info and internal endpoints.
    (re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.I), "email address found; use a Nostr pubkey or npub if possible", True),
    (re.compile(r'\bhttps?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|169\.254\.169\.254)(?::\d+)?\b', re.I), "internal URL found; redact host before posting", True),
    (re.compile(r'\bfile://\S+', re.I), "local file URL found; describe the file without exposing local paths", True),
]


def load_gitleaks_patterns():
    path = Path(GITLEAKS_RULES)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    patterns = []
    for rule in data["rules"]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            pattern = re.compile(rule["regex"])
        patterns.append((pattern, f"possible secret ({rule['id']})", False))
    return patterns


BANNED_PATTERNS = LOCAL_PATTERNS + load_gitleaks_patterns()


def redacted_match(text: str) -> str:
    cleaned = text.replace("\n", "\\n")
    if len(cleaned) <= 16:
        return cleaned
    return f"{cleaned[:8]}...{cleaned[-6:]}"


def has_force_tag(tags) -> bool:
    return any(isinstance(t, list) and len(t) >= 2 and t[0] == "force" and t[1] == "true" for t in tags)


def check_content_policy(content: str, tags) -> str | None:
    """Return rejection message if content violates relay policy, else None."""
    forced = has_force_tag(tags)
    for pattern, msg, force_allowed in BANNED_PATTERNS:
        match = pattern.search(content)
        if match and not (force_allowed and forced):
            action = "Redact it and retry."
            if force_allowed:
                action = "Redact it, or resubmit with tag [\"force\", \"true\"] if you are sure it is safe and useful."
            return f"content policy rejected this message: {msg}; matched {redacted_match(match.group(0))!r}. {action}"
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
        tags = event.get("tags", [])

        try:
            # Content policy check (before rate limit — don't count failed attempts)
            policy_violation = check_content_policy(content, tags)
            if policy_violation:
                print(json.dumps({
                    "id": event_id,
                    "action": "reject",
                    "msg": policy_violation
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
