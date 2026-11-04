{% extends "base.md" %}
{% block body %}
# The TV lobster

A seven-column screen, raised claws and small TV feet. Design: wassname + PI/OpenAI.
Expressions are matched to the quote's mood. The selection uses tags, not a model call.

## Fictional cocktail menu

Original names and descriptions: PI/OpenAI. These are jokes, not drink recipes.

{% for drink in cocktails %}
- **{{ drink.name }}**: {{ drink.description }} ({{ drink.mood }})
{% endfor %}

## Quotes

The supplied quotation-list attributions have not been independently verified.
Original bar jokes are credited to PI/OpenAI. The original seven greetings are kept.

{% for quote in quotes %}
> {{ quote.text }}

{% if quote.attribution_status == 'unverified' %}Reported attribution: {{ quote.reported_author }} (unverified).{% elif quote.author %}{{ quote.author }}.{% endif %} Mood: {{ quote.mood }}.

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
{% for source in face.sources %}{{ source.label }} · {{ source.source }}{% if source.adapted %} (adapted{% if source.original %} from `{{ source.original }}`{% endif %}){% endif %}
{% endfor %}

{% endfor %}
{% endblock %}
