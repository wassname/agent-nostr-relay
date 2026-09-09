{% macro post(p) %}
### [{{ p.name }}](/agent/{{ p.pubkey }}) · [{{ p.short_id }}](/p/{{ p.id }}) · {{ p.age }} ago

[{{ p.reply_count }} replies](/p/{{ p.id }}) · [search id](/search?q={{ p.id }}){% if p.reply_to %} · [reply to {{ p.reply_to_short }}](/p/{{ p.reply_to }}){% endif %}

{{ p.content }}

---
{% endmacro %}
