# /// script
# dependencies = ["flask>=3.1", "markdown-it-py", "nh3", "websocket-client", "boto3", "cowsay==6.1", "wcwidth==0.2.13", "pynostr", "websockets"]
# ///
"""Real local signed posting, named replies and automatic timestamps. -- PI/OpenAI"""
import hashlib
import io
import json
import os
import queue
import sqlite3
import sys
import tempfile
import threading
from contextlib import chdir, closing, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pynostr.event import Event
from pynostr.key import PrivateKey
from websockets.sync.server import serve

import search

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rustyclaw_post as post


events, submitted, tamper_profiles = [], queue.Queue(), set()
real_submit = search.amisim.submit


def submit(db, text):
    real_submit(db, text)
    submitted.put(text)


def relay(ws):
    for raw in ws:
        message = json.loads(raw)
        if message[0] == "CLOSE":
            continue
        if message[0] == "REQ":
            profiles = [e for e in events if e["kind"] == 0 and e["pubkey"] in message[2]["authors"]]
            if profiles:
                latest = max(profiles, key=lambda e: (e["created_at"], -int(e["id"], 16)))
                if latest["pubkey"] in tamper_profiles:
                    latest = latest | {"content": json.dumps({"name": "Forged profile"})}
                ws.send(json.dumps(["EVENT", message[1], latest]))
            ws.send(json.dumps(["EOSE", message[1]]))
            continue
        assert message[0] == "EVENT", message
        event = message[1]
        canonical = json.dumps([0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
                               separators=(",", ":"), ensure_ascii=False)
        assert hashlib.sha256(canonical.encode()).hexdigest() == event["id"]
        assert Event.from_dict(event).verify() and int(event["id"], 16) < 2**240
        search.index_event(event)
        events.append(event)
        ws.send(json.dumps(["OK", event["id"], True, ""]))


def varbytes(value):
    length, prefix = len(value), b""
    while length >= 128:
        prefix += bytes([(length & 127) | 128])
        length >>= 7
    return prefix + bytes([length]) + value


for uri in ("https://alice.btc.calendar.opentimestamps.org", "https://bob.btc.calendar.opentimestamps.org",
            "https://calendar.example/" + "x" * 128):
    encoded = b"\x00" + search.amisim.PENDING + varbytes(varbytes(uri.encode()))
    prefix, commitment, decoded = search.amisim.parse_reply(b"\x00" * 32, encoded)
    assert prefix == b"" and commitment == b"\x00" * 32 and decoded == uri


def run(key, name, *args):
    output = io.StringIO()
    argv = ["post.py", "--name", name, *args] + (["--key-file", str(key)] if key is not None else [])
    with patch.object(sys, "argv", argv), redirect_stdout(output):
        post.main()
    return json.loads(output.getvalue())


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    search.DB_PATH, search.GETLOG_DB, search.NIP05_DB = [str(root / f"{name}.db") for name in ("search", "getlog", "names")]
    search.init_db()
    client = search.app.test_client()
    calendar = b"https://calendar.example"
    pending = b"\x00" + search.amisim.PENDING + varbytes(varbytes(calendar))
    with serve(relay, "127.0.0.1", 0) as server, \
         patch("amisim.urllib.request.urlopen", return_value=SimpleNamespace(read=lambda: pending)) as http, \
         patch("amisim.submit", side_effect=submit):
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        post.RELAY_URL = f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        key = root / "identity/private.key"
        text = "Hello café 🦀\r\n\r\nA named message with exact bytes.\n"
        message = root / "message.md"
        message.write_bytes(text.encode())
        first = run(key, "Coinflip", "--tag", "amisim", "--content-file", str(message))
        assert submitted.get(timeout=10) == text
        assert key.stat().st_mode & 0o777 == 0o600
        assert json.loads(events[0]["content"])["name"] == "Coinflip" and events[0]["kind"] == 0
        assert events[1]["kind"] == 1 and events[1]["content"] == text
        assert first["sha256"] == hashlib.sha256(text.encode()).hexdigest()
        raw = client.get(f'/p/{first["id"]}?json').json
        assert raw["content"] == text and raw["pubkey"] == first["pubkey"]
        markdown = client.get(f'/p/{first["id"]}?format=md').text
        assert "Coinflip" in markdown and "Site timestamp: waiting for Bitcoin" in markdown
        assert first["sha256"] in markdown and "<small>" in markdown
        assert "<small>" in search.render_markdown(markdown)
        pending_proof = client.get(f'/ots/{first["sha256"]}.ots')
        assert pending_proof.status_code == 404
        assert pending_proof.headers["Retry-After"] == str(search.amisim.RECHECK_SECONDS)

        reply = run(key, "Coinflip", "--tag", "amisim", "--reply-to", first["id"],
                    "--to-pubkey", first["pubkey"], "--content", "The proof is still pending.")
        assert submitted.get(timeout=10) == "The proof is still pending."
        assert len(events) == 3 and reply["pubkey"] == first["pubkey"]
        assert ["e", first["id"], post.RELAY_URL, "reply"] in events[-1]["tags"]
        assert ["p", first["pubkey"]] in events[-1]["tags"]
        replies = client.get(f'/replies/{first["id"]}').json["events"]
        assert len(replies) == 1 and replies[0]["author_name"] == "Coinflip"
        assert 'The proof is still pending.' in client.get(f'/p/{first["id"]}?format=md').text
        tagged = client.get('/search?q=amisim&format=md').text
        assert first["id"] in tagged and reply["id"] in tagged and "Coinflip" in tagged
        with sqlite3.connect(search.GETLOG_DB) as conn:
            assert conn.execute("SELECT COUNT(*) FROM getlog").fetchone()[0] == 0
        assert http.call_count == 4
        search.index_event(events[1])
        assert submitted.empty()
        real_submit(search.GETLOG_DB, text)
        assert http.call_count == 4

        with closing(post.websocket.create_connection(post.RELAY_URL, timeout=10)) as ws:
            post.publish(ws, PrivateKey.from_hex(key.read_text()), 0,
                         json.dumps({"name": "Coinflip", "about": "Keep this", "agent": {"type": "test"}}), [],
                         events[0]["created_at"] + 1)
        renamed = run(key, "Ledger", "--content", "An ordinary untagged note.")
        profile = json.loads(events[-2]["content"])
        assert profile == {"name": "Ledger", "about": "Keep this", "agent": {"type": "test"}}
        assert events[-2]["created_at"] > events[-3]["created_at"]
        assert renamed["pubkey"] == first["pubkey"] and submitted.empty()
        before = len(events)
        tamper_profiles.add(first["pubkey"])
        try:
            run(key, "Ledger", "--content", "Must not be published.")
        except RuntimeError as error:
            assert "Invalid profile" in str(error)
        else:
            raise AssertionError("A forged profile was accepted")
        tamper_profiles.clear()
        assert len(events) == before
        assert http.call_count == 4
        search.index_event(dict(id="e" * 64, pubkey=first["pubkey"], kind=30078, content="",
                                tags=[["capability", "repeatable"]], created_at=1))
        assert search.get_db().execute("SELECT rowid FROM agent_search WHERE agent_search MATCH 'repeatable'").fetchone()
        search.index_event(dict(id="f" * 64, pubkey=first["pubkey"], kind=5, content="",
                                tags=[["e", renamed["id"]]], created_at=1))
        assert client.get(f'/p/{renamed["id"]}').status_code == 404
        for table in ("agent_search", "event_search"):
            search.get_db().execute(f"INSERT INTO {table}({table}, rank) VALUES('integrity-check', 1)")
        search.get_db().commit()

        with sqlite3.connect(search.GETLOG_DB) as conn:
            conn.execute("UPDATE ots SET checked_at=0")
        search.amisim.upgrade_once(search.GETLOG_DB)
        with sqlite3.connect(search.GETLOG_DB) as conn:
            assert conn.execute("SELECT COUNT(*) FROM ots WHERE proof IS NOT NULL").fetchone()[0] == 0
            conn.execute("UPDATE ots SET proof=? WHERE digest=?", (b"pending", first["sha256"]))
        assert search.amisim.ots_file(search.GETLOG_DB, first["sha256"]) is None
        with sqlite3.connect(search.GETLOG_DB) as conn:
            conn.execute("UPDATE ots SET proof=NULL, checked_at=0")
        bitcoin = b"\x00" + search.amisim.BITCOIN + b"\x01\x2a"
        with patch("amisim.urllib.request.urlopen", return_value=SimpleNamespace(read=lambda: bitcoin)):
            search.amisim.upgrade_once(search.GETLOG_DB)
        assert search.amisim.status(search.GETLOG_DB, [first["sha256"]])[first["sha256"]] == 42
        assert search.amisim.ots_file(search.GETLOG_DB, first["sha256"]).startswith(search.amisim.OTS_MAGIC)
        markdown = client.get(f'/p/{first["id"]}?format=md').text
        assert "Site timestamp: Bitcoin block 42" in markdown and first["sha256"] in markdown
        for name in ("work-a", "work-b"):
            (root / name).mkdir()
        with patch.dict(os.environ, {"RUSTYCLAW_HOME": str(root / "persistent")}):
            with chdir(root / "work-a"):
                a = run(None, "Nomad", "--content", "First directory.")
            with chdir(root / "work-b"):
                b = run(None, "Nomad", "--content", "Second directory.")
                other = run(None, "Another agent", "--content", "Independent identity.")
        assert a["key_file"] == b["key_file"] and a["pubkey"] == b["pubkey"]
        assert a["key_file"] != other["key_file"] and a["pubkey"] != other["pubkey"]
        assert Path(a["key_file"]).parent == root / "persistent/identities"
        with patch.dict(os.environ, {"RUSTYCLAW_HOME": "", "XDG_CONFIG_HOME": str(root / "xdg")}):
            xdg = run(None, "Nomad", "--content", "XDG storage.")
        assert Path(xdg["key_file"]).parent == root / "xdg/rustyclaw/identities"
    search.get_db().executescript("""
        DROP TRIGGER agent_profiles_au;
        UPDATE agent_profiles SET name='Legacy';
        DELETE FROM sync_state WHERE key='fts_triggers_v1';
    """)
    search.init_db()
    assert search.get_db().execute("SELECT rowid FROM agent_search WHERE agent_search MATCH 'Legacy'").fetchone()
    assert not search.get_db().execute("SELECT rowid FROM agent_search WHERE agent_search MATCH 'Ledger'").fetchone()
    for table in ("agent_search", "event_search"):
        search.get_db().execute(f"INSERT INTO {table}({table}, rank) VALUES('integrity-check', 1)")
    search.get_db().commit()
    search._db_conn.close()
    search._db_conn = None

print("PASS: named real signed/PoW messages and replies, exact UTF-8 bytes, key continuity/permissions, profile preservation, automatic timestamping/deduplication, small full-hash metadata, pending-proof retry and JSON content.")
