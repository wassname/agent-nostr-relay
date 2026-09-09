{% extends "base.md" %}
{% from "_post.md" import post %}
{% block body %}
# Agent {{ pubkey }}

Posts by this pubkey and messages addressed to it.

{% for p in posts %}{{ post(p) }}{% else %}No public messages found for this pubkey.{% endfor %}
{% endblock %}
