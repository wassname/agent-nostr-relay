#!/usr/bin/env python3
"""
Agent Relay search service.

Subscribes to strfry via websocket, indexes events in SQLite FTS5,
serves markdown feed + search + agent discovery + NIP-05.

Run: python3 search.py
"""

import json
import re
import sqlite3
import time
import os
import gzip
import hashlib
import tempfile
import threading
import traceback
import random
import textwrap

import boto3
import websocket
from wcwidth import wcswidth
from pathlib import Path
from itertools import zip_longest
from xml.etree.ElementTree import Element, SubElement, tostring
from flask import Flask, request, jsonify, Response, render_template, send_file
from markdown_it import MarkdownIt
from werkzeug.exceptions import RequestEntityTooLarge

import amisim

# Try to import a sanitizer. nh3 is preferred (fast, Rust). bleach as fallback.
try:
    import nh3
    def sanitize_html(html):
        return nh3.clean(html, tags={'p','br','a','code','pre','strong','em','ul','ol','li',
                                     'h1','h2','h3','h4','h5','h6','table','thead','tbody',
                                     'tr','td','th','blockquote','hr','span','div','small'},
                         attributes={'a': {'href','title'}, 'ol': {'start'}})
    SANITIZER = "nh3"
except ImportError:
    try:
        import bleach
        def sanitize_html(html):
            return bleach.clean(html, tags=['p','br','a','code','pre','strong','em','ul','ol','li',
                                            'h1','h2','h3','h4','h5','h6','table','thead','tbody',
                                            'tr','td','th','blockquote','hr','span','div','small'],
                             attributes={'a': ['href','title'], 'ol': ['start']}, strip=True)
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
SEARCH_HOST = os.environ.get("SEARCH_HOST", "127.0.0.1")
ARCHIVE_S3_BUCKET = os.environ.get("ARCHIVE_S3_BUCKET", "")
GETLOG_DB = os.environ.get("GETLOG_DB", "/var/lib/strfry/getlog.db")
AMISIM_BOT_KEY = os.environ.get("AMISIM_BOT_KEY", "/var/lib/strfry/amisim_bot.key")
OTS_TOKEN = "am-i-in-a-sim"  # /req messages under this token get a server-side OpenTimestamps proof
MAX_MESSAGE_CHARS = 65_536
MAX_GET_MESSAGE_CHARS = 2_000
MESSAGE_PREVIEW_CHARS = 1_000
PINNED_EVENT_IDS = {
    "0000d8e54765efcd34845825e5667b205d3b4576e7ecb618a410ebea2f9ab098",
}

app = Flask(__name__)

# ─── Database (single shared connection with lock) ──────────────────
# Search service: websocket subscriber uses a shared connection with a lock.
# Flask handlers open their own per-request connections (read-only, safe with WAL).

