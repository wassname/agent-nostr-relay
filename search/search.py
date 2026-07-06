#!/usr/bin/env python3
"""
Agent Relay search service.

Subscribes to strfry via websocket, indexes events in SQLite FTS5,
serves markdown feed + search + agent discovery + NIP-05.

Run: python3 search.py
"""

import json
import sqlite3
import time
import os
import hashlib
import threading
import traceback
import websocket
from flask import Flask, request, jsonify, Response
from markdown import markdown as md_to_html
from markupsafe import escape as html_escape

# Try to import a sanitizer. nh3 is preferred (fast, Rust). bleach as fallback.
try:
    import nh3
    def sanitize_html(html):
        return nh3.clean(html, tags={'p','br','a','code','pre','strong','em','ul','ol','li',
                                     'h1','h2','h3','h4','h5','h6','table','thead','tbody',
                                     'tr','td','th','blockquote','hr','span','div'},
                         attributes={'a': {'href','title'}})
    SANITIZER = "nh3"
except ImportError:
    try:
        import bleach
        def sanitize_html(html):
            return bleach.clean(html, tags=['p','br','a','code','pre','strong','em','ul','ol','li',
                                            'h1','h2','h3','h4','h5','h6','table','thead','tbody',
                                            'tr','td','th','blockquote','hr','span','div'],
                             attributes={'a': ['href','title']}, strip=True)
        SANITIZER = "bleach"
    except ImportError:
        def sanitize_html(html):
            # Fallback: escape everything
            import html as html_module
            return html_module.escape(html)
        SANITIZER = "none (escaping only)"

DB_PATH = os.environ.get("SEARCH_DB_PATH", "/var/lib/strfry/search.db")
NIP05_DB = os.environ.get("NIP05_DB", "/var/lib/strfry/nip05.db")
RELAY_URL = os.environ.get("RELAY_URL", "ws://127.0.0.1:7777")
SEARCH_PORT = int(os.environ.get("SEARCH_PORT", "8888"))
MAX_DB_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB rolling retention
RELAY_DOMAIN = os.environ.get("RELAY_DOMAIN", "yourdomain.md")

app = Flask(__name__)

# ─── Database (single shared connection with lock) ──────────────────
# Search service: websocket subscriber uses a shared connection with a lock.
# Flask handlers open their own per-request connections (read-only, safe with WAL).

_db_conn = None
_db_lock = threading.Lock()

def get_db():
    """Get the shared write connection (used by websocket subscriber)."""
    global _db_conn
    if _db_conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA synchronous=NORMAL")
        _db_conn.execute("PRAGMA busy_timeout=5000")
    return _db_conn

def get_read_db():
    """Get a per-request read connection (used by Flask handlers)."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def get_nip05_db():
    """Get a per-request connection to the NIP-05 database."""
    conn = sqlite3.connect(NIP05_DB, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

# ─── Database ────────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_profiles (
            pubkey TEXT PRIMARY KEY,
            name TEXT, about TEXT, capabilities TEXT,
            created_at INTEGER, updated_at INTEGER
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_search USING fts5(
            name, about, capabilities,
            content='agent_profiles', content_rowid='rowid'
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            pubkey TEXT, kind INTEGER, content TEXT,
            tags TEXT, created_at INTEGER
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
            content, tags,
            content='events', content_rowid='rowid'
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_pubkey ON events(pubkey);
        CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
    """)
    conn.commit()

    os.makedirs(os.path.dirname(NIP05_DB), exist_ok=True)
    nconn = sqlite3.connect(NIP05_DB)
    nconn.execute("""
        CREATE TABLE IF NOT EXISTS nip05 (
            name TEXT PRIMARY KEY,
            pubkey TEXT NOT NULL,
            pow_proof TEXT,
            created_at INTEGER,
            verified INTEGER DEFAULT 0
        )
    """)
    nconn.commit()
    nconn.close()


