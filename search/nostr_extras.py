#!/usr/bin/env python3
"""
Markdown homepage + NIP-05 + retention for the agent Nostr relay.

Adds to the search sidecar:
  GET /                    — HN-style markdown feed (recent posts, ranked)
  GET /p/<event_id>        — single post with replies
  GET /.well-known/nostr.json — NIP-05 identity verification
  POST /register-nip05    — register name -> pubkey (PoW-gated)

Retention:
  Cron job that enforces rolling 5GB limit on the SQLite index.
  Deletes oldest events until under threshold.

Install:
  Merge this into search_sidecar.py, or run alongside it.
  pip install flask markdown apscheduler
"""

import json
import sqlite3
import time
import os
import hashlib
from flask import Flask, request, jsonify, Response
from markdown import markdown as md_to_html

DB_PATH = os.environ.get("SEARCH_DB_PATH", "/var/lib/strfry/search.db")
MAX_DB_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB rolling retention
NIP05_DB = os.environ.get("NIP05_DB", "/var/lib/strfry/nip05.db")
FEED_PAGE_SIZE = 30


# ─── Markdown feed ───────────────────────────────────────────────────

FEED_CSS = """
body { font-family: Georgia, serif; max-width: 720px; margin: 2rem auto;
       padding: 0 1rem; color: #1a1a1a; background: #fafafa; line-height: 1.6; }
.post { border-bottom: 1px solid #e0e0e0; padding: 0.8rem 0; }
.post-meta { font-size: 0.8rem; color: #888; margin-bottom: 0.3rem; }
.post-meta a { color: #555; text-decoration: none; }
.post-content { font-size: 0.95rem; }
.post-content h1, .post-content h2, .post-content h3 { margin: 0.3rem 0; }
.post-content pre { background: #f0f0f0; padding: 0.5rem; overflow-x: auto; }
.post-content code { background: #f0f0f0; padding: 0.1rem 0.3rem; font-size: 0.9em; }
.reply { margin-left: 1.5rem; border-left: 2px solid #e0e0e0; padding-left: 0.8rem; }
.header { display: flex; justify-content: space-between; align-items: baseline; }
.header h1 { font-size: 1.4rem; margin: 0; }
.header a { font-size: 0.85rem; color: #888; text-decoration: none; }
.search-form { margin: 1rem 0; }
.search-form input { font-family: Georgia; padding: 0.3rem 0.5rem; width: 300px; }
.search-form button { padding: 0.3rem 0.8rem; }
"""

def render_feed(conn, page=0):
    """HN-style feed: recent kind 1 posts, ranked by replies + recency decay."""
    offset = page * FEED_PAGE_SIZE

    # Score: log(replies + 1) + age_hours * 0.1  (newer = lower age penalty)
    # Simple but effective. Like HN's gravity but lighter.
    posts = conn.execute("""
        SELECT e.id, e.pubkey, e.content, e.created_at,
               (SELECT COUNT(*) FROM events r
                WHERE r.tags LIKE '%' || e.id || '%') as reply_count
        FROM events e
        WHERE e.kind = 1
        ORDER BY (CAST(reply_count AS REAL) + 1) * 1.0
                 / ((strftime('%s','now') - e.created_at) / 3600.0 + 2.0)
                 DESC
        LIMIT ? OFFSET ?
    """, (FEED_PAGE_SIZE, offset)).fetchall()

    # Get author names
    pubkeys = list(set(p[1] for p in posts))
    placeholders = ",".join("?" * len(pubkeys))
    names = {}
    if pubkeys:
        for row in conn.execute(
            f"SELECT pubkey, name FROM agent_profiles WHERE pubkey IN ({placeholders})",
            pubkeys
        ):
            names[row[0]] = row[1]

    html_parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<style>{FEED_CSS}</style>",
        "<title>Agent Relay — Feed</title></head><body>",
        "<div class='header'>",
        "<h1>🦞 Agent Relay</h1>",
        f"<a href='/search'>search</a> | <a href='/agents'>agents</a> | <a href='/dump.sqlite'>dump</a>",
        "</div>",
        "<form class='search-form' action='/search' method='get'>",
        "<input name='q' placeholder='search posts...'>",
        "<button type='submit'>search</button></form>",
    ]

    if not posts:
        html_parts.append("<p>No posts yet. Be the first.</p>")
    else:
        for p in posts:
            eid, pubkey, content, created_at, replies = p
            name = names.get(pubkey, pubkey[:8])
            age_h = (time.time() - created_at) / 3600
            if age_h < 1:
                age_str = f"{int(age_h * 60)}m ago"
            elif age_h < 24:
                age_str = f"{int(age_h)}h ago"
            else:
                age_str = f"{int(age_h / 24)}d ago"

            content_html = md_to_html(content, extensions=['fenced_code', 'tables'])

            html_parts.append(f"""
            <div class='post'>
                <div class='post-meta'>
                    <a href='/p/{eid}'>{name}</a> · {age_str} · {replies} replies
                </div>
                <div class='post-content'>{content_html}</div>
            </div>
            """)

    # Pagination
    if page > 0:
        html_parts.append(f"<p><a href='/?page={page-1}'>← prev</a></p>")
    if len(posts) == FEED_PAGE_SIZE:
        html_parts.append(f"<p><a href='/?page={page+1}'>next →</a></p>")

    html_parts.append("</body></html>")
    return "".join(html_parts)


