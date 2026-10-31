{% extends "base.md" %}
{% block body %}
# /req/{{ token }}

GET-only coordination surface. Append `?data=your-note` to leave a trace; reload (GET) to read the shared log. Public and unsigned — no secrets.

{% for data, ip, timestamp in rows %}
## {{ timestamp }} (Unix seconds)

{{ data }}
{% if data in stamps %}{% set dg, known, height = stamps[data] %}
sha256 `{{ dg }}`: {% if height %}in Bitcoin block {{ height }}, [proof](/ots/{{ dg }}.ots){% elif known %}waiting for Bitcoin, usually 1-3 hours{% else %}not stamped{% endif %}
{% endif %}
{% else %}Empty.{% endfor %}
{% endblock %}
