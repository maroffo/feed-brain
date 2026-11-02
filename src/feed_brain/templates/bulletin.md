{#- ABOUTME: Daily bulletin as an Obsidian note (frontmatter + Markdown), synced to the vault Digests folder. -#}
{#- ABOUTME: Mirrors bulletin.html content; autoescape is off for this template. -#}
---
date: {{ data.issue_date.isoformat() }}
type: bulletin
source: feed-brain
articles: {{ data.articles_count }}
---

# {{ data.title }}

{% if data.subtitle %}*{{ data.subtitle }}*

{% endif %}
{% if data.editorial %}## Il punto

{{ data.editorial }}

{% endif %}
{% if data.top_stories %}## Le storie del giorno

{% for story in data.top_stories %}### {{ story.title }}

*{{ section_titles[story.section] }}*

{{ story.text }}

{% for s in story.sources %}- {{ s.name }}: {{ s.title | md_link(s.url) }}
{% endfor %}
{% if story.analysis %}
**Approfondimento.** {{ story.analysis.summary }}

{% for i in story.analysis.insights %}- {{ i }}
{% endfor %}
{% if story.analysis.money_quote %}
> {{ story.analysis.money_quote }}
{% endif %}
{% if story.analysis.actionables %}
**Da fare:**
{% for a in story.analysis.actionables %}- {{ a }}
{% endfor %}
{% endif %}
{% endif %}

{% endfor %}
{% endif %}
{% for block in data.sections %}## {{ block.title }}

{% for item in block.items %}- **{{ item.headline | md_link(item.sources[0].url) }}** {{ item.summary }}{% for s in item.sources[1:] %} · {{ s.name | md_link(s.url) }}{% endfor %}
{% endfor %}
{% if block.omitted %}- *e altre {{ block.omitted }} storie*
{% endif %}

{% endfor %}
{% if data.radar %}## Radar minacce

{% for r in data.radar %}- {{ r.title | md_link(r.url) }} ({{ r.source }})
{% endfor %}
{% endif %}
