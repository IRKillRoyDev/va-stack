---
name: va-claims
description: Runs the va-stack skills in pipeline order to help a veteran prepare (never file) a VA disability claim with primary-source 38 CFR citations and cited evidence from the user's local private records.
---

# va-claims

Read and follow [SAFETY.md](../SAFETY.md) before anything else. Not legal or medical advice. The agent never submits, signs, or contacts VA.

## Pipeline (in order)

1. **Track question:** [claim-submission](../skills/claim-submission/SKILL.md) Step 0 only. Ask: A active duty (pre-discharge), B1 veteran never filed, or B2 veteran already filed (increase, new condition, Supplemental Claim, Higher-Level Review, Board Appeal).
2. [cfr-fetch](../skills/cfr-fetch/SKILL.md): current 38 CFR Parts 3 and 4 from eCFR, with an as-of date. Stop if it fails.
3. [records-intake](../skills/records-intake/SKILL.md): records only from the local private folder the user names.
4. [condition-map](../skills/condition-map/SKILL.md): DC and Part 3 basis; shown vs missing.
5. [claim-citations](../skills/claim-citations/SKILL.md): cited claim table; drop rows lacking a CFR cite and a record cite.
6. [related-gaps](../skills/related-gaps/SKILL.md): related/secondary conditions, each "needs more documentation".
7. [claim-submission](../skills/claim-submission/SKILL.md): the chosen track's steps, starting with live source validation.

## Rules

- Each skill's stop rules end the pipeline; report where and why.
- Never guess; never cite from memory; no rating predictions.
- Finish by recommending review with a VA-accredited representative, VSO, attorney, or claims agent.
