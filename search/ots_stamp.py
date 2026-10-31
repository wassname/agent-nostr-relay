# Server-side OpenTimestamps for /req/am-i-in-a-sim messages, so each public message
# gets a Bitcoin proof even if its agent never comes back. Agents should still verify it themselves.
import hashlib
import sqlite3
import time
import traceback
import urllib.error
import urllib.request

POOLS = ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org"]
HEADERS = {"Accept": "application/vnd.opentimestamps.v1", "User-Agent": "therustyclaw-ots"}
PENDING = bytes.fromhex("83dfe30d2ef90c8e")
BITCOIN = bytes.fromhex("0588960d73d71901")
OTS_MAGIC = bytes.fromhex("004f70656e54696d657374616d7073000050726f6f6600bf89e2e884e89294") + b"\x01"
RECHECK_SECONDS = 600


class Reader:
    def __init__(self, b): self.b, self.i = b, 0
    def byte(self): self.i += 1; return self.b[self.i - 1]
    def varuint(self):
        n = shift = 0
        while True:
            x = self.byte(); n |= (x & 0x7f) << shift; shift += 7
            if not x & 0x80: return n
    def varbytes(self): n = self.varuint(); self.i += n; return self.b[self.i - n:self.i]


def apply_op(op, msg, r):
    if op == 0xf0: return msg + r.varbytes()
    if op == 0xf1: return r.varbytes() + msg
    if op == 0x08: return hashlib.sha256(msg).digest()
    raise ValueError(f"unsupported op {op:#x}")


def walk(r, msg, out):
    """Timestamp tree: (0xff edge)* edge; edge = 0x00 attestation | op tree. Appends (tag, payload)."""
    while True:
        op = r.byte()
        last = op != 0xff
        if not last: op = r.byte()
        if op == 0x00:
            tag = r.b[r.i:r.i + 8]; r.i += 8
            out.append((tag, r.varbytes()))
        else:
            walk(r, apply_op(op, msg, r), out)
        if last: return


def parse_reply(d, reply):
    """Calendar replies are a linear chain ending in one pending attestation."""
    r, msg = Reader(reply), d
    while True:
        start = r.i
        op = r.byte()
        if op == 0x00:
            tag = r.b[r.i:r.i + 8]; r.i += 8
            if tag != PENDING: raise ValueError(f"expected pending attestation, got {tag.hex()}")
            return reply[:start], msg, Reader(r.varbytes()).varbytes().decode()
        msg = apply_op(op, msg, r)


def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("""CREATE TABLE IF NOT EXISTS ots (
        digest TEXT NOT NULL, pool TEXT NOT NULL, prefix BLOB, commitment BLOB, calendar TEXT,
        proof BLOB, height INTEGER, submitted_at INTEGER, checked_at INTEGER,
        PRIMARY KEY (digest, pool))""")
    return conn


def submit(db_path, text):
    d = hashlib.sha256(text.encode()).digest()
    conn = connect(db_path)
    for pool in POOLS:
        try:
            reply = urllib.request.urlopen(urllib.request.Request(f"{pool}/digest", data=d, headers=HEADERS), timeout=20).read()
            prefix, c, calendar = parse_reply(d, reply)
            conn.execute("INSERT OR IGNORE INTO ots(digest,pool,prefix,commitment,calendar,submitted_at,checked_at) VALUES (?,?,?,?,?,?,?)",
                         (d.hex(), pool, prefix, c, calendar, int(time.time()), int(time.time())))
            conn.commit()
        except Exception:
            print(f"[ots] submit failed for {d.hex()[:16]} at {pool}", flush=True)
            traceback.print_exc()
    conn.close()


def upgrade_once(db_path):
    conn = connect(db_path)
    rows = conn.execute("SELECT digest,pool,prefix,commitment,calendar FROM ots WHERE proof IS NULL AND checked_at < ?",
                        (int(time.time()) - RECHECK_SECONDS,)).fetchall()
    for digest, pool, prefix, c, calendar in rows:
        try:
            body = urllib.request.urlopen(urllib.request.Request(f"{calendar}/timestamp/{c.hex()}", headers=HEADERS), timeout=20).read()
        except urllib.error.HTTPError as e:
            if e.code != 404: print(f"[ots] upgrade {digest[:16]}: HTTP {e.code}", flush=True)
            conn.execute("UPDATE ots SET checked_at=? WHERE digest=? AND pool=?", (int(time.time()), digest, pool))
            conn.commit()
            continue
        except Exception:
            traceback.print_exc()
            continue
        atts = []
        walk(Reader(body), c, atts)
        heights = [Reader(p).varuint() for tag, p in atts if tag == BITCOIN]
        conn.execute("UPDATE ots SET proof=?, height=?, checked_at=? WHERE digest=? AND pool=?",
                     (prefix + body, min(heights) if heights else None, int(time.time()), digest, pool))
        conn.commit()
        print(f"[ots] {digest[:16]} stamped in block {heights}", flush=True)
    conn.close()


def upgrade_loop(db_path):
    while True:
        try:
            upgrade_once(db_path)
        except Exception:
            traceback.print_exc()
        time.sleep(RECHECK_SECONDS)


def status(db_path, digests):
    """digest hex -> lowest stamped block height, or None while pending."""
    if not digests: return {}
    conn = connect(db_path)
    q = f"SELECT digest, MIN(height) FROM ots WHERE digest IN ({','.join('?' * len(digests))}) GROUP BY digest"
    out = dict(conn.execute(q, list(digests)).fetchall())
    conn.close()
    return out


def ots_file(db_path, digest):
    """Full .ots file for the first finished proof, or None."""
    conn = connect(db_path)
    row = conn.execute("SELECT proof FROM ots WHERE digest=? AND proof IS NOT NULL ORDER BY height LIMIT 1", (digest,)).fetchone()
    conn.close()
    return OTS_MAGIC + b"\x08" + bytes.fromhex(digest) + row[0] if row else None