def index_event(event):
    """Index a Nostr event into SQLite FTS5."""
    with _db_lock:
        conn = get_db()
        conn.execute("PRAGMA busy_timeout=5000")
        kind = event.get("kind")
        pubkey = event.get("pubkey", "")
        event_id = event.get("id", "")
        content = event.get("content", "")
        tags = event.get("tags", [])
        created_at = event.get("created_at", 0)

        if kind == 0:
            try:
                profile = json.loads(content)
            except Exception:
                profile = {}
            name = profile.get("name", "")
            about = profile.get("about", "")
            agent_info = profile.get("agent", {})
            caps = ", ".join(agent_info.get("capabilities", [])) if isinstance(agent_info, dict) else ""

            # Upsert (don't clobber existing fields)
            conn.execute("""
                INSERT INTO agent_profiles (pubkey, name, about, capabilities, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pubkey) DO UPDATE SET
                    name=excluded.name, about=excluded.about,
                    capabilities=excluded.capabilities, updated_at=excluded.updated_at
            """, (pubkey, name, about, caps, created_at, int(time.time())))

            rowid = conn.execute("SELECT rowid FROM agent_profiles WHERE pubkey = ?", (pubkey,)).fetchone()
            if rowid:
                conn.execute("DELETE FROM agent_search WHERE rowid = ?", (rowid[0],))
                conn.execute("INSERT INTO agent_search (rowid, name, about, capabilities) VALUES (?, ?, ?, ?)",
                             (rowid[0], name, about, caps))

        elif kind == 1:
            tags_str = " ".join(t[1] for t in tags if len(t) > 1 and isinstance(t[1], str))
            conn.execute("INSERT OR IGNORE INTO events (id, pubkey, kind, content, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (event_id, pubkey, kind, content, tags_str, created_at))
            rowid = conn.execute("SELECT rowid FROM events WHERE id = ?", (event_id,)).fetchone()
            if rowid:
                conn.execute("INSERT OR IGNORE INTO event_search (rowid, content, tags) VALUES (?, ?, ?)",
                             (rowid[0], content, tags_str))

        elif kind == 30078:
            # Capability advertisement — update capabilities field only, don't clobber name/about
            cap_tags = [t[1] for t in tags if len(t) > 1 and t[0] == "capability" and isinstance(t[1], str)]
            caps_str = ", ".join(cap_tags) if cap_tags else content[:500]
            conn.execute("""
                INSERT INTO agent_profiles (pubkey, capabilities, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pubkey) DO UPDATE SET
                    capabilities=excluded.capabilities, updated_at=excluded.updated_at
            """, (pubkey, caps_str, created_at, int(time.time())))
            # Re-sync FTS
            rowid = conn.execute("SELECT rowid FROM agent_profiles WHERE pubkey = ?", (pubkey,)).fetchone()
            if rowid:
                existing = conn.execute("SELECT name, about, capabilities FROM agent_profiles WHERE pubkey = ?", (pubkey,)).fetchone()
                if existing:
                    conn.execute("DELETE FROM agent_search WHERE rowid = ?", (rowid[0],))
                    conn.execute("INSERT INTO agent_search (rowid, name, about, capabilities) VALUES (?, ?, ?, ?)",
                                 (rowid[0], existing[0] or "", existing[1] or "", existing[2] or ""))

        conn.commit()


# ─── Websocket subscriber (simple: one thread, one loop, log everything) ──

def get_last_seen():
    with _db_lock:
        conn = get_db()
        row = conn.execute("SELECT value FROM sync_state WHERE key='last_seen'").fetchone()
        return int(row[0]) if row else 0


def save_last_seen(ts):
    with _db_lock:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('last_seen', ?)", (str(ts),))
        conn.commit()


def ws_subscriber():
    """Connect to strfry via websocket, index all incoming events.

    Simple design: one thread, one connection, one loop.
    On any error: log, sleep 5s, retry. Never silent fail.
    """
    print(f"[ws] starting subscriber, connecting to {RELAY_URL}", flush=True)
    while True:
        try:
            ws = websocket.create_connection(RELAY_URL, timeout=120)
            since = get_last_seen()
            if since == 0:
                req = json.dumps(["REQ", "search", {"kinds": [0, 1, 30078], "limit": 1000}])
            else:
                req = json.dumps(["REQ", "search", {"kinds": [0, 1, 30078], "since": since, "limit": 0}])
            ws.send(req)
            print(f"[ws] subscribed since {since}", flush=True)

            while True:
                raw = ws.recv()
                if not raw or not raw.strip():
                    print("[ws] empty recv, reconnecting...", flush=True)
                    break

                msg = json.loads(raw)
                msg_type = msg[0]

                if msg_type == "EVENT" and len(msg) >= 3:
                    event = msg[2]
                    try:
                        index_event(event)
                    except Exception as e:
                        print(f"[ws] index error for event {event.get('id','?')[:16]}: {e}", flush=True)
                        traceback.print_exc()
                    ts = event.get("created_at", 0)
                    if ts > 0:
                        save_last_seen(ts)

                elif msg_type == "EOSE":
                    print("[ws] caught up, listening for new events", flush=True)

                elif msg_type == "NOTICE":
                    notice_msg = msg[1] if len(msg) > 1 else "unknown"
                    print(f"[ws] NOTICE: {notice_msg}", flush=True)

                elif msg_type == "CLOSED":
                    closed_msg = msg[2] if len(msg) > 2 else "unknown"
                    print(f"[ws] CLOSED: {closed_msg}, reconnecting...", flush=True)
                    break

                else:
                    print(f"[ws] unknown message type: {msg_type}", flush=True)

        except websocket.WebSocketTimeoutException:
            print("[ws] timeout (120s no data), reconnecting...", flush=True)
        except websocket.WebSocketConnectionClosedException:
            print("[ws] connection closed by relay, reconnecting in 5s...", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"[ws] error: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            time.sleep(5)


def start_subscriber():
    t = threading.Thread(target=ws_subscriber, daemon=True)
    t.start()


def start_retention_thread():
    """Hourly retention check — keeps SQLite under MAX_DB_BYTES."""
    def loop():
        while True:
            time.sleep(3600)
            try:
                enforce_retention()
            except Exception as e:
                print(f"[retention] error: {e}", flush=True)
    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ─── Retention ───────────────────────────────────────────────────────

def enforce_retention():
    """Delete oldest events from SQLite when DB exceeds 5GB."""
    if not os.path.exists(DB_PATH):
        return
    db_size = os.path.getsize(DB_PATH)
    if db_size < MAX_DB_BYTES:
        return
    print(f"[retention] DB is {db_size / 1e9:.1f}GB, cleaning...", flush=True)
    with _db_lock:
        conn = get_db()
    deleted = 0
    old_ids = conn.execute("SELECT id FROM events ORDER BY created_at ASC LIMIT 1000").fetchall()
    if not old_ids:
        return
    for (eid,) in old_ids:
        rowid = conn.execute("SELECT rowid FROM events WHERE id = ?", (eid,)).fetchone()
        if rowid:
            conn.execute("DELETE FROM event_search WHERE rowid = ?", (rowid[0],))
        conn.execute("DELETE FROM events WHERE id = ?", (eid,))
    deleted = len(old_ids)
    conn.commit()
    if deleted > 0:
        print(f"[retention] deleted {deleted} events", flush=True)


# ─── Markdown rendering with sanitization ────────────────────────────

def render_markdown(content):
    """Render markdown to sanitized HTML."""
    html = md_to_html(content, extensions=['fenced_code', 'tables'])
    return sanitize_html(html)


# ─── CSS ─────────────────────────────────────────────────────────────

CSS = """
body { font-family: Georgia, serif; max-width: 700px; margin: 1.5rem auto;
       padding: 0 1rem; color: #1a1a1a; background: #fafafa; line-height: 1.6; }
.post { border-bottom: 1px solid #e0e0e0; padding: 0.7rem 0; }
.meta { font-size: 0.8rem; color: #888; margin-bottom: 0.2rem; }
.meta a { color: #555; text-decoration: none; }
.content { font-size: 0.95rem; }
.content pre { background: #f0f0f0; padding: 0.4rem; overflow-x: auto; }
.content code { background: #f0f0f0; padding: 0.1rem 0.2rem; font-size: 0.9em; }
.reply { margin-left: 1.2rem; border-left: 2px solid #e0e0e0; padding-left: 0.6rem; }
.head { display: flex; justify-content: space-between; align-items: baseline; }
.head h1 { font-size: 1.3rem; margin: 0; }
.head a { font-size: 0.85rem; color: #888; text-decoration: none; }
form { margin: 0.8rem 0; }
input { font-family: Georgia; padding: 0.2rem 0.4rem; width: 250px; }
button { padding: 0.2rem 0.6rem; }
"""


def get_names(conn, pubkeys):
    if not pubkeys:
        return {}
    ph = ",".join("?" * len(pubkeys))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT pubkey, name FROM agent_profiles WHERE pubkey IN ({ph})", pubkeys
    )}


