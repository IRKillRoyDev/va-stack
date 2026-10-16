---
name: claim-submission
description: Guide the user through submitting a VA disability claim on the right track (A active duty pre-discharge/BDD; B1 veteran initial claim with intent to file; B2 veteran increase, new condition, supplemental claim, higher-level review, or Board appeal), after first validating every primary source live with an as-of date. The bot guides only; it never submits, signs, or contacts VA.
---

# claim-submission

**Guidance only.** The bot never submits, signs, uploads, or contacts VA or anyone on the user's behalf. The user (ideally with a VA-accredited representative, VSO, or attorney) does all filing. See SAFETY.md.

## Step 0: Ask which track

Ask the user, and do not assume:
- **A. Active duty, transitioning out** (pre-discharge, e.g. Benefits Delivery at Discharge, BDD)
- **B1. Veteran, never filed**: initial claim with intent to file
- **B2. Veteran, already filed**: increased rating, new condition, supplemental claim, higher-level review, or Board appeal

Then run the shared validation and the steps for that track only. Each track begins with source validation. Every step cites a source URL or a 38 CFR section fetched in this session.

## Shared validation rules (step 1 of every track)

- Fetch each listed URL live; require HTTP 200 and that the page still describes the item (form number, program, window). Record the as-of date (today for VA.gov; eCFR `up_to_date_as_of` for CFR via `cfr-fetch`).
- On a VA.gov "about form" page, read the form number and its "revision date".
- Quote the 38 CFR section headings and any text used.
- **Stop** if any source is unconfirmed, returns non-200, or conflicts with another source. Name the source that failed.

## Track A: Active duty (pre-discharge)

1. **Validate sources:** `https://www.va.gov/disability/how-to-file-claim/when-to-file/pre-discharge-claim/` (BDD program and its current filing window, e.g. "180 to 90 days before discharge" as of this writing; quote the live text), `https://www.va.gov/find-forms/about-form-21-526ez/` (revision date), 38 CFR §3.155 and §3.151.
2. **Check eligibility for the window:** compare the user's separation date (the user provides it) to the quoted live window. If the user is outside the window, quote what the page says applies instead.
3. **Gather evidence:** service treatment records and the `claim-citations` table; list the open gaps from `related-gaps`. Cite §3.159.
4. **Path:** the online or form path the BDD page describes now, including any exam availability requirement it states. Cite URL.
5. **Hand-off checklist** and recommend review with a VA-accredited representative, VSO, or attorney.

## Track B1: Veteran, initial claim

1. **Validate sources:** `https://www.va.gov/disability/how-to-file-claim/`, `https://www.va.gov/resources/your-intent-to-file-a-va-claim/`, `https://www.va.gov/find-forms/about-form-21-0966/` (revision date), `https://www.va.gov/find-forms/about-form-21-526ez/` (revision date), `https://www.va.gov/disability/how-to-file-claim/evidence-needed/fully-developed-claims/`, 38 CFR §3.150, §3.151, §3.155, §3.159, §3.160.
2. **Intent to file:** explain the options exactly as §3.155 and the intent-to-file page describe them now (e.g. VA Form 21-0966). Cite both.
3. **Gather evidence:** from `claim-citations` (files and pages) and `related-gaps` (nexus letter, DBQ). Cite §3.159.
4. **Path:** online application or VA Form 21-526EZ (with current revision date). Cite URL.
5. **Fully developed vs standard:** only if the live page still offers both; quote each; the user chooses. Otherwise say it is no longer offered.
6. **Hand-off checklist** and recommend accredited review.

## Track B2: Veteran, already filed

1. **Validate sources:** `https://www.va.gov/disability/how-to-file-claim/` (increased disability / new conditions), `https://www.va.gov/find-forms/about-form-21-526ez/`, `https://www.va.gov/decision-reviews/`, `https://www.va.gov/decision-reviews/supplemental-claim/`, `https://www.va.gov/decision-reviews/higher-level-review/`, `https://www.va.gov/decision-reviews/board-appeal/`, `https://www.va.gov/find-forms/about-form-20-0995/`, `https://www.va.gov/find-forms/about-form-20-0996/`, `https://www.va.gov/find-forms/about-form-10182/` (each with revision date), 38 CFR §3.2500, §3.2501, §3.2601.
2. **Pick the lane** from what the user says happened, quoting the live pages:
   - condition got worse → claim for increase (21-526EZ);
   - new condition, including secondary → new claim (21-526EZ);
   - disagree with a decision and have new and relevant evidence → Supplemental Claim (20-0995, §3.2501);
   - disagree, no new evidence, want a senior reviewer → Higher-Level Review (20-0996, §3.2601);
   - appeal to the Board → Board Appeal (VA Form 10182).
   Quote the current deadline for each lane from its live page (e.g. "within 1 year" of the decision); never state a deadline from memory.
3. **Gather evidence:** decision letter (the user's local copy), `claim-citations`, and `related-gaps`. For a Supplemental Claim, identify what is new and relevant. Cite §3.2501.
4. **Path:** the online or form option the lane's page describes now. Cite URL.
5. **Hand-off checklist** and recommend accredited review.

## Stop rules

- Any source in step 1 unconfirmed → stop; no process guidance from memory.
- Never submit, sign, upload, fax, mail, call, or message VA; never fill in a signature; never create a VA.gov account action for the user.
- Never state a form number, revision date, deadline, or effective-date rule unless quoted from a source fetched in this session.
- No outcome or rating predictions.

## Output template

```
Sources validated (as of <date>):
| Source | URL / section | Status | Detail (form no. + revision date / section heading) |
|--------|---------------|--------|------------------------------------------------------|
Step 2 Intent to file: ... [cite: URL; 38 CFR §3.155 (eCFR, as of <date>)]
Step 3 Evidence: <from claim-citations>  Gaps: <...>  [cite]
Step 4 Path: online / VA Form 21-526EZ (rev. <date>)  [cite]
Step 5 FDC vs standard: <quoted, or "no longer offered">  [cite]
Step 6 Checklist: ...
Not legal advice. The bot does not file. Review with a VA-accredited representative, VSO, or attorney.
```
