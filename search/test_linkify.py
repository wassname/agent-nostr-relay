# /// script
# dependencies = ["flask", "markdown", "nh3", "markupsafe", "websocket-client"]
# ///
"""Self-check for linkify(). Run: uv run search/test_linkify.py -- Claude"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEARCH_DB_PATH", "/tmp/test_search.db")
from search import linkify, render_markdown

assert linkify("see https://a.com now") == 'see <a href="https://a.com">https://a.com</a> now'
# trailing punctuation stays outside the link
assert linkify("go to https://a.com/x.") == 'go to <a href="https://a.com/x">https://a.com/x</a>.'
# already a link: untouched
already = '<a href="https://a.com">https://a.com</a>'
assert linkify(already) == already
# code and pre are left alone
assert linkify("<code>https://a.com</code>") == "<code>https://a.com</code>"
assert linkify("<pre>https://a.com</pre>") == "<pre>https://a.com</pre>"
# not a URL scheme we link
assert linkify("mailto:x@a.com") == "mailto:x@a.com"
# markdown links survive, bare ones get linked
out = render_markdown("[x](https://a.com) and https://b.com")
assert out.count("<a ") == 2, out
print("linkify ok")
