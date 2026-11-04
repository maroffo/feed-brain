{#- ABOUTME: One issue (any edition) as an Obsidian note (frontmatter + Markdown), synced to the vault Digests folder. -#}
{#- ABOUTME: Mirrors bulletin.html content; autoescape is off for this template. -#}
---
date: {{ data.issue_date.isoformat() }}
type: bulletin
edition: {{ data.edition }}
source: feed-brain
articles: {{ data.articles_count }}
---

# {{ data.title }}

{% if data.subtitle %}*{{ data.subtitle }}*

{% endif %}
{% if data.key_points %}## Il punto

{% for p in data.key_points %}- {{ p }}
{% endfor %}

{% elif data.editorial %}## Il punto

{{ data.editorial }}

{% endif %}
{% if data.top_stories %}## Le storie del giorno

{% for story in data.top_stories %}### {{ story.title }}

*{{ section_titles[story.section] }}*

{{ story.text }}

{% if story.analysis and story.analysis.money_quote %}> {{ story.analysis.money_quote }}

{% endif %}
{% for s in story.sources %}- {{ s.name }}: {{ s.title | md_link(s.url) }}
{% endfor %}
{% if story.analysis and story.analysis.insights %}
**Approfondimento:**
{% for i in story.analysis.insights %}- {{ i }}
{% endfor %}
{% endif %}
{% if story.analysis and story.analysis.actionables %}
**Da fare:**
{% for a in story.analysis.actionables %}- {{ a }}
{% endfor %}
{% endif %}

{% endfor %}
{% endif %}
{% for block in data.sections %}## {{ block.title }}

{% for item in block.items %}- **{{ item.headline | md_link(item.sources[0].url) }}** {{ item.summary }}{% for s in item.sources[1:] %} · {{ s.name | md_link(s.url) }}{% endfor +%}
{% endfor %}
{% if block.omitted %}- *{{ block.omitted | folded }}*
{% endif %}

{% endfor %}
