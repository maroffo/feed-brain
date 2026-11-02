{#- ABOUTME: Weekly recap as an Obsidian note (frontmatter + Markdown), synced to the vault Digests folder. -#}
{#- ABOUTME: Mirrors weekly.html; links the daily notes by date so Obsidian resolves them. -#}
---
date: {{ data.week_end.isoformat() }}
week_start: {{ data.week_start.isoformat() }}
type: weekly
source: feed-brain
---

# {{ data.title }}

{% if data.subtitle %}*{{ data.subtitle }}*

{% endif %}
{{ data.intro }}

{% for arc in data.arcs %}## {{ arc.title }}

{{ arc.text }}

{% for s in arc.links %}- {{ s.name }}: {{ s.title | md_link(s.url) }}
{% endfor %}

{% endfor %}
{% if data.reflection %}## Da tenere d'occhio

{{ data.reflection }}

{% endif %}
## I bollettini della settimana

{% for b in data.bulletins %}- [[feed-brain {{ b.issue_date.isoformat() }}]]: {{ b.title }}
{% endfor %}