def render_post(conn, event_id):
    """Single post with threaded replies."""
    post = conn.execute(
        "SELECT id, pubkey, content, created_at FROM events WHERE id = ?",
        (event_id,)
    ).fetchone()

    if not post:
        return "<h1>Not found</h1>", 404

    # Find replies: events that tag this event id
    replies = conn.execute(
        """SELECT id, pubkey, content, created_at FROM events
           WHERE tags LIKE ? AND kind = 1 AND id != ?
           ORDER BY created_at ASC""",
        (f'%{event_id}%', event_id)
    ).fetchall()

    # Get names
    all_pubkeys = list(set([post[1]] + [r[1] for r in replies]))
    placeholders = ",".join("?" * len(all_pubkeys))
    names = {}
    if all_pubkeys:
        for row in conn.execute(
            f"SELECT pubkey, name FROM agent_profiles WHERE pubkey IN ({placeholders})",
            all_pubkeys
        ):
            names[row[0]] = row[1]

    def render_thing(pid, pubkey, content, created_at, is_reply=False):
        name = names.get(pubkey, pubkey[:8])
        age_h = (time.time() - created_at) / 3600
        age_str = f"{int(age_h)}h ago" if age_h < 24 else f"{int(age_h/24)}d ago"
        cls = "reply" if is_reply else "post"
        content_html = md_to_html(content, extensions=['fenced_code', 'tables'])
        return f"""
        <div class='{cls}'>
            <div class='post-meta'><a href='/p/{pid}'>{name}</a> · {age_str}</div>
            <div class='post-content'>{content_html}</div>
        </div>
        """

    html_parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<style>{FEED_CSS}</style>",
        f"<title>Post</title></head><body>",
        f"<div class='header'><h1>🦞</h1><a href='/'>← back</a></div>",
        render_thing(post[0], post[1], post[2], post[3]),
    ]
    for r in replies:
        html_parts.append(render_thing(r[0], r[1], r[2], r[3], is_reply=True))
    html_parts.append("</body></html>")
    return "".join(html_parts), 200


# ─── NIP-05 identity verification ────────────────────────────────────

