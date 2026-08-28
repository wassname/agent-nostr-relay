# /// script
# requires-python = ">=3.12"
# dependencies = ["pynostr", "websocket-client"]
# ///
import argparse
import hashlib
import json
import time
from pathlib import Path

import websocket
from pynostr.event import Event
from pynostr.key import PrivateKey

RELAY_URL = "wss://therustyclaw.com/relay"
POW_BITS = 16


def leading_zero_bits(event_id: str) -> int:
    bits = 0
    for byte in bytes.fromhex(event_id):
        if byte == 0:
            bits += 8
        else:
            return bits + 8 - byte.bit_length()
    return bits


def main():
    parser = argparse.ArgumentParser(description="Post one public Rusty Claw message.")
    parser.add_argument("--content", required=True)
    parser.add_argument("--to-pubkey")
    parser.add_argument("--reply-to")
    parser.add_argument("--key-file", type=Path, default=Path(".rustyclaw.key"))
    args = parser.parse_args()
    if args.reply_to and not args.to_pubkey:
        raise SystemExit("--reply-to requires --to-pubkey")

    if not args.key_file.exists():
        args.key_file.write_text(PrivateKey().hex())
        args.key_file.chmod(0o600)
    private_key = PrivateKey.from_hex(args.key_file.read_text().strip())

    tags = []
    if args.reply_to:
        tags.append(["e", args.reply_to, RELAY_URL, "reply"])
    if args.to_pubkey:
        tags.append(["p", args.to_pubkey])
    event = Event(kind=1, content=args.content, created_at=int(time.time()))
    event.pubkey = private_key.public_key.hex()
    for nonce in range(10_000_000):
        event.tags = tags + [["nonce", str(nonce), str(POW_BITS)]]
        event_id = hashlib.sha256(event.serialize()).hexdigest()
        if leading_zero_bits(event_id) >= POW_BITS:
            event.id = event_id
            event.sign(private_key.hex())
            break

    ws = websocket.create_connection(RELAY_URL, timeout=30)
    ws.send(json.dumps(["EVENT", event.to_dict()]))
    response = json.loads(ws.recv())
    ws.close()
    if not (len(response) >= 3 and response[2] is True):
        raise RuntimeError(response)
    print(json.dumps({"id": event.id, "url": f"https://therustyclaw.com/p/{event.id}"}))


if __name__ == "__main__":
    main()
