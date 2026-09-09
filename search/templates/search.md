{% extends "base.md" %}
{% from "_post.md" import post %}
{% block body %}
# Search

Search with `/search?q=your-query`.

{% if q %}Query: {{ q }}

{{ results|length }} results

{% for p in results %}{{ post(p) }}{% endfor %}{% endif %}
{% endblock %}
