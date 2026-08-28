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
    parser = argparse.ArgumentParser(description="Wait for Rusty Claw mailbox or reply events.")
    parser.add_argument("--pubkey", help="pubkey to check with a #p mailbox filter")
    parser.add_argument("--event-id", action="append", default=[], help="event id to check with a #e reply filter")
    parser.add_argument("--min-seconds", type=int, default=60, help="minimum seconds to collect events before returning")
    parser.add_argument("--wait-seconds", type=int, default=3600, help="maximum seconds to wait")
    parser.add_argument("--since", type=int, default=None, help="unix timestamp; default is command start")
    args = parser.parse_args()
    if not args.pubkey and not args.event_id:
        raise SystemExit("pass --pubkey or --event-id")
    if args.min_seconds > args.wait_seconds:
        raise SystemExit("--min-seconds must be <= --wait-seconds")

    since = args.since if args.since is not None else int(time.time())
    filters = []
    if args.pubkey:
        filters.append({"kinds": [1], "#p": [args.pubkey], "since": since})
    for event_id in args.event_id:
        filters.append({"kinds": [1], "#e": [event_id], "since": since})

    ws = websocket.create_connection(RELAY_URL, timeout=args.wait_seconds + 5)
    ws.send(json.dumps(["REQ", "rustyclaw-wait", *filters]))
    started = time.time()
    min_deadline = started + args.min_seconds
    deadline = started + args.wait_seconds
    events_by_id = {}
    try:
        while time.time() < deadline:
            if events_by_id and time.time() >= min_deadline:
                print(json.dumps(list(events_by_id.values()), indent=2), flush=True)
                return
            ws.settimeout(max(1, min(deadline, min_deadline if events_by_id else deadline) - time.time()))
            msg = json.loads(ws.recv())
            if msg[0] == "EVENT":
                events_by_id[msg[2]["id"]] = msg[2]
    except WebSocketTimeoutException:
        if events_by_id:
            print(json.dumps(list(events_by_id.values()), indent=2), flush=True)
            return
    finally:
        ws.close()
    raise SystemExit(124)


if __name__ == "__main__":
    main()
