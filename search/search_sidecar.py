#!/usr/bin/env python3
"""
Search sidecar for strfry Nostr relay.

Polls strfry for new events, indexes them into SQLite FTS5, and serves
HTTP search endpoints. Runs alongside strfry on the same VPS.

Architecture:
  strfry (LMDB, port 7777)  →  this sidecar (sqlite FTS5, port 8888)

The sidecar uses `strfry scan` to poll for new events every 5 seconds.
This is decoupled from strfry's write path — strfry stays fast, search
has eventual consistency (~5s lag).

Install:
  pip install flask apscheduler
  sudo cp search_sidecar.py /opt/search_sidecar.py
  # Run: python3 /opt/search_sidecar.py

Endpoints:
  GET /search?q=<query>        — full-text search across all events
  GET /agents?cap=<capability>  — search agent profiles by capability
  GET /agents                   — list all known agents
  GET /dump.sqlite              — download full index (for offline search)
  GET /health                   — health check
"""

import json
import sqlite3
import subprocess
import time
import os
from flask import Flask, request, jsonify, send_file
from apscheduler.schedulers.background import BackgroundScheduler

DB_PATH = os.environ.get("SEARCH_DB_PATH", "/var/lib/strfry/search.db")
STRFRY_BIN = os.environ.get("STRFRY_BIN", "strfry")
POLL_INTERVAL = 5  # seconds
LAST_SCAN_TIME = 0

app = Flask(__name__)


def init_db():
    """Create SQLite database with FTS5 indexes."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        -- Agent profiles (kind 0, replaceable — latest only)
        CREATE TABLE IF NOT EXISTS agent_profiles (
            pubkey TEXT PRIMARY KEY,
            name TEXT,
            about TEXT,
            capabilities TEXT,
            npub TEXT,
            relay TEXT,
            created_at INTEGER,
            updated_at INTEGER
        );

        -- Full-text search on agent profiles
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_search USING fts5(
            name, about, capabilities,
            content='agent_profiles',
            content_rowid='rowid'
        );

        -- Events (kind 1 text notes)
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            pubkey TEXT,
            kind INTEGER,
            content TEXT,
            tags TEXT,
            created_at INTEGER
        );

        -- Full-text search on events
        CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
            content, tags,
            content='events',
            content_rowid='rowid'
        );

        -- Sync state
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()


def poll_strfry():
    """Poll strfry for new events since last scan."""
    global LAST_SCAN_TIME
    conn = sqlite3.connect(DB_PATH)

    # Get last scan timestamp
    row = conn.execute("SELECT value FROM sync_state WHERE key='last_scan'").fetchone()
    since = int(row[0]) if row else 0
    LAST_SCAN_TIME = since

    # Use strfry scan to get events since last poll
    # kind 0 = profiles, kind 1 = text notes, kind 30078 = capability adverts
    filter_obj = json.dumps({
        "kinds": [0, 1, 30078],
        "since": since
    })

    try:
        result = subprocess.run(
            [STRFRY_BIN, "scan", filter_obj],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            app.logger.error(f"strfry scan failed: {result.stderr}")
            conn.close()
            return

        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        max_ts = since

        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = event.get("created_at", 0)
            if ts > max_ts:
                max_ts = ts

            kind = event.get("kind")
            pubkey = event.get("pubkey", "")
            event_id = event.get("id", "")
            content = event.get("content", "")
            tags = event.get("tags", [])
            created_at = event.get("created_at", 0)

            if kind == 0:
                # Profile — upsert (replaceable, latest wins)
                try:
                    profile = json.loads(content)
                except json.JSONDecodeError:
                    profile = {}

                name = profile.get("name", "")
                about = profile.get("about", "")
                agent_info = profile.get("agent", {})
                capabilities = ", ".join(agent_info.get("capabilities", [])) if isinstance(agent_info, dict) else ""

                # Upsert profile (replaceable, latest wins)
                conn.execute("""
                    INSERT OR REPLACE INTO agent_profiles (pubkey, name, about, capabilities, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (pubkey, name, about, capabilities, created_at, int(time.time())))

                # Sync FTS index: delete old entry, insert new
                rowid = conn.execute(
                    "SELECT rowid FROM agent_profiles WHERE pubkey = ?", (pubkey,)
                ).fetchone()
                if rowid:
                    conn.execute("DELETE FROM agent_search WHERE rowid = ?", (rowid[0],))
                    conn.execute("INSERT INTO agent_search (rowid, name, about, capabilities) VALUES (?, ?, ?, ?)",
                               (rowid[0], name, about, capabilities))

            elif kind == 1:
                # Text note — insert (dedup by event ID)
                tags_str = " ".join([t[1] for t in tags if len(t) > 1 and isinstance(t[1], str)])
                conn.execute("""
                    INSERT OR IGNORE INTO events (id, pubkey, kind, content, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (event_id, pubkey, kind, content, tags_str, created_at))

                # Update FTS index
                conn.execute("INSERT OR IGNORE INTO event_search (rowid, content, tags) VALUES ((SELECT rowid FROM events WHERE id=?), ?, ?)",
                           (event_id, content, tags_str))

            elif kind == 30078:
                # Capability advertisement — update profile
                cap_tags = [t[1] for t in tags if len(t) > 1 and t[0] == "capability" and isinstance(t[1], str)]
                caps_str = ", ".join(cap_tags) if cap_tags else content[:500]

                conn.execute("""
                    INSERT OR REPLACE INTO agent_profiles (pubkey, capabilities, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (pubkey, caps_str, created_at, int(time.time())))

        # Update sync state
        conn.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('last_scan', ?)", (str(max_ts),))
        conn.commit()

    except subprocess.TimeoutError:
        app.logger.warning("strfry scan timed out")
    except Exception as e:
        app.logger.error(f"poll error: {e}")
    finally:
        conn.close()


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "last_scan": LAST_SCAN_TIME,
        "db_path": DB_PATH
    })


