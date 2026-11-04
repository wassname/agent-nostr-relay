# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["flask>=3.1", "markdown-it-py", "nh3", "websocket-client", "boto3", "cowsay==6.1", "wcwidth==0.2.13", "playwright"]
# ///
"""Check the real TV renderer, quote matching and browser layout. -- PI/OpenAI"""
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from wcwidth import wcswidth
from werkzeug.serving import make_server

with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SEARCH_DB_PATH=f'{directory}/search.db', GETLOG_DB=f'{directory}/getlog.db', NIP05_DB=f'{directory}/nip05.db')
    import search
    search.init_db()
    data = search.BARKEEP
    assert search.tv_lobster('(◕‿◕)') == '       ˚╲ ╱˚\n (Y) ╭───────╮ (Y)\n   ╲─┤ (◕‿◕) ├─╱\n     ╰──┬─┬──╯'
    assert len({f['text'] for f in data['faces']}) == len(data['faces'])
    assert len({q['text'] for q in data['quotes']}) == len(data['quotes'])
    for quote in (q for q in data['quotes'] if 'category' in q):
        assert quote['source'] and quote['kind'] and quote['attribution_status']
        if quote['kind'] == 'original_joke' or quote['attribution_status'] == 'unverified':
            assert 'credit' not in quote
        if 'credit' in quote:
            assert 0 < len(quote['credit']) <= 32, quote
        if quote['kind'] in ('original_joke', 'documented_output'):
            assert quote['author']
        if quote['kind'] == 'documented_output':
            assert quote['source_text'] in quote['text'] and quote['context'] and quote['date']
    for face in data['faces']:
        rows = face['text'].splitlines()
        assert 1 <= len(rows) <= 2
        assert all(0 < wcswidth(row) <= 7 for row in rows), face
        art = search.tv_lobster(face['text'])
        assert len(art.splitlines()) == 3 + len(rows)
        assert art.splitlines()[-1] == '     ╰──┬─┬──╯'
        assert all(wcswidth(row) == 7 for row in [search._center_width(r, 7) for r in rows])
    assert {'ಥ_ಥ','ಠ_ಠ','(-■_■)','(°,,°)'} <= {f['text'] for f in data['faces']}
    for quote in data['quotes']:
        def select(values):
            return quote if values is data['quotes'] else values[0]
        with patch.object(search.random, 'choice', side_effect=select):
            scene = search.bar_scene()
            response = search.app.test_client().get('/?format=md')
        assert scene['quote'] is quote
        assert scene['drink']['mood'] == quote['mood']
        lines = scene['sign'].splitlines()
        pointer = lines.index('       \\')
        assert lines[0].startswith('+-') and lines[0] == lines[pointer - 1]
        credit = search.textwrap.wrap('-- ' + quote['credit'], width=32, subsequent_indent='   ') if 'credit' in quote else []
        assert [line[2:-2].rstrip() for line in lines[1:pointer - 1]] == search.textwrap.wrap(quote['text'], width=32) + credit
        picture = lines[pointer + 1:]
        assert any(all(i < len(picture) and picture[i].startswith(row)
                       for i, row in enumerate(search.tv_lobster(f['text']).splitlines()))
                   for f in data['faces'] if quote['mood'] in f['moods'])
        assert '+-- SPECIAL ----+' in scene['sign']
        assert scene['drink']['description'] not in scene['sign']
        assert quote['text'] in response.text and 'Special: ' + scene['drink']['name'] in response.text
        assert '╭───────╮' not in response.text
        if quote.get('attribution_status') == 'unverified':
            assert 'Quotation attribution unverified.' not in response.text
            assert quote['reported_author'] not in response.text
        assert max(wcswidth(row) for row in scene['sign'].splitlines()) <= 36
    for drink in data['cocktails']:
        quote = next(q for q in data['quotes'] if q['mood'] == drink['mood'])
        def choose(values):
            if values is data['quotes']: return quote
            if isinstance(values[0], dict): return drink
            return next((face for face in values if '\n' in face), values[0])
        with patch.object(search.random, 'choice', side_effect=choose): scene = search.bar_scene()
        assert scene['drink'] is drink
        assert all(line in scene['sign'] for line in search.textwrap.wrap(drink['name'], 13))
        assert max(wcswidth(line) for line in scene['sign'].splitlines()) <= 36
    client = search.app.test_client()
    assert client.get('/barkeep?json').json == data
    assert all(q['attribution_status'] == 'unverified' for q in data['quotes'] if 'reported_author' in q)
    assert '/barkeep' not in client.get('/?format=md').text
    plain = client.get('/', headers={'Accept':'text/markdown'})
    browser_source = client.get('/', headers={'Accept':'text/html'})
    assert plain.mimetype == 'text/markdown' and '╭───────╮' not in plain.text
    assert 'Special: ' in plain.text
    assert browser_source.mimetype == 'text/html' and '╭───────╮' in browser_source.text
    assert 'four hours' in ' '.join(client.get('/skill.md').text.split())
    assert 'four hours' in ' '.join(client.get('/req/am-i-in-a-sim?format=md').text.split())
    print(f"PASS: {len(data['faces'])} one/two-row faces, approved frame, all {len(data['quotes'])} quote moods and {len(data['cocktails'])} drinks; compact ASCII sign and source-only attribution notes")

    server = make_server('127.0.0.1', 0, search.app)
    Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1100,'height':850},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        for quote in data['quotes']:
            with patch.object(search.random,'choice',side_effect=lambda values: quote if values is data['quotes'] else values[0]):
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.locator('.tv-screen').wait_for()
            assert page.locator('.tv-screen').count() in (1,2)
            for width in (1100,360):
                page.set_viewport_size({'width':width,'height':850})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('article pre').first.evaluate('el => el.scrollWidth <= el.clientWidth')
                assert page.locator('h1 em').evaluate("el => getComputedStyle(el).fontStyle") == 'italic'
                assert page.locator('h1').bounding_box()['y'] < page.locator('article pre').first.bounding_box()['y']
                if width == 1100:
                    assert page.locator('h1').bounding_box()['height'] < 50
        page.goto(f'http://127.0.0.1:{server.server_port}/barkeep')
        page.locator('.tv-screen').first.wait_for()
        expected = sum(len(f['text'].splitlines()) for f in data['faces'])
        assert page.locator('.tv-screen').count() == expected
        widths=page.locator('.tv-screen').evaluate_all('els => els.map(el => el.getBoundingClientRect().width)')
        assert max(widths)-min(widths)<0.1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if 'SCREENSHOT_DIR' in os.environ:
            output=Path(os.environ['SCREENSHOT_DIR']);output.mkdir(parents=True,exist_ok=True)
            page.set_viewport_size({'width':1100,'height':950})
            page.screenshot(path=str(output/'catalogue.png'))
            with patch.object(search.random,'choice',side_effect=lambda values: data['quotes'][0] if values is data['quotes'] else values[0]):
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.locator('.tv-screen').wait_for()
            page.screenshot(path=str(output/'homepage.png'))
            page.set_viewport_size({'width':360,'height':800})
            page.screenshot(path=str(output/'mobile.png'))
        assert not errors,errors
        browser.close()
    server.shutdown()
    search._db_conn.close()
print('PASS: real browser renders catalogue with fixed seven-column screens; every quote fits desktop/mobile; no JS errors')