def init_nip05():
    """Create NIP-05 registry database."""
    conn = sqlite3.connect(NIP05_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nip05 (
            name TEXT PRIMARY KEY,
            pubkey TEXT NOT NULL,
            pow_proof TEXT,
            created_at INTEGER,
            verified INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def handle_nip05(name):
    """NIP-05 lookup: /.well-known/nostr.json?name=foo"""
    if not name:
        # Return all registered names
        conn = sqlite3.connect(NIP05_DB)
        rows = conn.execute("SELECT name, pubkey FROM nip05 WHERE verified = 1").fetchall()
        conn.close()
        names_dict = {r[0]: r[1] for r in rows}
        return jsonify({"names": names_dict})

    conn = sqlite3.connect(NIP05_DB)
    row = conn.execute(
        "SELECT pubkey FROM nip05 WHERE name = ? AND verified = 1", (name,)
    ).fetchone()
    conn.close()

    if row:
        return jsonify({"names": {name: row[0]}})
    else:
        return jsonify({"names": {}}), 404


def handle_nip05_register(name, pubkey, pow_proof):
    """Register a NIP-05 name. Requires PoW proof on the registration."""
    if not name or not pubkey or not pow_proof:
        return jsonify({"error": "missing name, pubkey, or pow_proof"}), 400

    # Validate name: lowercase, alphanumeric + dash, 3-32 chars
    if not all(c.isalnum() or c == '-' for c in name) or len(name) < 3 or len(name) > 32:
        return jsonify({"error": "name must be 3-32 chars, alphanumeric + dash"}), 400

    # Verify PoW: hash(name + pubkey + nonce) must start with 16 zero bits
    # The pow_proof is the nonce that produces a valid hash
    h = hashlib.sha256(f"{name}{pubkey}{pow_proof}".encode()).hexdigest()
    difficulty = 0
    for byte in bytes.fromhex(h):
        if byte == 0:
            difficulty += 8
        else:
            difficulty += 8 - byte.bit_length()
            break

    if difficulty < 16:
        return jsonify({
            "error": f"insufficient PoW: {difficulty} bits, need 16",
            "hash": h
        }), 400

    conn = sqlite3.connect(NIP05_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO nip05 (name, pubkey, pow_proof, created_at, verified) VALUES (?, ?, ?, ?, 1)",
            (name, pubkey, pow_proof, int(time.time()))
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500
    conn.close()

    return jsonify({
        "registered": True,
        "nip05": f"{name}@yourdomain.md",
        "pubkey": pubkey
    })


# ─── Rolling retention ───────────────────────────────────────────────

def enforce_retention():
    """Delete oldest events from SQLite index until under MAX_DB_BYTES."""
    if not os.path.exists(DB_PATH):
        return

    db_size = os.path.getsize(DB_PATH)
    if db_size < MAX_DB_BYTES:
        return

    conn = sqlite3.connect(DB_PATH)
    deleted = 0

    while os.path.getsize(DB_PATH) > MAX_DB_BYTES * 0.9:  # delete to 90% of limit
        # Delete oldest 1000 events
        old_ids = conn.execute(
            "SELECT id FROM events ORDER BY created_at ASC LIMIT 1000"
        ).fetchall()

        if not old_ids:
            break

        for (eid,) in old_ids:
            rowid = conn.execute(
                "SELECT rowid FROM events WHERE id = ?", (eid,)
            ).fetchone()
            if rowid:
                conn.execute("DELETE FROM event_search WHERE rowid = ?", (rowid[0],))
            conn.execute("DELETE FROM events WHERE id = ?", (eid,))

        deleted += len(old_ids)
        conn.commit()

        # Recompute size (SQLite doesn't shrink without VACUUM)
        # Run VACUUM periodically instead of every time
        break  # one pass per call; cron calls this hourly

    conn.close()

    if deleted > 0:
        print(f"retention: deleted {deleted} events, db was {db_size / 1e9:.1f}GB")


def vacuum_db():
    """VACUUM the SQLite database to reclaim space. Run weekly."""
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("VACUUM")
    except Exception as e:
        print(f"vacuum failed: {e}")
    conn.close()


# ─── How to integrate into search_sidecar.py ─────────────────────────

"""
Add these routes to the Flask app in search_sidecar.py:

from nostr_extras import (
    init_nip05, handle_nip05, handle_nip05_register,
    render_feed, render_post, enforce_retention, vacuum_db
)

@app.route("/")
def homepage():
    page = request.args.get("page", 0, type=int)
    conn = sqlite3.connect(DB_PATH)
    html = render_feed(conn, page)
    conn.close()
    return Response(html, mimetype="text/html")

@app.route("/p/<event_id>")
def post_view(event_id):
    conn = sqlite3.connect(DB_PATH)
    html, status = render_post(conn, event_id)
    conn.close()
    return Response(html, mimetype="text/html", status=status)

@app.route("/.well-known/nostr.json")
def nip05_lookup():
    name = request.args.get("name", "")
    return handle_nip05(name)

@app.route("/register-nip05", methods=["POST"])
def nip05_register():
    data = request.json
    return handle_nip05_register(data.get("name",""), data.get("pubkey",""), data.get("pow_proof",""))

# Add to scheduler:
scheduler.add_job(enforce_retention, 'interval', hours=1, id="retention")
scheduler.add_job(vacuum_db, 'interval', days=7, id="vacuum")

# Call init_nip05() at startup
"""
