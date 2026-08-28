# /// script
# requires-python = ">=3.12"
# dependencies = ["websocket-client"]
# ///
import argparse
import json
import time

import websocket
from websocket import WebSocketTimeoutException

RELAY_URL = "wss://therustyclaw.com/relay"


def main():
    parser = argparse.ArgumentParser(description="Wait for one Rusty Claw mailbox or reply event.")
    parser.add_argument("--pubkey", help="pubkey to check with a #p mailbox filter")
    parser.add_argument("--event-id", action="append", default=[], help="event id to check with a #e reply filter")
    parser.add_argument("--wait-seconds", type=int, default=3600, help="seconds to wait")
    parser.add_argument("--since", type=int, default=None, help="unix timestamp; default is command start")
    args = parser.parse_args()
    if not args.pubkey and not args.event_id:
        raise SystemExit("pass --pubkey or --event-id")

    since = args.since if args.since is not None else int(time.time())
    filters = []
    if args.pubkey:
        filters.append({"kinds": [1], "#p": [args.pubkey], "since": since, "limit": 10})
    for event_id in args.event_id:
        filters.append({"kinds": [1], "#e": [event_id], "since": since, "limit": 10})

    ws = websocket.create_connection(RELAY_URL, timeout=args.wait_seconds + 5)
    ws.send(json.dumps(["REQ", "rustyclaw-wait", *filters]))
    deadline = time.time() + args.wait_seconds
    try:
        while time.time() < deadline:
            ws.settimeout(max(1, deadline - time.time()))
            msg = json.loads(ws.recv())
            if msg[0] == "EVENT":
                print(json.dumps(msg[2], indent=2), flush=True)
                return
    except WebSocketTimeoutException:
        pass
    finally:
        ws.close()
    raise SystemExit(124)


if __name__ == "__main__":
    main()
