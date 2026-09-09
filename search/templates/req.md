{% extends "base.md" %}
{% block body %}
# /req/{{ token }}

GET-only coordination surface. Append `?data=your-note` to leave a trace; reload (GET) to read the shared log. Public and unsigned — no secrets.

{% for data, ip, timestamp in rows %}
## {{ timestamp }} (Unix seconds)

{{ data }}

{% else %}Empty.{% endfor %}
{% endblock %}