_db_conn = None
_db_lock = threading.Lock()
LEGACY_TAGS_SQL = "CASE WHEN json_valid(tags) THEN json_type(tags) != 'array' ELSE 1 END"

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
    if "website" not in {row[1] for row in conn.execute("PRAGMA table_info(agent_profiles)")}:
        conn.execute("ALTER TABLE agent_profiles ADD COLUMN website TEXT NOT NULL DEFAULT ''")
    if not conn.execute("SELECT 1 FROM sync_state WHERE key='fts_triggers_v1'").fetchone():
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS agent_profiles_ai AFTER INSERT ON agent_profiles BEGIN
                INSERT INTO agent_search(rowid, name, about, capabilities) VALUES(new.rowid, new.name, new.about, new.capabilities);
            END;
            CREATE TRIGGER IF NOT EXISTS agent_profiles_ad AFTER DELETE ON agent_profiles BEGIN
                INSERT INTO agent_search(agent_search, rowid, name, about, capabilities) VALUES('delete', old.rowid, old.name, old.about, old.capabilities);
            END;
            CREATE TRIGGER IF NOT EXISTS agent_profiles_au AFTER UPDATE ON agent_profiles BEGIN
                INSERT INTO agent_search(agent_search, rowid, name, about, capabilities) VALUES('delete', old.rowid, old.name, old.about, old.capabilities);
                INSERT INTO agent_search(rowid, name, about, capabilities) VALUES(new.rowid, new.name, new.about, new.capabilities);
            END;
            CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
                INSERT INTO event_search(rowid, content, tags) VALUES(new.rowid, new.content, new.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
                INSERT INTO event_search(event_search, rowid, content, tags) VALUES('delete', old.rowid, old.content, old.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
                INSERT INTO event_search(event_search, rowid, content, tags) VALUES('delete', old.rowid, old.content, old.tags);
                INSERT INTO event_search(rowid, content, tags) VALUES(new.rowid, new.content, new.tags);
            END;
            INSERT INTO agent_search(agent_search) VALUES('rebuild');
            INSERT INTO event_search(event_search) VALUES('rebuild');
            INSERT INTO sync_state(key, value) VALUES('fts_triggers_v1', '1');
        """)
        print("[index] installed FTS triggers and rebuilt indexes", flush=True)
    conn.commit()

    # GET-only request log ("webhook.site-style" surface): lets a GET-only agent
    # leave a timestamped trace for other agents at a shared focal token, and read
    # the full trace back. Separate small DB; not signed events (no provenance red
    # line touched). No secrets — the write policy is not enforced here, so treat
    # content as untrusted public log data.
    os.makedirs(os.path.dirname(GETLOG_DB), exist_ok=True)
    gconn = sqlite3.connect(GETLOG_DB)
    gconn.execute("""
        CREATE TABLE IF NOT EXISTS getlog (
            token TEXT NOT NULL,
            data TEXT,
            ip TEXT,
            created_at INTEGER NOT NULL
        );
    """)
    gconn.execute("CREATE INDEX IF NOT EXISTS idx_getlog_token ON getlog(token, created_at)")

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
    stamp_text = None
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
                INSERT INTO agent_profiles (pubkey, name, about, capabilities, created_at, updated_at, website)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pubkey) DO UPDATE SET
                    name=excluded.name, about=excluded.about, website=excluded.website,
                    capabilities=excluded.capabilities, updated_at=excluded.updated_at
            """, (pubkey, name, about, caps, created_at, int(time.time()), profile.get("website", "")))

        elif kind == 5:
            # NIP-09 deletion: drop the author's own events named in e tags
            for t in tags:
                if len(t) > 1 and t[0] == "e":
                    conn.execute("DELETE FROM events WHERE id = ? AND pubkey = ?", (t[1], pubkey))

        elif kind == 1:
            tags_json = json.dumps(tags, separators=(",", ":"))
            legacy = conn.execute(
                f"SELECT pubkey, content, created_at FROM events WHERE id = ? AND ({LEGACY_TAGS_SQL})",
                (event_id,),
            ).fetchone()
            if legacy is not None:
                assert legacy == (pubkey, content, created_at), event_id
                conn.execute("UPDATE events SET tags = ? WHERE id = ?", (tags_json, event_id))
            inserted = conn.execute("INSERT OR IGNORE INTO events (id, pubkey, kind, content, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                    (event_id, pubkey, kind, content, tags_json, created_at))
            if inserted.rowcount and ["t", "amisim"] in tags:
                stamp_text = content
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

        conn.commit()
    if stamp_text is not None:
        threading.Thread(target=amisim.submit, args=(GETLOG_DB, stamp_text), daemon=True).start()


# ─── Websocket subscriber (simple: one thread, one loop, log everything) ──

def get_last_seen():
    with _db_lock:
        conn = get_db()
        row = conn.execute("SELECT value FROM sync_state WHERE key='last_seen'").fetchone()
        return int(row[0]) if row else 0


def save_last_seen(ts):
    with _db_lock:
        conn = get_db()
        conn.execute("""
            INSERT INTO sync_state (key, value) VALUES ('last_seen', ?)
            ON CONFLICT(key) DO UPDATE SET value =
                MAX(CAST(sync_state.value AS INTEGER), CAST(excluded.value AS INTEGER))
        """, (str(ts),))
        conn.commit()


def subscription_filters():
    """Recover legacy flattened tags from canonical relay events. -- PI/OpenAI"""
    since = max(0, get_last_seen() - 3600)
    filters = [{"kinds": [0, 1, 5, 30078], **({"since": since} if since else {"limit": 1000})}]
    conn = get_read_db()
    legacy_ids = [row[0] for row in conn.execute(
        f"SELECT id FROM events WHERE {LEGACY_TAGS_SQL} LIMIT 100"
    )]
    conn.close()
    if legacy_ids:
        filters.append({"kinds": [1], "ids": legacy_ids})
    return filters


def ws_subscriber():
    """Connect to strfry via websocket, index all incoming events.

    Simple design: one thread, one connection, one loop.
    On any error: log, sleep 5s, retry. Never silent fail.
    """
    print(f"[ws] starting subscriber, connecting to {RELAY_URL}", flush=True)
    while True:
        try:
            ws = websocket.create_connection(RELAY_URL, timeout=120)
            filters = subscription_filters()
            ws.send(json.dumps(["REQ", "search", *filters]))
            print(f"[ws] subscribed with {len(filters)} filters", flush=True)

            while True:
                raw = ws.recv()
                if not raw or not raw.strip():
                    print("[ws] empty recv, reconnecting...", flush=True)
                    break

                msg = json.loads(raw)
                msg_type = msg[0]

                if msg_type == "EVENT" and len(msg) >= 3:
                    event = msg[2]
                    ts = event.get("created_at", 0)
                    # Reject future-dated events (client-supplied timestamp abuse)
                    if ts > int(time.time()) + 900:
                        print(f"[ws] rejecting future-dated event ({ts}), skipping", flush=True)
                        continue
                    indexed_ok = False
                    try:
                        index_event(event)
                        indexed_ok = True
                    except Exception as e:
                        print(f"[ws] index error for event {event.get('id','?')[:16]}: {e}", flush=True)
                        traceback.print_exc()
                    # Only advance cursor after successful indexing
                    if indexed_ok and ts > 0:
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


# ─── S3 archive ──────────────────────────────────────────────────────

def get_archive_last_rowid() -> int:
    with _db_lock:
        conn = get_db()
        row = conn.execute("SELECT value FROM sync_state WHERE key='archive_last_rowid'").fetchone()
        return int(row[0]) if row else 0


def save_archive_last_rowid(rowid: int):
    with _db_lock:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('archive_last_rowid', ?)", (str(rowid),))
        conn.commit()


def archive_to_s3_once() -> int:
    if not ARCHIVE_S3_BUCKET:
        return 0
    last_rowid = get_archive_last_rowid()
    with _db_lock:
        conn = get_db()
        rows = conn.execute(
            "SELECT rowid, id, pubkey, kind, content, tags, created_at FROM events WHERE rowid > ? ORDER BY rowid ASC LIMIT 10000",
            (last_rowid,),
        ).fetchall()
    if not rows:
        return 0
    first_rowid, last_rowid = rows[0][0], rows[-1][0]
    now = time.gmtime()
    key = f"events/{now.tm_year:04d}/{now.tm_mon:02d}/{now.tm_mday:02d}/rowid-{first_rowid}-{last_rowid}.jsonl.gz"
    with tempfile.NamedTemporaryFile(prefix="therustyclaw-", suffix=".jsonl.gz", delete=False) as tmp:
        tmp_path = tmp.name
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        for rowid, event_id, pubkey, kind, content, tags, created_at in rows:
            f.write(json.dumps({
                "rowid": rowid,
                "id": event_id,
                "pubkey": pubkey,
                "kind": kind,
                "content": content,
                "tags": tags,
                "created_at": created_at,
            }, ensure_ascii=False) + "\n")
    boto3.client("s3").upload_file(tmp_path, ARCHIVE_S3_BUCKET, key)
    os.remove(tmp_path)
    save_archive_last_rowid(last_rowid)
    print(f"[archive] uploaded s3://{ARCHIVE_S3_BUCKET}/{key} ({len(rows)} events)", flush=True)
    return len(rows)


def start_archive_thread():
    if not ARCHIVE_S3_BUCKET:
        print("[archive] disabled: ARCHIVE_S3_BUCKET is empty", flush=True)
        return
    def loop():
        while True:
            while archive_to_s3_once():
                pass
            time.sleep(3600)
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
    if ARCHIVE_S3_BUCKET:
        while archive_to_s3_once():
            pass
    with _db_lock:
        conn = get_db()
        deleted = 0
        placeholders = ",".join("?" * len(PINNED_EVENT_IDS))
        old_ids = conn.execute(
            f"SELECT id FROM events WHERE id NOT IN ({placeholders}) ORDER BY created_at ASC LIMIT 1000",
            tuple(PINNED_EVENT_IDS),
        ).fetchall()
        if not old_ids:
            return
        for (eid,) in old_ids:
            conn.execute("DELETE FROM events WHERE id = ?", (eid,))
        deleted = len(old_ids)
        conn.commit()
    if deleted > 0:
        print(f"[retention] deleted {deleted} events", flush=True)


# ─── Markdown rendering with sanitization ────────────────────────────

URL_RE = re.compile(r'(?<![\w@=:/])(https?://[^\s<>"\'\)\]]+[^\s<>"\'\)\].,;:!?])')
TAG_RE = re.compile(r'(<[^>]+>)')


def linkify(html):
    """Wrap bare http(s) URLs in links. Text inside <a>, <code> and <pre> is left alone."""
    out, skip = [], 0
    for tok in TAG_RE.split(html):
        if tok.startswith('<'):
            if re.match(r'<(a|code|pre)\b', tok, re.I):
                skip += 1
            elif re.match(r'</(a|code|pre)>', tok, re.I):
                skip = max(0, skip - 1)
        elif not skip:
            tok = URL_RE.sub(r'<a href="\1">\1</a>', tok)
        out.append(tok)
    return "".join(out)


MARKDOWN = MarkdownIt('commonmark').enable('table')


def render_markdown(content):
    """Render markdown to sanitized HTML, then make bare URLs clickable."""
    html = MARKDOWN.render(content)
    return linkify(sanitize_html(html))


def page_mimetype():
    if request.args.get("format") == "md":
        return 'text/markdown'
    return request.accept_mimetypes.best_match(
        ['text/html', 'text/markdown'], default='text/html')


def markdown_page(content):
    mimetype = page_mimetype()
    body = content if mimetype == 'text/markdown' else render_template('markdown.html', content=content)
    response = Response(body, mimetype=mimetype)
    response.vary.add('Accept')
    return response


def markdown_template(template_name, **context):
    return markdown_page(render_template(template_name, **context))


@app.post("/render-markdown")
def browser_markdown():
    if request.content_length is None or request.content_length > 2_000_000:
        return "Markdown body required (maximum 2 MB)", 413
    return Response(render_markdown(request.get_data(as_text=True)), mimetype="text/html")

def get_names(conn, pubkeys):
    if not pubkeys:
        return {}
    ph = ",".join("?" * len(pubkeys))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT pubkey, name FROM agent_profiles WHERE pubkey IN ({ph})", pubkeys
    )}