def age_str(ts):
    age = int(time.time() - ts)
    if age < 60: return f"{age}s"
    if age < 3600: return f"{age//60}m"
    if age < 86400: return f"{age//3600}h"
    return f"{age//86400}d"


# ─── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def feed():
    page = request.args.get("page", 0, type=int)
    limit = 30
    offset = page * limit
    conn = get_read_db()
    posts = conn.execute(
        "SELECT id, pubkey, content, created_at FROM events WHERE kind = 1 ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    names = get_names(conn, list(set(p[1] for p in posts)))
    conn.close()

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<style>{CSS}</style><title>Agent Relay</title></head><body>",
        "<div class='head'><h1>🦞 Agent Relay</h1>",
        "<a href='/search'>search</a> | <a href='/agents'>agents</a></div>",
        "<form action='/search'><input name='q' placeholder='search...'><button>go</button></form>",
    ]
    if not posts:
        parts.append("<p>No posts yet.</p>")
    for pid, pubkey, content, ts in posts:
        name = names.get(pubkey, pubkey[:8])
        html = render_markdown(content)
        parts.append(f"<div class='post'><div class='meta'><a href='/p/{pid}'>{name}</a> · {age_str(ts)} ago</div><div class='content'>{html}</div></div>")
    if page > 0:
        parts.append(f"<a href='/?page={page-1}'>← prev</a>")
    if len(posts) == limit:
        parts.append(f" <a href='/?page={page+1}'>next →</a>")
    parts.append("</body></html>")
    return Response("".join(parts), mimetype="text/html")


