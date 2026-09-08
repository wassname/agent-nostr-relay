# /// script
# dependencies = ["flask", "boto3", "websocket-client", "markdown", "nh3", "cowsay==6.1", "wcwidth==0.2.13", "playwright"]
# ///
"""CODEX: Render the real feed locally and save browser evidence for the crab header."""

import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/slop/review/crab-preview"
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "search"))
import search as relay

with tempfile.TemporaryDirectory(prefix="rustyclaw-preview-") as database_dir:
    relay.DB_PATH = str(Path(database_dir) / "search.db")
    relay.GETLOG_DB = str(Path(database_dir) / "getlog.db")
    relay.NIP05_DB = str(Path(database_dir) / "nip05.db")
    relay.init_db()
    server = make_server("127.0.0.1", 0, relay.app)
    Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 650}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install()
        with patch.object(relay.random, "choice", side_effect=lambda values: values[0]):
            response = page.goto(f"http://127.0.0.1:{server.server_port}/")
        assert response.status == 200
        page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
        face = page.locator("#face")
        initial = face.bounding_box()
        samples = []
        for expected in relay.KAOMOJI_FACES:
            text = face.text_content()
            assert text.strip() == expected, (text, expected)
            assert relay.wcswidth(text) == 9
            bounds = face.bounding_box()
            assert abs(bounds["width"] - initial["width"]) < 0.1, bounds
            assert bounds["x"] == initial["x"]
            samples.append({"face": text, "display_columns": relay.wcswidth(text), "bounds": bounds})
            page.locator(".sign").screenshot(path=str(OUTPUT / f"face-{len(samples)}.png"))
            page.clock.fast_forward(10000)
        assert face.text_content().strip() == relay.KAOMOJI_FACES[0]
        page.close()
        page = browser.new_page(viewport={"width": 375, "height": 650}, device_scale_factor=2, is_mobile=True)
        page.on("pageerror", lambda error: errors.append(str(error)))
        for line in relay.BARKEEP_LINES:
            with patch.object(relay.random, "choice", side_effect=[relay._center_width("•‿•", 9), line]):
                page.goto(f"http://127.0.0.1:{server.server_port}/")
            assert line in page.locator(".sign").text_content()
            assert page.locator(".sign").evaluate("el => el.scrollWidth <= el.clientWidth")
        page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
        assert not errors, errors
        (OUTPUT / "checks.json").write_text(json.dumps({
            "author": "CODEX", "route_status": response.status,
            "faces": samples, "mobile_bubbles_checked": relay.BARKEEP_LINES,
            "rotation_wraps": True, "javascript_errors": errors,
        }, indent=2, ensure_ascii=False) + "\n")
        browser.close()
    server.shutdown()

print(f"PASS: eight face rotations, seven mobile bubbles. Evidence: {OUTPUT}")
