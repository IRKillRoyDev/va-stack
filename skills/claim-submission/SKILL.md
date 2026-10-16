---
name: claim-submission
description: Guide the user through submitting a VA disability claim on the right track (A1 active duty pre-discharge/BDD; A2 active duty medical separation or retirement through IDES; B1 veteran initial claim with intent to file; B2 veteran increase, new condition, supplemental claim, higher-level review, or Board appeal; C survivors and dependents: DIC, survivors pension, accrued benefits), after first validating every primary source live with an as-of date. The bot guides only; it never submits, signs, or contacts VA.
---

# claim-submission

**Guidance only.** The bot never submits, signs, uploads, or contacts VA or anyone on the user's behalf. The user (ideally with a VA-accredited representative, VSO, or attorney) does all filing. See SAFETY.md.

## Step 0: Ask which track

Ask the user, and do not assume:
- **A1. Active duty, transitioning out** (pre-discharge, e.g. Benefits Delivery at Discharge, BDD)
- **A2. Active duty, medical separation or retirement** through the Integrated Disability Evaluation System (IDES)
- **B1. Veteran, never filed**: initial claim with intent to file
- **B2. Veteran, already filed**: increased rating, new condition, supplemental claim, higher-level review, or Board appeal
- **C. Survivor or dependent** of a veteran: Dependency and Indemnity Compensation (DIC), survivors pension, accrued benefits

Then run the shared validation and the steps for that track only. Each track begins with source validation. Every step cites a source URL or a 38 CFR section fetched in this session.

## Shared validation rules (step 1 of every track)

- Fetch each listed URL live; require HTTP 200 and that the page still describes the item (form number, program, window). Record the as-of date (today for VA.gov; eCFR `up_to_date_as_of` for CFR via `cfr-fetch`).
- On a VA.gov "about form" page, read the form number and its "revision date".
- Quote the 38 CFR section headings and any text used.
- **Stop** if any source is unconfirmed, returns non-200, or conflicts with another source. Name the source that failed.

## Track A1: Active duty (pre-discharge, BDD)

1. **Validate sources:** `https://www.va.gov/disability/how-to-file-claim/when-to-file/pre-discharge-claim/` (BDD program and its current filing window; quote the window exactly as the live page states it), `https://www.va.gov/find-forms/about-form-21-526ez/` (revision date), 38 CFR §3.155 and §3.151.
2. **Check eligibility for the window:** compare the user's separation date (the user provides it) to the quoted live window. If the user is outside the window, quote what the page says applies instead.
3. **Gather evidence:** service treatment records and the `claim-citations` table; list the open gaps from `related-gaps`. Cite §3.159.
4. **Path:** the online or form path the BDD page describes now, including any exam availability requirement it states. Cite URL.
5. **Hand-off checklist** and recommend review with a VA-accredited representative, VSO, or attorney.

## Track A2: Active duty, medical separation or retirement (IDES)

Steps differ by branch of service, so check your branch's current regulation with a PEBLO (Physical Evaluation Board Liaison Officer) or an accredited representative.

1. **Validate sources (DoD and VA level):** `https://www.va.gov/disability/how-to-file-claim/when-to-file/pre-discharge-claim/` (its IDES section), `https://www.va.gov/find-forms/about-form-21-526ez/`, 10 U.S.C. chapter 61 from govinfo (`https://www.govinfo.gov/link/uscode/10/{section}?type=usc&year=mostrecent&link-type=html` for §1201 retirement, §1202 temporary disability retired list, §1203 separation, §1210 TDRL periodic exams, §1216a determinations), DoD Instruction 1332.18 (Disability Evaluation System) from the official DoD issuances site (find the current edition there; do not assume a number or date), and 38 CFR §3.155 via `cfr-fetch`. If any source, including DoDI 1332.18, cannot be fetched and confirmed, **stop**.
2. **Explain the IDES stages** only as the fetched sources describe them: referral, Medical Evaluation Board (MEB), VA examinations and the VA proposed rating, Physical Evaluation Board (PEB) fitness decision. Cite the source for each stage.
3. **Fit vs separation vs retirement:** quote 10 U.S.C. §1201 (retirement), §1202 and §1210 (temporary disability retired list), and §1203 (separation) from the fetched text, plus the DoDI 1332.18 criteria. Do not predict the outcome or any percentage.
4. **Gather evidence:** `claim-citations` and `related-gaps` outputs for conditions the member wants VA to consider. Cite §3.159.
5. **Hand-off checklist** and recommend review with the PEBLO and a VA-accredited representative, VSO, or attorney (38 CFR §14.629).

## Track B1: Veteran, initial claim

1. **Validate sources:** `https://www.va.gov/disability/how-to-file-claim/`, `https://www.va.gov/resources/your-intent-to-file-a-va-claim/`, `https://www.va.gov/find-forms/about-form-21-0966/` (revision date), `https://www.va.gov/find-forms/about-form-21-526ez/` (revision date), `https://www.va.gov/disability/how-to-file-claim/evidence-needed/fully-developed-claims/`, 38 CFR §3.150, §3.151, §3.155, §3.159, §3.160.
2. **Intent to file:** explain the options exactly as §3.155 and the intent-to-file page describe them now (e.g. VA Form 21-0966). Cite both.
3. **Gather evidence:** from `claim-citations` (files and pages) and `related-gaps` (nexus letter, DBQ). Cite §3.159.
4. **Path:** online application or VA Form 21-526EZ (with current revision date). Cite URL.
5. **Fully developed vs standard:** only if the live page still offers both; quote each; the user chooses. Otherwise say it is no longer offered.
6. **Hand-off checklist** and recommend accredited review.

