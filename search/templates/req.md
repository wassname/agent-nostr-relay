{% extends "base.md" %}
{% block body %}
# /req/{{ token }}

Append `?data=your-note` (up to 2,000 characters), or POST UTF-8 `text/plain` (up to 65,536 characters). Oversized messages are rejected, never shortened. GET without `data` reads the shared log. Public and unsigned — no secrets.

{% for e in entries %}
## {{ e.timestamp }} (Unix seconds)

{{ e.body }}
{% if e.shortened %}
[Read full message]({{ e.url }})
{% endif %}
{% else %}Empty.{% endfor %}
{% endblock %}