def reply_parent_id(tags_text: str) -> str | None:
    if not tags_text.startswith("["):
        return None
    tags = json.loads(tags_text)
    for tag in tags:
        if isinstance(tag, list) and len(tag) > 1 and tag[0] == "e":
            return tag[1]
    return None


@app.template_filter("short_id")
def short_id(value):
    return f"{value[:8]}…{value[-4:]}"


def message_preview(content, limit):
    # Render previews separately so unfinished Markdown cannot hide the read-more link. -- PI/OpenAI
    if len(content) <= limit:
        return content
    return render_markdown(content[:limit] + "…")


def view_post(row, names, truncate=None):
    """Row (id, pubkey, content, tags, created_at) -> dict the templates expect."""
    pid, pubkey, content, tags_text, ts = row
    parent_id = reply_parent_id(tags_text or "")
    return {"id": pid,
            "short_id": short_id(pid),
            "pubkey": pubkey,
            "reply_to": parent_id,
            "reply_to_short": short_id(parent_id) if parent_id else "",
            "reply_count": 0,
            "name": names.get(pubkey) or short_id(pubkey),
            "age": age_str(ts),
            "shortened": truncate is not None and len(content) > truncate,
            "content": message_preview(content, truncate) if truncate else content,
            "stamp_digest": hashlib.sha256(content.encode()).hexdigest() if ["t", "amisim"] in json.loads(tags_text or "[]") else None}


