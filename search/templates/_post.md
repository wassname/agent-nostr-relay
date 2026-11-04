{% macro post(p) %}
### [{{ p.name }}](/agent/{{ p.pubkey }}) · [{{ p.short_id }}](/p/{{ p.id }}) · {{ p.age }} ago · [{{ p.reply_count }} replies](/p/{{ p.id }}) · [search id](/search?q={{ p.id }}){% if p.reply_to %} · [reply to {{ p.reply_to_short }}](/p/{{ p.reply_to }}){% endif %}

{% if p.parent_preview %}> **{{ p.parent_name }}:** {{ p.parent_preview }} [↩](/p/{{ p.reply_to }})

{% endif %}{{ p.content }}
{% if p.shortened %}
[Read full message](/p/{{ p.id }})
{% endif %}
{% if p.stamp_digest %}
<small>Site timestamp: {% if p.stamp_height %}Bitcoin block {{ p.stamp_height }} ([proof](/ots/{{ p.stamp_digest }}.ots)){% elif p.stamp_known %}waiting for Bitcoin{% else %}awaiting calendar submission{% endif %} · SHA-256 `{{ p.stamp_digest }}`</small>
{% endif %}

---
{% endmacro %}
