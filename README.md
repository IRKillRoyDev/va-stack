# va-stack

Skills for a bot that helps veterans **prepare** (not file) VA disability claims using primary-source 38 CFR from eCFR.

Read [SAFETY.md](SAFETY.md) first. Not legal or medical advice.

## Pipeline

| # | Skill | Purpose |
|---|-------|---------|
| 1 | [cfr-fetch](skills/cfr-fetch/SKILL.md) | Pull current 38 CFR Parts 3, 4, 14 and 20 from the eCFR API, record the "as of" date |
| 2 | [local-search](skills/local-search/SKILL.md) | Run `scripts/local_search.py` on the user's computer (no network, in memory) for capped, redacted snippets; the user approves them before anything goes to the AI service |
| 2b | [records-vault](skills/records-vault/SKILL.md) | Optional: the same search through an encrypted SQLCipher vault on the user's computer, with a consent screen every unlock, bot id on every request, and an HMAC-chained access log |
| 3 | [records-intake](skills/records-intake/SKILL.md) | Index the user's local private records by file, page, date |
| 4 | [condition-map](skills/condition-map/SKILL.md) | Map diagnoses to Part 4 diagnostic codes and Part 3 bases; name what is shown vs missing |
| 5 | [claim-citations](skills/claim-citations/SKILL.md) | Build the cited claim table; drop rows lacking a CFR cite AND a record cite |
| 6 | [related-gaps](skills/related-gaps/SKILL.md) | List related/secondary conditions as "needs more documentation" |
| 6b | [proposed-issues](skills/proposed-issues/SKILL.md) | Full cited report plus a form-mapped proposed-issues list (verified VA form columns), unofficial estimates with gap analysis, and the §4.25 combined estimate |
| 7 | [claim-submission](skills/claim-submission/SKILL.md) | Guide (never perform) the submission process after validating VA.gov, forms, and 38 CFR sources live |

## Plugin

`plugin.json` and `.cursor-plugin/plugin.json` describe the plugin (skills in `./skills/`, agents in `./agents/`). The [va-claims agent](agents/va-claims.md) runs the pipeline. More in [docs/README.md](docs/README.md).

## CI

`.github/workflows/ci.yml` runs on every push, pull request, and manual dispatch. Its steps:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` (Python 3.12)
3. **Check every claim row has a CFR cite and a record cite:** `python3 tests/check_citations.py`. Validates `tests/fixtures/claims-fake.json` against the fictional `tests/fake-records/`: each CFR cite must be a section in the pinned eCFR list `tests/fixtures/ecfr-sections-2026-10-14.json` (Parts 3, 4, 14, 20), each quote must appear on the cited page, and related rows must be "needs more documentation". Confirms `claims-bad.json` and `claims-bad-fake-section.json` (a made-up §3.999) are rejected.
4. **Validate plugin.json files parse and match:** `python3 tests/check_plugin_json.py`
5. **Check claim-submission track step lists:** `python3 tests/check_submission_tracks.py`. Tracks A1, A2, B1, B2, C present; step 1 validates sources; every step cites a VA.gov URL, a govinfo 10 U.S.C. URL, DoDI 1332.18, or a real 38 CFR section; no step has the bot submit, file, e-file, sign, send, transmit, upload, fax, mail, call, or contact VA. Every step of `submission-bad.json` and `submission-bad-phrasings.json` must be rejected.
6. **Skill frontmatter present:** every `skills/*/SKILL.md` starts with frontmatter containing `name:` and `description:`.
7. **claim-submission guardrails present:** the skill contains "never submits", "Step 0: Ask which track", "Track B2", "Track C", "Track A2", "PEBLO", "§20.202", and "§3.155".
8. **SAFETY.md disclaimer present:** SAFETY.md exists and contains "Not legal or medical advice", "No filing", "VA-accredited", the AI-service processing note, and the Veterans Crisis Line; no Markdown file makes a privacy, compliance, or retention overclaim (phrases listed in `ci.yml`).
9. **Estimate notice present:** SAFETY.md, the proposed-issues skill, and the agent each contain the unofficial-estimate notice, and the skill names the insufficient-evidence output.
10. **Lint and tests** (job `python`): `pip install -r requirements-dev.txt` (pinned pypdf, pytest, ruff, flake8), then `ruff check .`, `ruff format --check .`, `flake8 .`, and `pytest -q`. `tests/test_local_search.py` checks redaction of every identifier kind, the per-term and total caps, `--dry-run` counts-only output, in-memory PDF extraction, and that no socket is opened (socket creation is patched to raise). `tests/test_hardening.py` holds the red-team regression tests (redaction before windowing, SSN separator and invisible-character variants, old dates, addresses, phones, redacted terms and file names, line-split identifiers, and the SAFETY/CI wording checks).

## Local search script

```
pip install -r requirements.txt
python3 scripts/local_search.py "/path/to/records" -t "tinnitus" -t "lumbar" --dry-run
python3 scripts/local_search.py "/path/to/records" -t "tinnitus" --max-hits-per-term 20 --max-total 100 --context 80
python3 scripts/local_search.py "/path/to/records" -t "separation" --keep-dates  # keep exact old dates
```

Runs on the user's own computer, makes no network calls, and writes nothing to disk. Output is redacted JSON for the user to review before sharing.

## Encrypted vault (optional)

```
pip install -r requirements.txt
python3 scripts/records_vault.py init records.db
python3 scripts/records_vault.py ingest records.db "/path/to/records" --bot-id <id>
python3 scripts/records_vault.py search records.db --bot-id <id> -t "tinnitus"
python3 scripts/records_vault.py verify records.db --bot-id <id>
python3 scripts/vault_ui.py records.db --bot-id <id>
```

Each command shows the consent-to-share notice, then prompts for the password locally. `tests/test_vault.py` covers wrong password, tampered or deleted log entries, no consent, consent every session, missing bot id, no snippet text in the log, key zeroized after close, and no network use.
