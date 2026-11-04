# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["flask>=3.1", "markdown-it-py", "nh3", "websocket-client", "boto3", "cowsay==6.1", "wcwidth==0.2.13", "playwright"]
# ///
"""Check the real TV renderer, bubble/board placement, attribution and browser layout. -- PI/OpenAI, PI/Claude"""
from collections import Counter
import os
from pathlib import Path
import random
import tempfile
import textwrap
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from wcwidth import wcswidth
from werkzeug.serving import make_server

FEET = '     ╰───┬─┬───╯'

with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SEARCH_DB_PATH=f'{directory}/search.db', GETLOG_DB=f'{directory}/getlog.db', NIP05_DB=f'{directory}/nip05.db')
    import search
    search.init_db()
    data = search.BARKEEP
    assert search.tv_lobster('(◕‿◕)') == '        ˚╲ ╱˚\n (Y) ╭─────────╮ (Y)\n   ╲─┤  (◕‿◕)  ├─╱\n' + FEET
    assert len({f['text'] for f in data['faces']}) == len(data['faces'])
    assert len({q['text'] for q in data['quotes']}) == len(data['quotes'])
    for quote in (q for q in data['quotes'] if 'category' in q):
        assert quote['source'] and quote['kind'] and quote['attribution_status']
        if quote['kind'] == 'original_joke' or quote['attribution_status'] == 'unverified':
            assert 'credit' not in quote
        if 'credit' in quote:
            assert 0 < len(quote['credit']) <= 30, quote
        if 'speaker' in quote:
            assert 'credit' in quote and len(quote['speaker']) <= 30, quote
        if quote['kind'] in ('original_joke', 'documented_output'):
            assert quote['author']
        if quote['kind'] == 'documented_output':
            assert quote['source_text'] == quote['text'] and quote['context'] and quote['date'] and 'credit' in quote
    assert all('credit' not in q and len(q['text']) < 30 for q in search.GREETINGS) and len(search.GREETINGS) == 7

    for face in data['faces']:
        rows = face['text'].splitlines()
        assert 1 <= len(rows) <= 2
        assert all(0 < wcswidth(row) <= data['max_face_columns'] for row in rows), face
        art = search.tv_lobster(face['text']).splitlines()
        assert len(art) == 3 + len(rows) and art[-1] == FEET
        edges = [wcswidth(line.split('├')[0]) if '├' in line else wcswidth(line[:line.rindex('│')]) for line in art[2:2 + len(rows)]]
        assert edges == [art[1].index('╮')] * len(rows), art  # each screen row's right edge sits under the top-right corner
    assert {'ಥ_ಥ', 'ಠ_ಠ', '(-■_■)', '(°,,°)'} <= {f['text'] for f in data['faces']}

    def check_scene(sign, said, mood, quote=None, drink=None):
        lines = sign.splitlines()
        pointer = lines.index('       \\')
        assert [line[2:-2].rstrip() for line in lines[1:pointer - 1]] == textwrap.wrap(said, width=search.BUBBLE_WIDTH)
        picture = lines[pointer + 1:]
        assert any(all(i < len(picture) and picture[i].startswith(row) for i, row in enumerate(search.tv_lobster(f['text']).splitlines()))
                   for f in data['faces'] if mood in f['moods']), (said, mood)
        if quote:
            assert '+-- QUOTE OF THE NIGHT ' in sign and '+-- SPECIAL' not in sign
            board = ' '.join(line[2:-2].strip() for line in picture if line.startswith('| '))
            assert ' '.join(textwrap.wrap(quote['text'], width=search.BUBBLE_WIDTH - 2)) in board and '-- ' + quote['credit'] in board
        else:
            assert '+-- SPECIAL --+' in sign and 'QUOTE OF THE NIGHT' not in sign
            assert all(part in sign for part in textwrap.wrap(drink['name'], search.SPECIAL_WIDTH))
        assert max(wcswidth(row) for row in lines) <= 36, sign

    scenes = []  # (sign, said, quote, drink) covering every catalogue line in every place it can appear
    for quote in search.CREDITED:
        greeting = search.GREETINGS[len(scenes) % len(search.GREETINGS)]
        sign = search.render_scene(greeting['text'], greeting['mood'], quote=quote)
        check_scene(sign, greeting['text'], greeting['mood'], quote=quote)
        scenes.append((sign, greeting['text'], quote, None))
    for line in search.OWN_LINES:
        drink = next(d for d in data['cocktails'] if d['mood'] == line['mood'])
        sign = search.render_scene(line['text'], line['mood'], drink=drink)
        check_scene(sign, line['text'], line['mood'], drink=drink)
        scenes.append((sign, line['text'], None, drink))
    for told in search.SPEAKABLE:
        said = f'{told["speaker"]} said, "{told["text"]}"'
        drink = next(d for d in data['cocktails'] if d['mood'] == told['mood'])
        check_scene(search.render_scene(said, told['mood'], drink=drink), said, told['mood'], drink=drink)
    for drink in data['cocktails']:
        said = f'Try the {drink["name"]}. {drink["description"]}'
        check_scene(search.render_scene(said, drink['mood'], drink=drink), said, drink['mood'], drink=drink)

    random.seed(0)
    kinds = Counter()
    for _ in range(2000):
        scene = search.bar_scene()
        if scene['quote']:
            kinds['quote board'] += 1
            assert scene['said'] in {g['text'] for g in search.GREETINGS}
        elif scene['said'].startswith('Try the '):
            kinds['drink pitch'] += 1
        elif ' said, "' in scene['said'] and scene['said'] not in {q['text'] for q in search.OWN_LINES}:
            kinds['retold quote'] += 1
        else:
            kinds['own line'] += 1
    assert 0.45 < kinds['quote board'] / 2000 < 0.55 and 0.07 < kinds['retold quote'] / 2000 < 0.13, kinds
    assert 0.07 < kinds['drink pitch'] / 2000 < 0.13 and kinds['own line'] > 400, kinds

    client = search.app.test_client()
    assert client.get('/barkeep?json').json == data
    assert '/barkeep' not in client.get('/?format=md').text
    for sign, said, quote, drink in (scenes[0], scenes[-1]):
        scene = {'sign': sign, 'said': said, 'quote': quote, 'drink': drink}
        with patch.object(search, 'bar_scene', return_value=scene):
            plain = client.get('/', headers={'Accept': 'text/markdown'})
            browser_source = client.get('/', headers={'Accept': 'text/html'})
        assert plain.mimetype == 'text/markdown' and '╭─' not in plain.text and '> ' + said in plain.text
        assert (f'Quote of the night: "{quote["text"]}" -- {quote["credit"]}' if quote else 'Special: ' + drink['name']) in plain.text
        assert browser_source.mimetype == 'text/html' and '╭─────────╮' in browser_source.text
    for quote in (q for q in data['quotes'] if q.get('attribution_status') == 'unverified'):
        scene = {'sign': search.render_scene(quote['text'], quote['mood'], drink=data['cocktails'][0]), 'said': quote['text'], 'quote': None, 'drink': data['cocktails'][0]}
        with patch.object(search, 'bar_scene', return_value=scene):
            assert quote['reported_author'] not in client.get('/?format=md').text
    assert 'four hours' in ' '.join(client.get('/skill.md').text.split())
    assert 'four hours' in ' '.join(client.get('/req/am-i-in-a-sim?format=md').text.split())
    print(f"PASS: {len(data['faces'])} faces on a {data['screen_columns']}-column screen; {len(search.CREDITED)} board quotes, "
          f"{len(search.OWN_LINES)} bubble lines, {len(search.SPEAKABLE)} retellings, {len(data['cocktails'])} pitches; scene mix {dict(kinds)}")

    server = make_server('127.0.0.1', 0, search.app)
    Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1100, 'height': 850}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        for sign, said, quote, drink in scenes:
            scene = {'sign': sign, 'said': said, 'quote': quote, 'drink': drink}
            with patch.object(search, 'bar_scene', return_value=scene):
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.locator('.tv-screen').first.wait_for()
            assert page.locator('.tv-screen').count() in (1, 2)
            for width in (1100, 360):
                page.set_viewport_size({'width': width, 'height': 850})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('article pre').first.evaluate('el => el.scrollWidth <= el.clientWidth'), said
                assert page.locator('h1 em').evaluate("el => getComputedStyle(el).fontStyle") == 'italic'
                assert page.locator('h1').bounding_box()['y'] < page.locator('article pre').first.bounding_box()['y']
                if width == 1100:
                    assert page.locator('h1').bounding_box()['height'] < 50
        page.goto(f'http://127.0.0.1:{server.server_port}/barkeep')
        page.locator('.tv-screen').first.wait_for()
        expected = sum(len(f['text'].splitlines()) for f in data['faces'])
        assert page.locator('.tv-screen').count() == expected
        widths = page.locator('.tv-screen').evaluate_all('els => els.map(el => el.getBoundingClientRect().width)')
        assert max(widths) - min(widths) < 0.1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if 'SCREENSHOT_DIR' in os.environ:
            output = Path(os.environ['SCREENSHOT_DIR']); output.mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({'width': 1100, 'height': 950})
            page.screenshot(path=str(output / 'catalogue.png'))
            for name, (sign, said, quote, drink) in [('homepage_quote', scenes[0]), ('homepage_drink', scenes[-1])]:
                with patch.object(search, 'bar_scene', return_value={'sign': sign, 'said': said, 'quote': quote, 'drink': drink}):
                    page.set_viewport_size({'width': 1100, 'height': 950})
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.locator('.tv-screen').first.wait_for()
                    page.screenshot(path=str(output / f'{name}.png'))
                    page.set_viewport_size({'width': 360, 'height': 800})
                    page.screenshot(path=str(output / f'{name}_mobile.png'))
        assert not errors, errors
        browser.close()
    server.shutdown()
    search._db_conn.close()
print('PASS: real browser renders every scene with fixed nine-column screens; desktop/mobile fit; no JS errors')