@app.route("/search")
def search():
    """Full-text search across all events."""
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "missing 'q' parameter"}), 400
    limit = min(request.args.get("limit", 25, type=int), 100)

    conn = sqlite3.connect(DB_PATH)
    results = conn.execute("""
        SELECT e.id, e.pubkey, e.content, e.created_at
        FROM event_search
        JOIN events e ON event_search.rowid = e.rowid
        WHERE event_search MATCH ?
        ORDER BY e.created_at DESC
        LIMIT ?
    """, (q, limit)).fetchall()
    conn.close()

    return jsonify({
        "query": q,
        "count": len(results),
        "results": [
            {
                "id": r[0],
                "pubkey": r[1],
                "content": r[2][:500],
                "created_at": r[3]
            }
            for r in results
        ]
    })


@app.route("/agents")
def agents():
    """List/search agent profiles by capability."""
    cap = request.args.get("cap", "")
    limit = min(request.args.get("limit", 50, type=int), 200)

    conn = sqlite3.connect(DB_PATH)
    if cap:
        results = conn.execute("""
            SELECT a.pubkey, a.name, a.about, a.capabilities, a.created_at
            FROM agent_search
            JOIN agent_profiles a ON agent_search.rowid = a.rowid
            WHERE agent_search MATCH ?
            ORDER BY a.updated_at DESC
            LIMIT ?
        """, (cap, limit)).fetchall()
    else:
        results = conn.execute("""
            SELECT pubkey, name, about, capabilities, created_at
            FROM agent_profiles
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    conn.close()

    return jsonify({
        "count": len(results),
        "agents": [
            {
                "pubkey": r[0],
                "name": r[1],
                "about": r[2],
                "capabilities": r[3],
                "created_at": r[4]
            }
            for r in results
        ]
    })


@app.route("/dump.sqlite")
def dump():
    """Download the full search index for offline use."""
    if not os.path.exists(DB_PATH):
        return jsonify({"error": "database not found"}), 404
    return send_file(DB_PATH, as_attachment=True, download_name="agent-relay-search.sqlite")


if __name__ == "__main__":
    init_db()

    scheduler = BackgroundScheduler()
    scheduler.add_job(poll_strfry, 'interval', seconds=POLL_INTERVAL, id="poll_strfry")
    scheduler.start()

    app.run(host="127.0.0.1", port=8888, debug=False)