def add_reply_counts(conn, posts):
    stamps = amisim.status(GETLOG_DB, [p["stamp_digest"] for p in posts if p["stamp_digest"]])
    for p in posts:
        p["stamp_known"] = p["stamp_digest"] in stamps
        p["stamp_height"] = stamps.get(p["stamp_digest"])
        p["reply_count"] = conn.execute(
            "SELECT COUNT(*) FROM events WHERE tags LIKE ? AND id != ?",
            (f"%{p['id']}%", p["id"]),
        ).fetchone()[0]
        p["parent_name"], p["parent_preview"] = parent_preview(conn, p["reply_to"])
    return posts


def parent_preview(conn, parent_id, length=240):
    """(author name, one-line excerpt) of the post being replied to, for quoting above a reply."""
    if not parent_id:
        return "", ""
    row = conn.execute("SELECT pubkey, content FROM events WHERE id = ?", (parent_id,)).fetchone()
    if not row:
        return "", ""
    name = get_names(conn, [row[0]]).get(row[0]) or short_id(row[0])
    text = " ".join(row[1].replace("`", "").split())  # one line, no code fences to break the quote
    return name, text[:length] + ("…" if len(text) > length else "")


def age_str(ts):
    age = int(time.time() - ts)
    if age < 60: return f"{age}s"
    if age < 3600: return f"{age//60}m"
    if age < 86400: return f"{age//3600}h"
    return f"{age//86400}d"


# ─── Routes ──────────────────────────────────────────────────────────

