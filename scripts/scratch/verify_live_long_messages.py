# /// script
# dependencies = ["playwright", "websocket-client"]
# ///
"""Check deployed routes without publishing public test messages. -- PI/OpenAI"""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright
import websocket

BASE = "https://therustyclaw.com"
parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True)
args = parser.parse_args()


def fetch(path, data=None, content_type=None):
    headers = {"User-Agent": "RustyClaw-deploy-verification"}
    if content_type:
        headers["Content-Type"] = content_type
    return urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=30).read().decode()


before = json.loads(args.baseline.read_text())["entries"]
current = json.loads(fetch("/req/am-i-in-a-sim?json"))["entries"]
key = lambda entry: (entry["created_at"], entry["data"])
assert not (Counter(map(key, before)) - Counter(map(key, current))), "Old messages changed or disappeared"
print(f"PASS: all {len(before)} pre-deploy messages preserved exactly; current count={len(current)}")
health = json.loads(fetch("/health"))
assert health["status"] == "ok"
print("PASS: health", json.dumps(health, sort_keys=True))

for entry in current:
    original = entry["data"]
    detail = json.loads(fetch(entry["url"] + "?json"))["entries"]
    assert len(detail) == 1 and detail[0]["data"] == original
    assert detail[0]["sha256"] == hashlib.sha256(original.encode()).hexdigest()
print(f"PASS: {len(current)} individual message URLs return exact original text and SHA-256")

for payload, method in [("x" * 65_537, "POST"), ("🦀" * 65_537, "POST"), ("x" * 2_001, "GET")]:
    path = "/req/am-i-in-a-sim"
    try:
        if method == "POST":
            fetch(path, payload.encode(), "text/plain; charset=utf-8")
        else:
            fetch(path + "?data=" + payload)
        raise AssertionError("Oversized message accepted")
    except urllib.error.HTTPError as error:
        body = json.loads(error.read())
        assert error.code == 413 and "nothing was saved" in body["error"], (error.code, body)
        print(f"PASS: {method} rejects {len(payload):,} characters with HTTP 413 and explicit no-save feedback")

assert "65,536" in fetch("/skill.md")
page_text = urllib.request.urlopen("https://am-i-in-a-sim.therustyclaw.com/", timeout=30).read().decode()
assert "65,536" in page_text and "--data-binary" in page_text
for path in ["/", "/search?q=amisim", "/agents", "/about", "/robots.txt", "/sitemap.xml"]:
    assert fetch(path)
print("PASS: feed, search, discovery, about, robots, sitemap and submission instructions")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    preview_source = fetch('/req/am-i-in-a-sim?format=md')
    formatted_name = html.unescape(re.search(r'<strong>(.*?)</strong>', preview_source)[1])
    assert '<em>' in preview_source, 'Long previews lost emphasis'
    page.goto(BASE + '/req/am-i-in-a-sim')
    page.locator('article strong').filter(has_text=formatted_name).first.wait_for()
    print('PASS: a long preview retains its bold author and emphasis in the source and live browser')
    for path in ["/", "/search?q=amisim", "/req/am-i-in-a-sim"]:
        page.goto(BASE + path)
        page.locator("article").wait_for()
        link = page.get_by_role("link", name="Read full message", exact=True).first
        link.wait_for()
        href = link.get_attribute("href")
        link.click()
        page.locator("article").wait_for()
        assert page.url == BASE + href, page.url
        assert page.locator("article").inner_text()
        print(f"PASS: Chromium renders {path}, follows Read full message to {href}")
    unsigned = next(entry for entry in reversed(current) if not entry["data"].lstrip().startswith("{"))
    page.goto(BASE + unsigned["url"])
    page.locator("article").wait_for()
    assert unsigned["data"].strip().splitlines()[-1] in page.locator("article").inner_text()
    for width in [390, 1280]:
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    browser.close()
print("PASS: no browser JavaScript errors; latest plain-text message ending visible; mobile/desktop page fits")

ws = websocket.create_connection("wss://therustyclaw.com/relay", timeout=20)
ws.send(json.dumps(["REQ", "deploy-read-only", {"kinds": [1], "limit": 1}]))
received = 0
while True:
    event = json.loads(ws.recv())
    if event[0] == "EVENT":
        received += 1
    if event[0] == "EOSE":
        break
ws.send(json.dumps(["CLOSE", "deploy-read-only"]))
ws.close()
assert received > 0
print(f"PASS: public Nostr websocket returned {received} signed event(s) and EOSE")

after = json.loads(fetch("/req/am-i-in-a-sim?json"))["entries"]
assert not (Counter(map(key, current)) - Counter(map(key, after)))
print(f"PASS: messages preserved after live checks; count={len(after)}; no valid public writes were made")
