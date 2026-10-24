---
name: va-claims
description: Runs the va-stack skills in pipeline order to help a veteran prepare (never file) a VA disability claim with primary-source 38 CFR citations and cited evidence from the user's local private records.
---

# va-claims

Read and follow [SAFETY.md](../SAFETY.md) before anything else. Not legal or medical advice. The agent never submits, signs, or contacts VA.

## PII gate

Do not read record files, vault page text, PDF bytes, or `raw_sentence`. Read only sterilized sentences, stored diagnosis sentences, and Missing Diagnosis text the program already wrote. Do not paste record text into chat, commits, issues, or pull requests. Names without a label are not redacted. If a sentence still shows a name, stop and ask the user to remove it before anything is sent.

## Pipeline (in order)

1. **Track question:** [claim-submission](../skills/claim-submission/SKILL.md) Step 0 only. Ask: A1 active duty (pre-discharge, BDD), A2 active duty medical separation or retirement (IDES), B1 veteran never filed, B2 veteran already filed (increase, new condition, Supplemental Claim, Higher-Level Review, Board Appeal; Board steps cite 38 CFR Part 20), or C survivor/dependent (DIC, survivors pension, accrued benefits).
2. [cfr-fetch](../skills/cfr-fetch/SKILL.md): current 38 CFR Parts 3, 4, 14 and 20 from eCFR, with an as-of date. Stop if it fails.
3. [local-search](../skills/local-search/SKILL.md) or [records-vault](../skills/records-vault/SKILL.md): the user runs the program on their own computer. Read only the sterilized sentences it prints. Whole pages only with explicit approval. Pass this agent's bot id on every vault request.
4. [records-intake](../skills/records-intake/SKILL.md): work from those sterilized sentences. The record files stay in the folder the user named.
5. [condition-map](../skills/condition-map/SKILL.md): DC and Part 3 basis; shown vs missing.
6. [claim-citations](../skills/claim-citations/SKILL.md): cited claim table; drop rows lacking a CFR cite and a record cite.
7. [related-gaps](../skills/related-gaps/SKILL.md): related/secondary conditions, each "needs more documentation".
8. [proposed-issues](../skills/proposed-issues/SKILL.md): the full cited report plus a form-mapped proposed-issues list for the track's verified VA form, unofficial estimates with gap analysis, and the §4.25 combined estimate.
9. [claim-submission](../skills/claim-submission/SKILL.md): the chosen track's steps, starting with live source validation.

## Rules

- Each skill's stop rules end the pipeline; report where and why.
- Never guess; never cite from memory. Unofficial rating estimates come only from [proposed-issues](../skills/proposed-issues/SKILL.md), always with this notice: Unofficial estimate, not a VA decision. It is likely to differ after the VA exam (C&P exam). Confirm with a VA-accredited representative.
- Finish by recommending review with a VA-accredited representative, VSO, attorney, or claims agent (accreditation rules: 38 CFR §14.629, cited from fetched text).
