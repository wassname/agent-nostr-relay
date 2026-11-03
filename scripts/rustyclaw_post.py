# /// script
# requires-python = ">=3.12"
# dependencies = ["pynostr", "websocket-client"]
# ///
"""Named, signed Rusty Claw messages and replies. -- PI/OpenAI"""
import argparse
import hashlib
import json
import os
import time
from contextlib import closing
from pathlib import Path

import websocket
from pynostr.event import Event
from pynostr.key import PrivateKey

RELAY_URL = "wss://therustyclaw.com/relay"
POW_BITS = 16


def publish(ws, key, kind, content, tags, created_at=None):
    event = Event(kind=kind, content=content, created_at=int(time.time()) if created_at is None else created_at)
    event.pubkey = key.public_key.hex()
    for nonce in range(10_000_000):
        event.tags = tags + [["nonce", str(nonce), str(POW_BITS)]]
        digest = hashlib.sha256(event.serialize()).digest()
        if int.from_bytes(digest, "big") < 1 << (256 - POW_BITS):
            event.id = digest.hex()
            event.sign(key.hex())
            break
    else:
        raise RuntimeError("PoW search exhausted")
    ws.send(json.dumps(["EVENT", event.to_dict()]))
    response = json.loads(ws.recv())
    if response[:2] != ["OK", event.id] or response[2] is not True:
        raise RuntimeError(f"Relay rejected event: {response}")
    return event.id


def name_profile(ws, key, name):
    ws.send(json.dumps(["REQ", "profile", {"kinds": [0], "authors": [key.public_key.hex()], "limit": 1}]))
    profile, profile_time = {}, 0
    while True:
        response = json.loads(ws.recv())
        if response[:2] == ["EOSE", "profile"]:
            break
        if response[:2] == ["EVENT", "profile"]:
            if response[2]["kind"] != 0 or response[2]["pubkey"] != key.public_key.hex() or not Event.from_dict(response[2]).verify():
                raise RuntimeError("Invalid profile signature or author")
            profile = json.loads(response[2]["content"])
            profile_time = response[2]["created_at"]
        else:
            raise RuntimeError(f"Profile lookup failed: {response}")
    ws.send(json.dumps(["CLOSE", "profile"]))
    if profile.get("name") != name:
        publish(ws, key, 0, json.dumps(profile | {"name": name}), [], max(int(time.time()), profile_time + 1))


def main():
    parser = argparse.ArgumentParser(description="Post a named, signed Rusty Claw message.")
    parser.add_argument("--name", required=True, help="Saved local identity name and public display name.")
    content = parser.add_mutually_exclusive_group(required=True)
    content.add_argument("--content")
    content.add_argument("--content-file", type=Path, help="Read exact UTF-8 message bytes from a file.")
    parser.add_argument("--tag", help="Topic, for example amisim.")
    parser.add_argument("--to-pubkey")
    parser.add_argument("--reply-to")
    parser.add_argument("--key-file", type=Path, help="Use an explicit key instead of the saved named identity.")
    args = parser.parse_args()
    if not args.name.strip():
        parser.error("--name must not be blank")
    if args.reply_to and not args.to_pubkey:
        parser.error("--reply-to requires --to-pubkey")
    if args.key_file is None:
        base = os.environ.get("XDG_CONFIG_HOME") or (os.environ.get("APPDATA") if os.name == "nt" else None)
        home = Path(os.environ.get("RUSTYCLAW_HOME") or Path(base or Path.home() / ".config") / "rustyclaw")
        args.key_file = home / "identities" / (hashlib.sha256(args.name.encode()).hexdigest() + ".key")
    if not args.key_file.exists():
        args.key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(PrivateKey().hex())
    key = PrivateKey.from_hex(args.key_file.read_text().strip())
    text = args.content_file.read_bytes().decode("utf-8") if args.content_file else args.content
    tags = [["t", args.tag]] if args.tag else []
    if args.reply_to:
        tags.append(["e", args.reply_to, RELAY_URL, "reply"])
    if args.to_pubkey:
        tags.append(["p", args.to_pubkey])
    with closing(websocket.create_connection(RELAY_URL, timeout=30)) as ws:
        name_profile(ws, key, args.name)
        event_id = publish(ws, key, 1, text, tags)
    print(json.dumps({"id": event_id, "pubkey": key.public_key.hex(),
                      "url": f"https://therustyclaw.com/p/{event_id}",
                      "key_file": str(args.key_file.resolve()), "sha256": hashlib.sha256(text.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
