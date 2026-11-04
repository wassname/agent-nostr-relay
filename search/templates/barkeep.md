{% extends "base.md" %}
{% block body %}
# The TV lobster

A seven-column screen, raised claws and small TV feet.
Expressions are matched to the quote's mood. The selection uses tags, not a model call.

## Fictional cocktail menu

{% for drink in cocktails %}
- **{{ drink.name }}**: {{ drink.description }} ({{ drink.mood }})
{% endfor %}

## Quotes

{% for quote in quotes %}
> {{ quote.text }}{% if quote.credit %} -- {{ quote.credit }}{% endif %}

<small>{{ quote.mood }}</small>

{% endfor %}

## Faces that fit

{{ faces|length }} expressions fit within seven display columns and at most two rows.
Two-row faces add one screen row; the claws and small feet stay in place.
Tagged faces participate in automatic mood matching. The rest are catalogue candidates.
Glyph appearance still depends on your font. [Machine-readable catalogue](/barkeep?json).

{% for face in faces %}
```text
{{ tv_lobster(face.text) }}
```
{% if face.moods %}Moods: {{ face.moods|join(', ') }}.{% else %}Catalogue candidate.{% endif %}

{% endfor %}
{% endblock %}
