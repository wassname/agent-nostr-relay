# /// script
# dependencies = ["flask>=3.1", "markdown-it-py", "nh3", "websocket-client", "boto3", "cowsay==6.1", "wcwidth==0.2.13"]
# ///
"""Long-message preservation, rejection and read-more links. -- PI/OpenAI"""
import hashlib
import json
import re
import sqlite3
import tempfile
from unittest.mock import patch

import search


with tempfile.TemporaryDirectory() as directory:
    search.DB_PATH = f"{directory}/search.db"
    search.GETLOG_DB = f"{directory}/getlog.db"
    search.NIP05_DB = f"{directory}/names.db"
    search.init_db()
    client = search.app.test_client()
    endpoint = "/req/am-i-in-a-sim"

    with patch("search.threading.Thread") as thread:
        for text in ["a" * 65_536, "🦀" * 65_536, " \r\nA café & + % ? #\x00\t\r\n "]:
            response = client.post(endpoint, data=text.encode(), content_type="text/plain; charset=utf-8")
            assert response.status_code == 200, response.text
            receipt = response.json
            assert receipt["written"] is True and receipt["count"] == 1, receipt
            assert receipt["entries"][0]["data"] == text
            assert receipt["entries"][0]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
            url = receipt["message_url"]
            assert client.get(url + "?json").json["entries"][0]["data"] == text
            assert client.get(url.replace("am-i-in-a-sim", "other-token")).status_code == 404
            assert client.post(url, data="overwrite", content_type="text/plain").status_code == 405
            assert client.get(url, query_string={"data": "must-not-write", "json": ""}).json["written"] is False
            assert f"[JSON view]({url}?json)" in client.get(url + "?format=md").text
            assert thread.call_args_list[-2].kwargs["args"][-1] == text
            assert thread.call_args_list[-1].kwargs["args"][-1] == text
            assert thread.call_args_list[-1].kwargs["kwargs"]["message_url"].endswith(url)

        thread.reset_mock()
        before = client.get(endpoint + "?json").json["count"]
        for text, method in [("a" * 65_537, "POST"), ("🦀" * 65_537, "POST"), ("a" * 2_001, "GET")]:
            if method == "POST":
                response = client.post(endpoint, data=text.encode(), content_type="text/plain")
            else:
                response = client.get(endpoint, query_string={"data": text})
            assert response.status_code == 413, response.text
            assert "nothing was saved" in response.json["error"]
            assert client.get(endpoint + "?json").json["count"] == before
            thread.assert_not_called()
        for raw, content_type, status in [(b"\xff", "text/plain", 400),
                                          (b"  \r\n", "text/plain", 400),
                                          (b'{"data":"hello"}', "application/json", 415)]:
            assert client.post(endpoint, data=raw, content_type=content_type).status_code == status
            assert client.get(endpoint + "?json").json["count"] == before
            thread.assert_not_called()
        assert client.head(endpoint, query_string={"data": "must-not-write"}).status_code == 200
        assert client.get(endpoint + "?json").json["count"] == before
        thread.assert_not_called()
        assert client.get(endpoint, query_string={"data": "g" * 2_000, "json": ""}).json["written"] is True

    with sqlite3.connect(search.GETLOG_DB) as conn:
        assert conn.execute("SELECT data FROM getlog ORDER BY rowid LIMIT 1").fetchone()[0] == "a" * 65_536

    post_id, pubkey = "c" * 64, "d" * 64
    text = "LongPreview\n\n```text\n" + "x" * 2_000 + "\n```\nEND OF MESSAGE"
    search.index_event(dict(id=post_id, pubkey=pubkey, kind=1, content=text, tags=[], created_at=1))
    for path in ["/", "/agent/" + pubkey, "/search?q=LongPreview"]:
        preview = client.get(path + ("&" if "?" in path else "?") + "format=md").text
        assert "END OF MESSAGE" not in preview
        rendered = search.render_markdown(preview)
        assert re.search(f'<a href="/p/{post_id}"[^>]*>Read full message</a>', rendered), rendered
    assert text in client.get("/p/" + post_id + "?format=md").text
    short = search.view_post(("e" * 64, pubkey, "short", "[]", 1), {}, truncate=500)
    assert short["shortened"] is False and short["content"] == "short"

    for token, message in [("browser", text), ("am-i-in-a-sim", json.dumps({"who": "LongPreview", "message": text}))]:
        with patch("search.threading.Thread"):
            receipt = client.post("/req/" + token, data=message.encode(), content_type="text/plain").json
        preview = client.get("/req/" + token + "?format=md").text
        assert "END OF MESSAGE" not in preview
        assert re.search(f'<a href="{receipt["message_url"]}"[^>]*>Read full message</a>', search.render_markdown(preview))
        assert "END OF MESSAGE" in client.get(receipt["message_url"] + "?format=md").text
        assert client.get(receipt["message_url"] + "?json").json["entries"][0]["data"] == message

    for message in ["short message", "x" * 65_536, "🦀" * 1_000]:
        url = "https://therustyclaw.com/req/am-i-in-a-sim/123"
        with patch("amisim.bot_key", return_value=object()), patch("amisim._publish", return_value="event-id") as publish:
            assert search.amisim.mirror_to_feed("unused", "unused", message, message_url=url) == "event-id"
            content = publish.call_args.args[3]
            assert url in content and len(json.dumps(content).encode()) <= 4_000
            digest = hashlib.sha256(message.encode()).hexdigest()
            assert ["x", digest] in publish.call_args.args[4]
            if message != "short message":
                assert "too long to mirror" in content and digest in content
            else:
                assert message in content

    search._db_conn.close()
    search._db_conn = None
print("PASS: exact 65,536-character ASCII/Unicode writes, hashes, rejection without side effects, HEAD safety, read-more links and long-message feed pointers.")
