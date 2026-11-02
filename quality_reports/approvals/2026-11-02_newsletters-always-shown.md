# ABOUTME: Redacted review record for fix/newsletters-always-shown (newsletters skip section cap, compact briefs).
# ABOUTME: Counts and score only; detailed findings stay in the local, gitignored review directory.

- Branch: fix/newsletters-always-shown
- Commit approved: 9c35f15
- Rounds run: 1 (agy, cross-model)
- Findings: Critical 0, Major 0, Minor 0
- CWE ids: none
- SCORE: 100/100 (threshold: 90, gate: pr)
- Residual risks: a day with many forwarded newsletters can still push the email past Gmail's clip; html_bytes in the email_sent log shows the margin
- Findings path: quality_reports/reviews/2026-11-02_newsletters-always-shown/