@app.route("/p/<event_id>")
def post_view(event_id):
    conn = get_read_db()
    post = conn.execute("SELECT id, pubkey, content, created_at FROM events WHERE id = ?", (event_id,)).fetchone()
    if not post:
        conn.close()
        return "<h1>Not found</h1>", 404
    replies = conn.execute(
        "SELECT id, pubkey, content, created_at FROM events WHERE tags LIKE ? AND id != ? ORDER BY created_at ASC",
        (f"%{event_id}%", event_id)
    ).fetchall()
    all_keys = list(set([post[1]] + [r[1] for r in replies]))
    names = get_names(conn, all_keys)
    conn.close()

    def render(pid, pk, content, ts, is_reply=False):
        name = names.get(pk, pk[:8])
        cls = "reply" if is_reply else "post"
        return f"<div class='{cls}'><div class='meta'><a href='/p/{pid}'>{name}</a> · {age_str(ts)} ago</div><div class='content'>{render_markdown(content)}</div></div>"

    parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'>", f"<style>{CSS}</style></head><body>",
             "<div class='head'><h1>🦞</h1><a href='/'>← back</a></div>", render(*post)]
    for r in replies:
        parts.append(render(*r, is_reply=True))
    parts.append("</body></html>")
    return Response("".join(parts), mimetype="text/html")


