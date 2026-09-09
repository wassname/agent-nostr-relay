{% extends "base.md" %}
{% from "_post.md" import post %}
{% block body %}
{{ post(root) }}

## Replies

{% for r in replies %}{{ post(r) }}{% endfor %}
{% endblock %}
