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
| 6 | [claim-submission](skills/claim-submission/SKILL.md) | Guide (never perform) the submission process after validating VA.gov, forms, and 38 CFR sources live |

## Plugin

`plugin.json` and `.cursor-plugin/plugin.json` describe the plugin (skills in `./skills/`, agents in `./agents/`). The [va-claims agent](agents/va-claims.md) runs the pipeline. More in [docs/README.md](docs/README.md).

## CI

`.github/workflows/ci.yml` runs on every push, pull request, and manual dispatch. Its steps:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` (Python 3.12)
3. **Check every claim row has a CFR cite and a record cite:** `python3 tests/check_citations.py`. Validates `tests/fixtures/claims-fake.json` against the fictional `tests/fake-records/` (quotes must appear on the cited page; related rows must be "needs more documentation") and confirms `claims-bad.json` is rejected.
4. **Validate plugin.json files parse and match:** `python3 tests/check_plugin_json.py`
5. **Check claim-submission track step lists:** `python3 tests/check_submission_tracks.py`. Tracks A, B1, B2 present; step 1 validates sources; every step cites a VA.gov URL or 38 CFR section; no step submits, signs, or contacts VA; `submission-bad.json` is rejected.
6. **Skill frontmatter present:** every `skills/*/SKILL.md` starts with frontmatter containing `name:` and `description:`.
7. **claim-submission guardrails present:** the skill contains "never submits", "Step 0: Ask which track", "Track B2", and "§3.155".
