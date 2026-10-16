# va-stack

Skills for a bot that helps veterans **prepare** (not file) VA disability claims using primary-source 38 CFR from eCFR.

Read [SAFETY.md](SAFETY.md) first. Not legal or medical advice.

## Pipeline

| # | Skill | Purpose |
|---|-------|---------|
| 1 | [cfr-fetch](skills/cfr-fetch/SKILL.md) | Pull current 38 CFR Parts 3 and 4 from the eCFR API, record the "as of" date |
| 2 | [records-intake](skills/records-intake/SKILL.md) | Index the user's local private records by file, page, date |
| 3 | [condition-map](skills/condition-map/SKILL.md) | Map diagnoses to Part 4 diagnostic codes and Part 3 bases; name what is shown vs missing |
| 4 | [claim-citations](skills/claim-citations/SKILL.md) | Build the cited claim table; drop rows lacking a CFR cite AND a record cite |
| 5 | [related-gaps](skills/related-gaps/SKILL.md) | List related/secondary conditions as "needs more documentation" |

## Tests

`python3 tests/check_citations.py` validates the claim table against the fictional record set in `tests/fake-records/`. CI runs it on every push.