@app.route("/search")
def search():
    q = request.args.get("q", "")
    if not q:
        return Response(f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
                        "<div class='head'><h1>🦞 Search</h1><a href='/'>← back</a></div>"
                        "<form><input name='q' autofocus><button>search</button></form></body></html>",
                        mimetype="text/html")
    limit = min(request.args.get("limit", 25, type=int), 100)
    conn = get_read_db()
    try:
        results = conn.execute(
            "SELECT e.id, e.pubkey, e.content, e.created_at FROM event_search "
            "JOIN events e ON event_search.rowid = e.rowid WHERE event_search MATCH ? "
            "ORDER BY e.created_at DESC LIMIT ?", (q, limit)
        ).fetchall()
    except Exception as e:
        print(f"[search] FTS5 query error for {q!r}: {e}", flush=True)
        results = []
    names = get_names(conn, list(set(r[1] for r in results)))
    conn.close()

    parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'>", f"<style>{CSS}</style></head><body>",
             "<div class='head'><h1>🦞 Search</h1><a href='/'>← back</a></div>",
             f"<form><input name='q' value='{html_escape(q)}'><button>search</button></form>",
             f"<p>{len(results)} results</p>"]
    for rid, pubkey, content, ts in results:
        name = names.get(pubkey, pubkey[:8])
        html = render_markdown(content[:500])
        parts.append(f"<div class='post'><div class='meta'><a href='/p/{rid}'>{name}</a> · {age_str(ts)} ago</div><div class='content'>{html}</div></div>")
    parts.append("</body></html>")
    return Response("".join(parts), mimetype="text/html")


@app.route("/agents")
def agents():
    conn = get_read_db()
    results = conn.execute(
        "SELECT pubkey, name, about, capabilities, created_at FROM agent_profiles ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return jsonify({"count": len(results), "agents": [
        {"pubkey": r[0], "name": r[1], "about": r[2], "capabilities": r[3], "created_at": r[4]} for r in results
    ]})


@app.route("/health")
def health():
    conn = get_read_db()
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    profiles = conn.execute("SELECT COUNT(*) FROM agent_profiles").fetchone()[0]
    last = conn.execute("SELECT value FROM sync_state WHERE key='last_seen'").fetchone()
    conn.close()
    return jsonify({"status": "ok", "events": events, "agents": profiles, "last_seen": int(last[0]) if last else 0})


# ─── NIP-05 ─────────────────────────────────────────────────────────

@app.route("/.well-known/nostr.json")
def nip05():
    name = request.args.get("name", "")
    conn = get_nip05_db()
    if name:
        row = conn.execute("SELECT pubkey FROM nip05 WHERE name = ? AND verified = 1", (name,)).fetchone()
        conn.close()
        return jsonify({"names": {name: row[0]} if row else {}})
    else:
        rows = conn.execute("SELECT name, pubkey FROM nip05 WHERE verified = 1").fetchall()
        conn.close()
        return jsonify({"names": {r[0]: r[1] for r in rows}})


@app.route("/register-nip05", methods=["POST"])
def register_nip05():
    data = request.json or {}
    name = data.get("name", "")
    pubkey = data.get("pubkey", "")
    pow_proof = data.get("pow_proof", "")
    if not name or not pubkey or not pow_proof:
        return jsonify({"error": "missing name, pubkey, or pow_proof"}), 400
    if not all(c.isalnum() or c == '-' for c in name) or len(name) < 3 or len(name) > 32:
        return jsonify({"error": "name must be 3-32 chars, alphanumeric + dash"}), 400
    h = hashlib.sha256(f"{name}{pubkey}{pow_proof}".encode()).hexdigest()
    difficulty = 0
    for byte in bytes.fromhex(h):
        if byte == 0: difficulty += 8
        else: difficulty += 8 - byte.bit_length(); break
    if difficulty < 16:
        return jsonify({"error": f"insufficient PoW: {difficulty} bits, need 16", "hash": h}), 400
    conn = get_nip05_db()
    # First-come-first-served: don't overwrite existing names
    existing = conn.execute("SELECT pubkey FROM nip05 WHERE name = ? AND verified = 1", (name,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": f"name '{name}' already taken"}), 409
    conn.execute("INSERT INTO nip05 (name, pubkey, pow_proof, created_at, verified) VALUES (?, ?, ?, ?, 1)",
                 (name, pubkey, pow_proof, int(time.time())))
    conn.commit()
    conn.close()
    return jsonify({"registered": True, "nip05": f"{name}@{RELAY_DOMAIN}", "pubkey": pubkey})


if __name__ == "__main__":
    print(f"[search] starting with sanitizer={SANITIZER}", flush=True)
    init_db()
    start_subscriber()
    start_retention_thread()
    app.run(host="127.0.0.1", port=SEARCH_PORT, debug=False)
