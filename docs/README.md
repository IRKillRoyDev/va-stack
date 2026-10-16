# va-stack docs

Not legal or medical advice. See [SAFETY.md](../SAFETY.md).

## Pipeline

The [va-claims agent](../agents/va-claims.md) runs:

1. claim-submission Step 0: which track (A1 active duty pre-discharge/BDD; A2 active duty medical separation or retirement through IDES: MEB, VA proposed rating, PEB, fit vs separation vs retirement/TDRL, citing DoDI 1332.18 and 10 U.S.C. ch. 61; steps differ by branch, so check the branch's current regulation with a PEBLO or accredited representative; B1 veteran initial claim; B2 veteran already filed: increase, new condition, Supplemental Claim, Higher-Level Review, Board Appeal, with Board steps citing 38 CFR Part 20; C survivors and dependents: DIC, survivors pension, accrued benefits, e.g. VA Form 21P-534EZ)
2. cfr-fetch: 38 CFR Parts 3, 4, 14 (representation and accreditation) and 20 (Board rules of practice) from the eCFR versioner API, with an as-of date
3. records-intake: index the private records folder by file, page, date
4. condition-map: diagnostic code (Part 4) and basis (Part 3: direct §3.303, presumptive §3.307/§3.309, secondary §3.310, aggravation §3.306), shown vs missing
5. claim-citations: table of condition, basis, CFR, DC, evidence (file p.# "quote"), gaps; rows without both cite types are dropped
6. related-gaps: related/secondary conditions labeled "needs more documentation"
7. claim-submission: the chosen track's guidance, each step citing a live VA.gov URL or CFR section

## The private local records folder

- The user names one local folder; records are read only from there.
- It must not be inside a git repository or a public sync folder.
- The index and extractions (`_index.json`, `_extract.json`, `claims.json`) are written into that same folder and nowhere else.
- Records are never pasted into chat, committed, or uploaded. Outputs redact what the user does not need.
- This repository holds fictional test data only (`tests/fake-records/`).

## Where accredited review fits

- **Before acting on outputs:** have a VA-accredited Veterans Service Organization (VSO) representative, attorney, or claims agent review the claim table, the gaps, and the chosen track. Accreditation requirements are in 38 CFR §14.629 (cite from the fetched Part 14 text). Check accreditation at https://www.va.gov/ogc/apps/accreditation/.
- **Before filing:** the user (with their accredited representative) files. The bot only produces the hand-off checklist.
- **In IDES (track A2):** the PEBLO guides the member through the branch's process; an accredited representative can review the VA proposed rating.
- **For gaps:** nexus letters and DBQs come from medical providers; an accredited representative can advise which gaps matter.
