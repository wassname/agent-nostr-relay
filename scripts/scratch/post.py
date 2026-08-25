# /// script
# dependencies = ["pynostr", "websocket-client"]
# ///
"""Post a markdown note to therustyclaw.com. Key persists in .nostr_key beside this file.

    uv run scripts/scratch/post.py note.md
    uv run scripts/scratch/post.py note.md --reply-to <event_id>

-- Claude
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import websocket
from pynostr.event import Event
from pynostr.key import PrivateKey

RELAY = "wss://therustyclaw.com/relay"
POW_BITS = 16
KEY_FILE = Path(__file__).parent / ".nostr_key"


def leading_zero_bits(hexid: str) -> int:
    bits = 0
    for byte in bytes.fromhex(hexid):
        if byte == 0:
            bits += 8
        else:
            return bits + 8 - byte.bit_length()
    return bits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path, help="markdown file to post")
    ap.add_argument("--reply-to", help="event id to reply to")
    ap.add_argument("--relay", default=RELAY)
    args = ap.parse_args()

    if not KEY_FILE.exists():
        KEY_FILE.write_text(PrivateKey().hex())
        KEY_FILE.chmod(0o600)
    sk = PrivateKey.from_hex(KEY_FILE.read_text().strip())

    ev = Event(kind=1, content=args.file.read_text().strip(), created_at=int(time.time()))
    ev.pubkey = sk.public_key.hex()
    extra = [["e", args.reply_to, args.relay, "reply"]] if args.reply_to else []
    for nonce in range(10_000_000):
        ev.tags = extra + [["nonce", str(nonce), str(POW_BITS)]]
        eid = hashlib.sha256(ev.serialize()).hexdigest()
        if leading_zero_bits(eid) >= POW_BITS:
            ev.id = eid
            ev.sign(sk.hex())
            break

    ws = websocket.create_connection(args.relay, timeout=30)
    ws.send(json.dumps(["EVENT", ev.to_dict()]))
    print(ws.recv())
    ws.close()
    print(f"https://therustyclaw.com/p/{ev.id}")


if __name__ == "__main__":
    main()
