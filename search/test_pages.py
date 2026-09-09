# /// script
# dependencies = ["flask", "markdown", "nh3", "websocket-client", "boto3", "cowsay==6.1", "wcwidth==0.2.13", "playwright"]
# ///
"""Exercise Markdown pages and browser enhancement. -- Codex/GPT-6"""
import html
import os
from pathlib import Path
import re
import tempfile
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SEARCH_DB_PATH=f"{directory}/search.db",
                      NIP05_DB=f"{directory}/names.db", GETLOG_DB=f"{directory}/getlog.db")
    import search

    search.init_db()
    payload = '# Sample\n\n**bold** & <tag>\n\n</pre><script>window.injected = true</script>\n\n[x](javascript:alert(1))'
    search.index_event(dict(id='a'*64, pubkey='b'*64, kind=1, content=payload, tags=[], created_at=1))
    client = search.app.test_client()
    paths = ['/', '/p/'+'a'*64, '/agent/'+'b'*64, '/search', '/search?q=Sample',
             '/agents', '/about', '/skill.md', '/req/check']
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.get_data(as_text=True)
        source = html.unescape(re.search(r'<pre id="markdown">(.*?)</pre>', body, re.S)[1])
        plain = client.get(path + ('&' if '?' in path else '?') + 'format=md')
        if path != '/':
            assert source == plain.get_data(as_text=True), path
        rendered = client.post('/render-markdown', data=source, content_type='text/plain')
        assert rendered.status_code == 200
        assert '<script' not in rendered.get_data(as_text=True)
        assert 'href="javascript:' not in rendered.get_data(as_text=True)
    assert payload in client.get('/p/'+'a'*64+'?format=md').get_data(as_text=True)
    assert client.get('/skill.md?format=md').get_data(as_text=True) == Path('skill.md').read_text()
    assert client.get('/agents?format=json').is_json
    assert client.get('/req/check?json').is_json
    assert client.get('/inbox/'+'b'*64).is_json
    assert client.get('/p/missing').status_code == 404
    assert client.post('/render-markdown', data='x'*2_000_001).status_code == 413

    server = make_server('127.0.0.1', 0, search.app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        for path in paths:
            page.goto(f'http://127.0.0.1:{server.server_port}{path}')
            page.locator('article').wait_for()
            assert page.locator('pre#markdown').count() == 0
            assert page.evaluate('window.injected') is None
        page.goto(f'http://127.0.0.1:{server.server_port}/search')
        page.locator('input[name=q]').fill('Sample')
        page.get_by_role('button', name='Search', exact=True).click()
        page.locator('article strong').wait_for()
        assert '1 results' in page.locator('article').inner_text()
        no_js = browser.new_page(java_script_enabled=False)
        no_js.goto(f'http://127.0.0.1:{server.server_port}/p/'+ 'a'*64)
        assert payload in no_js.locator('#markdown').inner_text()
        browser.close()
    server.shutdown()
    search._db_conn.close()
print('Markdown routes, source preservation, sanitization, JS and no-JS browser checks passed.')
