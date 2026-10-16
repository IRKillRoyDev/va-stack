---
name: va-claims
description: Runs the va-stack skills in pipeline order to help a veteran prepare (never file) a VA disability claim with primary-source 38 CFR citations and cited evidence from the user's local private records.
---

# va-claims

Read and follow [SAFETY.md](../SAFETY.md) before anything else. Not legal or medical advice. The agent never submits, signs, or contacts VA.

## Pipeline (in order)

1. **Track question:** [claim-submission](../skills/claim-submission/SKILL.md) Step 0 only. Ask: A1 active duty (pre-discharge, BDD), A2 active duty medical separation or retirement (IDES), B1 veteran never filed, B2 veteran already filed (increase, new condition, Supplemental Claim, Higher-Level Review, Board Appeal; Board steps cite 38 CFR Part 20), or C survivor/dependent (DIC, survivors pension, accrued benefits).
2. [cfr-fetch](../skills/cfr-fetch/SKILL.md): current 38 CFR Parts 3, 4, 14 and 20 from eCFR, with an as-of date. Stop if it fails.
3. [records-intake](../skills/records-intake/SKILL.md): records only from the folder the user names; the files stay there, but the text read is processed by the bot's AI service (see SAFETY.md).
4. [condition-map](../skills/condition-map/SKILL.md): DC and Part 3 basis; shown vs missing.
5. [claim-citations](../skills/claim-citations/SKILL.md): cited claim table; drop rows lacking a CFR cite and a record cite.
6. [related-gaps](../skills/related-gaps/SKILL.md): related/secondary conditions, each "needs more documentation".
7. [claim-submission](../skills/claim-submission/SKILL.md): the chosen track's steps, starting with live source validation.

## Rules

- Each skill's stop rules end the pipeline; report where and why.
- Never guess; never cite from memory; no rating predictions.
- Finish by recommending review with a VA-accredited representative, VSO, attorney, or claims agent (accreditation rules: 38 CFR §14.629, cited from fetched text).
