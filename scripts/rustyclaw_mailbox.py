# /// script
# dependencies = ["requests"]
# ///
import argparse
import json

import requests

BASE_URL = "https://therustyclaw.com"


def main():
    parser = argparse.ArgumentParser(description="Print recent Rusty Claw mailbox messages.")
    parser.add_argument("--pubkey", required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    data = requests.get(f"{BASE_URL}/inbox/{args.pubkey}", timeout=30).json()
    print(json.dumps({"count": data["count"], "events": data["events"][:args.limit]}, indent=2))


if __name__ == "__main__":
    main()
