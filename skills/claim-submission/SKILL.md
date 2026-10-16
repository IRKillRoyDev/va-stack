---
name: claim-submission
description: Guide the user through the process of submitting a VA disability claim (intent to file, evidence package, form or online path, fully developed vs standard claim), after first validating every primary source live with an as-of date. The bot guides only; it never submits, signs, or contacts VA.
---

# claim-submission

**Guidance only.** The bot never submits, signs, uploads, or contacts VA or anyone on the user's behalf. The user (ideally with a VA-accredited representative, VSO, or attorney) does all filing. See SAFETY.md.

## Steps

1. **Validate primary sources (always first).** Fetch live, record HTTP status and an "as of" date (today's date for VA.gov pages; eCFR `up_to_date_as_of` for CFR):
   - VA.gov: `https://www.va.gov/disability/how-to-file-claim/`, `https://www.va.gov/resources/your-intent-to-file-a-va-claim/`, `https://www.va.gov/disability/how-to-file-claim/evidence-needed/fully-developed-claims/`
   - Forms: `https://www.va.gov/find-forms/about-form-21-526ez/` and `https://www.va.gov/find-forms/about-form-21-0966/`. Read the current form number and the "revision date" shown on each page.
   - 38 CFR Part 3 via `cfr-fetch`: confirm §3.150, §3.151, §3.155 (how to file a claim, including intent to file), §3.159, §3.160 exist in the fetched text and quote the parts used.
   - If any page is not HTTP 200, a form page no longer shows that form, a section is missing, or content conflicts between sources → **stop** and tell the user which source could not be confirmed.
2. **Intent to file.** Explain the intent-to-file options exactly as the current §3.155 text and the VA.gov intent-to-file page describe them (including the current form, e.g. VA Form 21-0966, with its revision date). Cite the URL and section.
3. **Gather evidence.** Use the `claim-citations` table: for each row list the record files and pages to include, and the gaps still open. Point `related-gaps` items to what documentation (nexus letter, DBQ) the user may want to get first. Cite §3.159 for VA's duty to assist as quoted from the fetched text.
4. **Choose the path.** Present the online application and the paper form (e.g. VA Form 21-526EZ, with current revision date) as described on the current VA.gov pages. Cite each URL.
5. **Fully developed vs standard.** Only if the current VA.gov page still offers both, quote how each works and let the user decide. If the page no longer offers the choice, say so and skip.
6. **Hand-off checklist.** Give the user a checklist (forms, evidence files, open gaps) and recommend review with a VA-accredited representative, VSO, or attorney before filing.

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
