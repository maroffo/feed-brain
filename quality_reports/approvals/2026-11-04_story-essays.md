# ABOUTME: Approval record for feat/story-essays: Opus essay per top story on a private signed page.
# ABOUTME: Redacted summary of the agy review rounds; findings detail stays in the gitignored reviews dir.

| Field | Value |
|-------|-------|
| Branch, commit | feat/story-essays @ f2506fd (on top of feat/bulletin-polish a364992) |
| Rounds run | 2 (agy) |
| Counts by severity | Critical 0 / Major 0 / Minor 3, all fixed in round 2 |
| CWE ids | CWE-77 (prompt delimiter injection from untrusted source text), CWE-770 (unbounded concurrency) |
| Final SCORE | SCORE: 100/100 (threshold: 90, gate: pr) |
| Residual risks | Essay links never expire (deliberate: read-only; a leaked link exposes one essay). Cost and runtime grow by 5 Opus calls a day (one real run: 17.5k input tokens, ~50 s); watch `opus_call` logs. Paywalled sources (NYT 403, Politico teaser) go in as summaries; the page footer says so. |
| Findings path | quality_reports/reviews/2026-11-04_story-essays/ (gitignored) |