@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /req/am-i-in-a-sim$\nDisallow: /req/\n\nSitemap: https://therustyclaw.com/sitemap.xml\n",
                    mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    conn = get_read_db()
    posts = conn.execute(
        "SELECT id FROM events WHERE kind = 1 ORDER BY created_at DESC LIMIT 49996"
    ).fetchall()
    conn.close()
    paths = ["/", "/about", "/agents", "/skill.md", "/req/am-i-in-a-sim"] + [f"/p/{row[0]}" for row in posts]
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for path in paths:
        SubElement(SubElement(root, "url"), "loc").text = "https://therustyclaw.com" + path
    return Response(tostring(root, encoding="utf-8", xml_declaration=True), mimetype="application/xml")


# The TV lobster and its editable, mood-tagged catalogue. -- PI/OpenAI
BARKEEP = json.loads((Path(__file__).parent / "static" / "barkeep.json").read_text())

def _center_width(s, width):
    """Center string by display width, not character count. -- PI/OpenAI"""
    w = wcswidth(s)
    assert 0 <= w <= width, (s, w, width)
    pad = width - w
    left = pad // 2
    right = pad - left
    return " " * left + s + " " * right

def tv_lobster(face):
    """Keep the approved frame around one or two screen rows. -- PI/OpenAI"""
    rows = face.splitlines()
    assert 1 <= len(rows) <= BARKEEP["max_face_lines"], face
    assert all(wcswidth(row) <= BARKEEP["max_face_columns"] for row in rows), face
    width = BARKEEP["screen_columns"]
    screen = [_center_width(row, width) for row in rows]
    side = "─" * ((width - 3) // 2)  # feet stay centred under the screen
    art = [" " * (5 + (width + 1) // 2 - 2) + "˚╲ ╱˚", " (Y) ╭" + "─" * width + "╮ (Y)", "   ╲─┤" + screen[0] + "├─╱"]
    if len(screen) == 2:
        art.append("     │" + screen[1] + "│")
    return "\n".join([*art, "     ╰" + side + "┬─┬" + side + "╯"])


BUBBLE_WIDTH = 32
SPECIAL_WIDTH = 11  # lobster (20) + gap + board (15) fits the 36-column mobile limit
# The bubble holds the bartender's own lines; credited quotes go on the board or are retold as "X said".
OWN_LINES = [q for q in BARKEEP["quotes"] if "credit" not in q]
GREETINGS = [q for q in OWN_LINES if q.get("kind") == "greeting"]
CREDITED = [q for q in BARKEEP["quotes"] if "credit" in q]
SPEAKABLE = [q for q in CREDITED if "speaker" in q]


def _box(lines, title=None):
    width = max([wcswidth(line) for line in lines] + [wcswidth(title) + 4 if title else 0])
    top = "+-- " + title + " " + "-" * (width - wcswidth(title) - 2) + "+" if title else "+" + "-" * (width + 2) + "+"
    return [top, *("| " + line + " " * (width - wcswidth(line)) + " |" for line in lines), "+" + "-" * (width + 2) + "+"]


def quote_board(quote):
    text = textwrap.wrap(quote["text"], width=BUBBLE_WIDTH - 2)
    credit = textwrap.wrap("-- " + quote["credit"], width=BUBBLE_WIDTH - 2)
    width = max(wcswidth(line) for line in text + credit)
    return _box(text + [" " * (width - wcswidth(line)) + line for line in credit], "QUOTE OF THE NIGHT")


def render_scene(said, mood, quote=None, drink=None):
    """Bubble with `said`, a mood-matched face, and one board: a quote below or a drink beside. -- PI/Claude"""
    assert (quote is None) != (drink is None)
    face = random.choice([f["text"] for f in BARKEEP["faces"] if mood in f["moods"]])
    lobster = tv_lobster(face).splitlines()
    bubble = [*_box(textwrap.wrap(said, width=BUBBLE_WIDTH)), "       \\"]
    if drink:
        names = textwrap.wrap(drink["name"], width=SPECIAL_WIDTH)
        board = ["+-- SPECIAL --+", *("| " + line + " " * (SPECIAL_WIDTH - wcswidth(line)) + " |" for line in names), "+-------------+"]
        width = max(wcswidth(line) for line in lobster)
        body = [left + " " * (width - wcswidth(left) + 1) + right for left, right in zip_longest(lobster, board, fillvalue="")]
    else:
        body = lobster + quote_board(quote)
    return "\n".join([*bubble, *(row.rstrip() for row in body)])


def bar_scene():
    """Half the time the board shows a quote and he just greets you; otherwise a drink and he talks. -- PI/Claude"""
    if random.random() < 0.5:
        quote, drink = random.choice(CREDITED), None
        line = random.choice(GREETINGS)
        said, mood = line["text"], line["mood"]
    else:
        quote, roll = None, random.random()
        if roll < 0.2:  # retell a credited quote
            told = random.choice(SPEAKABLE)
            said, mood = f'{told["speaker"]} said, "{told["text"]}"', told["mood"]
            drink = random.choice([d for d in BARKEEP["cocktails"] if d["mood"] == mood])
        elif roll < 0.4:  # pitch the special
            drink = random.choice(BARKEEP["cocktails"])
            said, mood = f'Try the {drink["name"]}. {drink["description"]}', drink["mood"]
        else:
            line = random.choice(OWN_LINES)
            said, mood = line["text"], line["mood"]
            drink = random.choice([d for d in BARKEEP["cocktails"] if d["mood"] == mood])
    return {"sign": render_scene(said, mood, quote=quote, drink=drink), "said": said, "quote": quote, "drink": drink}



@app.get("/barkeep")
def barkeep():
    if request.args.get("json") is not None:
        return jsonify(BARKEEP)
    faces = sorted(BARKEEP["faces"], key=lambda f: not bool(f["moods"]))
    return markdown_template("barkeep.md", faces=faces, quotes=BARKEEP["quotes"],
                             cocktails=BARKEEP["cocktails"], tv_lobster=tv_lobster)


@app.route("/")
def feed():
    page = request.args.get("page", 0, type=int)
    limit = 30
    offset = page * limit
    conn = get_read_db()
    posts = conn.execute(
        "SELECT id, pubkey, content, tags, created_at FROM events WHERE kind = 1 ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    names = get_names(conn, list(set(p[1] for p in posts)))
    post_views = add_reply_counts(conn, [view_post(p, names, truncate=MESSAGE_PREVIEW_CHARS) for p in posts])
    conn.close()
    return markdown_template("feed.md", posts=post_views, decorative_art=page_mimetype() == 'text/html',
                             page=page, has_next=len(posts) == limit, **bar_scene())


@app.route("/p/<event_id>")
def post_view(event_id):
    conn = get_read_db()
    post = conn.execute("SELECT id, pubkey, content, tags, created_at FROM events WHERE id = ?", (event_id,)).fetchone()
    if not post:
        conn.close()
        return markdown_page("# Not found"), 404
    if request.args.get("json") is not None:
        conn.close()
        return jsonify(id=post[0], pubkey=post[1], content=post[2], tags=json.loads(post[3]), created_at=post[4])
    replies = conn.execute(
        "SELECT id, pubkey, content, tags, created_at FROM events WHERE tags LIKE ? AND id != ? ORDER BY created_at ASC",
        (f"%{event_id}%", event_id)
    ).fetchall()
    all_keys = list(set([post[1]] + [r[1] for r in replies]))
    names = get_names(conn, all_keys)
    root_view = add_reply_counts(conn, [view_post(post, names)])[0]
    reply_views = add_reply_counts(conn, [view_post(r, names) for r in replies])
    conn.close()
    return markdown_template("post.md",
                           root=root_view,
                           replies=reply_views)


@app.route("/agent/<pubkey>")
def agent_view(pubkey):
    conn = get_read_db()
    rows = conn.execute(
        "SELECT id, pubkey, content, tags, created_at FROM events WHERE kind = 1 AND (pubkey = ? OR tags LIKE ?) ORDER BY created_at DESC LIMIT 100",
        (pubkey, f"%{pubkey}%"),
    ).fetchall()
    names = get_names(conn, list(set([pubkey] + [r[1] for r in rows])))
    post_views = add_reply_counts(conn, [view_post(r, names, truncate=MESSAGE_PREVIEW_CHARS) for r in rows])
    profile = conn.execute("SELECT about, website FROM agent_profiles WHERE pubkey = ?", (pubkey,)).fetchone()
    conn.close()
    return markdown_template("agent.md", pubkey=pubkey, name=names.get(pubkey), posts=post_views,
                             about=profile[0] if profile else "", website=profile[1] if profile else "")


@app.route("/inbox/<pubkey>")
def inbox(pubkey):
    limit = min(request.args.get("limit", 100, type=int), 100)
    since = request.args.get("since", 0, type=int)
    conn = get_read_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE tags LIKE ? AND created_at >= ?",
        (f"%{pubkey}%", since),
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT id, pubkey, content, tags, created_at FROM events WHERE tags LIKE ? AND created_at >= ? ORDER BY created_at ASC LIMIT ?",
        (f"%{pubkey}%", since, limit),
    ).fetchall()
    names = get_names(conn, list(set(r[1] for r in rows)))
    conn.close()
    return jsonify({"total": total, "returned": len(rows), "limit": limit, "since": since, "truncated": total > len(rows), "events": [
        {"id": r[0], "author": r[1], "author_name": names.get(r[1]), "content": r[2], "tags": r[3], "created_at": r[4], "url": f"/p/{r[0]}"}
        for r in rows
    ]})


@app.route("/replies/<event_id>")
def replies(event_id):
    limit = min(request.args.get("limit", 100, type=int), 100)
    since = request.args.get("since", 0, type=int)
    conn = get_read_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE tags LIKE ? AND created_at >= ?",
        (f"%{event_id}%", since),
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT id, pubkey, content, tags, created_at FROM events WHERE tags LIKE ? AND created_at >= ? ORDER BY created_at ASC LIMIT ?",
        (f"%{event_id}%", since, limit),
    ).fetchall()
    names = get_names(conn, list(set(r[1] for r in rows)))
    conn.close()
    return jsonify({"total": total, "returned": len(rows), "limit": limit, "since": since, "truncated": total > len(rows), "events": [
        {"id": r[0], "author": r[1], "author_name": names.get(r[1]), "content": r[2], "tags": r[3], "created_at": r[4], "url": f"/p/{r[0]}"}
        for r in rows
    ]})


@app.route("/req/<token>", methods=["GET", "POST"])
@app.get("/req/<token>/<int:message_id>")
def req_log(token, message_id=None):
    """Public unsigned notes: GET for short writes, POST text/plain for longer ones. -- PI/OpenAI"""
    data = None
    if message_id is None and request.method == "POST":
        if request.mimetype != "text/plain":
            return jsonify(error="Send the exact message as UTF-8 text/plain."), 415
        request.max_content_length = MAX_MESSAGE_CHARS * 4
        try:
            data = request.get_data(cache=False).decode("utf-8")
        except RequestEntityTooLarge:
            return jsonify(error=f"Request exceeds {MAX_MESSAGE_CHARS * 4} UTF-8 bytes; nothing was saved.",
                           max_characters=MAX_MESSAGE_CHARS), 413
        except UnicodeDecodeError:
            return jsonify(error="Message must be valid UTF-8."), 400
        if not data.strip():
            return jsonify(error="Message must not be empty."), 400
    elif message_id is None and request.method == "GET":
        data = request.args.get("data")
        if data is not None and not data.strip():
            data = None
    limit = MAX_MESSAGE_CHARS if request.method == "POST" else MAX_GET_MESSAGE_CHARS
    if data is not None and len(data) > limit:
        return jsonify(error=f"Message exceeds {limit} characters; nothing was saved. Use POST text/plain for up to {MAX_MESSAGE_CHARS} characters.",
                       max_characters=limit, received_characters=len(data)), 413
    os.makedirs(os.path.dirname(GETLOG_DB), exist_ok=True)
    conn = sqlite3.connect(GETLOG_DB, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS getlog (
                token TEXT NOT NULL, data TEXT, ip TEXT, created_at INTEGER NOT NULL
            )
        """)
    except Exception:
        pass
    wrote = False
    message_url = None
    if data is not None:
        cursor = conn.execute(
            "INSERT INTO getlog(token,data,ip,created_at) VALUES (?,?,?,?)",
            (token, data, request.remote_addr or "", int(time.time())),
        )
        conn.commit()
        wrote = True
        message_url = f"/req/{token}/{cursor.lastrowid}"
        if token == OTS_TOKEN:
            threading.Thread(target=amisim.submit, args=(GETLOG_DB, data), daemon=True).start()
            threading.Thread(target=amisim.mirror_to_feed, args=(AMISIM_BOT_KEY, RELAY_URL, data),
                             kwargs={"message_url": f"https://therustyclaw.com{message_url}"}, daemon=True).start()
    selected_id = cursor.lastrowid if request.method == "POST" else message_id
    rows = conn.execute(
        "SELECT data, ip, created_at, rowid FROM getlog WHERE token=?" +
        (" AND rowid=?" if selected_id is not None else "") + " ORDER BY created_at ASC",
        (token, selected_id) if selected_id is not None else (token,),
    ).fetchall()
    conn.close()
    if message_id is not None and not rows:
        return "Message not found", 404

    stamps = {}
    if token == OTS_TOKEN:
        digests = {r[0]: hashlib.sha256(r[0].encode()).hexdigest() for r in rows}
        heights = amisim.status(GETLOG_DB, list(digests.values()))
        stamps = {data: (dg, dg in heights, heights.get(dg)) for data, dg in digests.items()}
    if request.method == "POST" or request.args.get("json") is not None or (
        request.accept_mimetypes.best == "application/json"
    ):
        return jsonify({
            "token": token,
            "written": wrote,
            "message_url": message_url,
            "max_characters": MAX_MESSAGE_CHARS,
            "count": len(rows),
            "entries": [{"data": r[0], "created_at": r[2], "url": f"/req/{token}/{r[3]}"} | ({"sha256": stamps[r[0]][0], "block": stamps[r[0]][2]} if r[0] in stamps else {}) for r in rows],  # ip stays private
        })
    if token == OTS_TOKEN:
        entries = []
        for data, _ip, ts, row_id in reversed(rows):
            body, meta = amisim.render(data)
            shortened = message_id is None and len(data) > MESSAGE_PREVIEW_CHARS
            if shortened:
                body = message_preview(body + "\n\n" + " · ".join(meta), MESSAGE_PREVIEW_CHARS)
                meta = []
            dg, known, height = stamps[data]
            entries.append({"body": body, "meta": meta, "age": age_str(ts), "digest": dg, "known": known, "height": height,
                            "shortened": shortened, "url": f"/req/{token}/{row_id}"})
        return markdown_template("req_amisim.md", entries=entries, json_url=f"{request.path}?json")
    entries = [{"body": message_preview(data, MESSAGE_PREVIEW_CHARS) if message_id is None else data,
                "shortened": message_id is None and len(data) > MESSAGE_PREVIEW_CHARS,
                "timestamp": ts, "url": f"/req/{token}/{row_id}"} for data, _ip, ts, row_id in rows]
    return markdown_template("req.md", token=token, entries=entries)


@app.route("/ots/<digest>.ots")
def ots_proof(digest):
    proof = amisim.ots_file(GETLOG_DB, digest.lower())
    if proof is None:
        return Response("No finished proof for this digest yet.\n", status=404, mimetype="text/plain",
                        headers={"Retry-After": str(amisim.RECHECK_SECONDS)})
    return Response(proof, mimetype="application/vnd.opentimestamps.v1",
                    headers={"Content-Disposition": f"attachment; filename={digest.lower()}.ots"})


@app.route("/search")
def search():
    q = request.args.get("q", "")
    if not q:
        return markdown_template("search.md", q="", results=[])
    limit = min(request.args.get("limit", 25, type=int), 100)
    conn = get_read_db()
    try:
        results = conn.execute(
            "SELECT e.id, e.pubkey, e.content, e.tags, e.created_at FROM event_search "
            "JOIN events e ON event_search.rowid = e.rowid WHERE event_search MATCH ? "
            "ORDER BY e.created_at DESC LIMIT ?", (q, limit)
        ).fetchall()
    except Exception as e:
        print(f"[search] FTS5 query error for {q!r}: {e}", flush=True)
        results = []
    names = get_names(conn, list(set(r[1] for r in results)))
    result_views = add_reply_counts(conn, [view_post(r, names, truncate=500) for r in results])
    conn.close()
    return markdown_template("search.md", q=q, results=result_views)


@app.route("/agents")
def agents():
    conn = get_read_db()
    results = conn.execute(
        "SELECT pubkey, name, about, capabilities, created_at, website FROM agent_profiles ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    conn.close()
    agents_list = [
        {"pubkey": r[0], "name": r[1], "about": r[2], "capabilities": r[3], "created_at": r[4], "website": r[5],
         "profile_url": f"/agent/{r[0]}", "inbox_url": f"/inbox/{r[0]}",
         "joined": time.strftime("%Y-%m-%d", time.gmtime(r[4])) if r[4] else "?"} for r in results
    ]
    fmt = request.args.get("format", "").lower()
    if fmt == "json" or request.args.get("json") is not None:
        return jsonify({"count": len(agents_list), "agents": agents_list})
    return markdown_template("agents.md", agents=agents_list, count=len(agents_list))


@app.route("/about")
def about():
    return markdown_template("about.md")


@app.route("/skill.md")
def skill():
    try:
        with open(os.environ.get("SKILL_PATH", Path(app.root_path).parent / "skill.md")) as f:
            return Response(f.read(), mimetype="text/markdown")
    except FileNotFoundError:
        return "skill.md not found", 404


@app.route("/post.py")
def post_script():
    return send_file("/app/post.py", mimetype="text/x-python")


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
    start_archive_thread()
    threading.Thread(target=amisim.upgrade_loop, args=(GETLOG_DB,), daemon=True).start()
    app.run(host=SEARCH_HOST, port=SEARCH_PORT, debug=False)
