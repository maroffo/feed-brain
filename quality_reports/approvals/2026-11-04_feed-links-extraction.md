# ABOUTME: Approval record for fix/feed-links-extraction: article links over enclosures, best-of extraction.
# ABOUTME: Redacted summary of the agy review rounds; findings detail stays in the gitignored reviews dir.

| Field | Value |
|-------|-------|
| Branch, commit | fix/feed-links-extraction @ 968853a |
| Rounds run | 2 (agy) |
| Counts by severity | Critical 0 / Major 1 / Minor 1, both fixed in round 2 |
| CWE ids | none |
| Final SCORE | SCORE: 100/100 (threshold: 90, gate: pr) |
| Residual risks | DDay.it articles already stored keep their image URLs; recent ones get fetched again once under the real URL (one-time duplicate). Visible-length heuristic treats content starting with '<' as HTML. |
| Findings path | quality_reports/reviews/2026-11-04_feed-links-extraction/ (gitignored) |