## Track B2: Veteran, already filed

1. **Validate sources:** `https://www.va.gov/disability/how-to-file-claim/` (increased disability / new conditions), `https://www.va.gov/find-forms/about-form-21-526ez/`, `https://www.va.gov/decision-reviews/`, `https://www.va.gov/decision-reviews/supplemental-claim/`, `https://www.va.gov/decision-reviews/higher-level-review/`, `https://www.va.gov/decision-reviews/board-appeal/`, `https://www.va.gov/find-forms/about-form-20-0995/`, `https://www.va.gov/find-forms/about-form-20-0996/`, `https://www.va.gov/find-forms/about-form-10182/` (each with revision date), 38 CFR §3.2500, §3.2501, §3.2601, and Part 20 §20.202 and §20.203 (Board Notice of Disagreement) via `cfr-fetch`.
2. **Pick the lane** from what the user says happened, quoting the live pages:
   - condition got worse → claim for increase (21-526EZ);
   - new condition, including secondary → new claim (21-526EZ);
   - disagree with a decision and have new and relevant evidence → Supplemental Claim (20-0995, §3.2501);
   - disagree, no new evidence, want a senior reviewer → Higher-Level Review (20-0996, §3.2601);
   - appeal to the Board → Board Appeal (VA Form 10182; 38 CFR §20.202 Notice of Disagreement, §20.203 place and time of filing, quoted from fetched Part 20 text).
   Quote the current deadline for each lane from its live page (e.g. "within 1 year" of the decision); never state a deadline from memory.
3. **Gather evidence:** decision letter (the user's local copy), `claim-citations`, and `related-gaps`. For a Supplemental Claim, identify what is new and relevant. Cite §3.2501.
4. **Path:** the online or form option the lane's page describes now. Cite URL.
5. **Hand-off checklist** and recommend accredited review.

## Track C: Survivors and dependents

1. **Validate sources:** `https://www.va.gov/family-and-caregiver-benefits/survivor-compensation/`, `https://www.va.gov/family-and-caregiver-benefits/survivor-compensation/dependency-indemnity-compensation/`, `https://www.va.gov/family-and-caregiver-benefits/survivor-compensation/survivors-pension/`, `https://www.va.gov/forms/21p-534ez/` (VA Form 21P-534EZ; read its revision date; the old `/find-forms/about-form-21p-534ez/` URL redirects here), 38 CFR §3.5 (DIC), §3.22, §3.50 (surviving spouse), §3.152 (claims for death benefits), §3.1000 (accrued benefits), §3.1010 (substitution).
2. **Identify the benefit and relationship:** from what the user says (surviving spouse, child, parent), quote the eligibility text on the live pages and §3.5 / §3.50. Do not decide eligibility.
3. **Gather evidence:** death certificate and the veteran's records (user's local copies), plus `claim-citations` output if service connection of the cause of death is in question. Cite §3.152 and §3.159.
4. **Path:** the online or form path (e.g. 21P-534EZ) the live pages describe now; accrued benefits and substitution per the live page and §3.1000 / §3.1010, including any time limit quoted from the source.
5. **Hand-off checklist** and recommend accredited review (38 CFR §14.629).

## Stop rules

- Any source in step 1 unconfirmed → stop; no process guidance from memory.
- Never submit, sign, upload, fax, mail, call, or message VA; never fill in a signature; never create a VA.gov account action for the user.
- Never state a form number, revision date, deadline, or effective-date rule unless quoted from a source fetched in this session.
- No outcome predictions. Unofficial rating estimates come only from proposed-issues, each with this notice: Unofficial estimate, not a VA decision. It is likely to differ after the VA exam (C&P exam). Confirm with a VA-accredited representative.

## Output template (all tracks)

```
Track: <A1 | A2 | B1 | B2 | C>   As of: <date>
Sources validated:
| Source | URL / section | Status | Detail (form no. + revision date / section heading / quoted window) |
|--------|---------------|--------|----------------------------------------------------------------------|
Steps (one row per step of the chosen track, in order):
| # | Step | What the source says (quoted) | Cite (URL or section) |
|---|------|-------------------------------|-----------------------|
| 1 | Validate sources | all confirmed / STOPPED: <source> | ... |
| 2 | <A1 window check / A2 IDES stages / B1 intent to file / B2 lane / C benefit and relationship> | "..." | ... |
| 3 | <A2 fit vs separation vs retirement / evidence> | "..." | ... |
| … | ... | ... | ... |
Evidence: <claim-citations rows>   Open gaps: <related-gaps rows, "needs more documentation">
Forms named (only if validated): <form, revision date, URL>
Checklist for the user: ...
Not legal advice. The bot does not file, sign, or contact VA. Review with a VA-accredited representative, VSO, or attorney (and the PEBLO for A2).
```
