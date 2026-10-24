# va-stack docs

Not legal or medical advice. See [SAFETY.md](../SAFETY.md).

## Pipeline

The [va-claims agent](../agents/va-claims.md) runs:

1. claim-submission Step 0: which track (A1 active duty pre-discharge/BDD; A2 active duty medical separation or retirement through IDES: MEB, VA proposed rating, PEB, fit vs separation vs retirement/TDRL, citing DoDI 1332.18 and 10 U.S.C. ch. 61; steps differ by branch, so check the branch's current regulation with a PEBLO or accredited representative; B1 veteran initial claim; B2 veteran already filed: increase, new condition, Supplemental Claim, Higher-Level Review, Board Appeal, with Board steps citing 38 CFR Part 20; C survivors and dependents: DIC, survivors pension, accrued benefits, e.g. VA Form 21P-534EZ)
2. cfr-fetch: 38 CFR Parts 3, 4, 14 (legal services, General Counsel, and accreditation of representatives, e.g. §14.629) and 20 (Board rules of practice) from the eCFR versioner API, with an as-of date
3. local-search: the user runs `scripts/local_search.py` on their own computer (no network calls, text held in memory, nothing written); it returns capped, redacted snippets (`file`, `page`, `line`, `context`); the user approves the snippet list before any of it goes to the AI service; whole pages only with explicit approval
4. records-intake: index the private records folder by file, page, date
5. condition-map: diagnostic code (Part 4) and basis (Part 3: direct §3.303, presumptive §3.307/§3.309, secondary §3.310, aggravation §3.306), shown vs missing
6. claim-citations: table of condition, basis, CFR, DC, evidence (file p.# "quote"), gaps; rows without both cite types are dropped
7. related-gaps: related/secondary conditions labeled "needs more documentation"
8. proposed-issues: full cited report plus form-mapped proposed issues for the track's verified form (VA Form 21-526EZ Section V for BDD, initial and increase; 20-0995, 20-0996, 10182, 21P-534EZ, 21-0966 for the others), unofficial Part 4 estimates with gap analysis and the §4.25 combined estimate, each with the unofficial-estimate notice
9. claim-submission: the chosen track's guidance, each step citing a live VA.gov URL or CFR section

## The private local records folder

- The user names one local folder; records are read only from there.
- It must not be inside a git repository or a public sync folder.
- The index and extractions (`_index.json`, `_extract.json`, `claims.json`) are written into that same folder and nowhere else.
- The skills never commit, upload, or copy the record files. The text the bot reads is processed by the AI service it runs on, so check that service's privacy terms; a fully offline setup needs a local model.
- Search first with `scripts/local_search.py` (`--dry-run` prints counts only). Redaction is pattern-based: it can miss identifiers and can over-redact, so the user reviews every snippet. Known gaps: labeled names are redacted (Name, Patient, Veteran, and the other labels in SAFETY.md); names without a label are not. A bare year ("born in 1961") is not redacted; an unlabeled phone such as "555 010 0199" or an unlabeled 7-digit phone is missed; a minor's (child's) date of birth with no birth label is missed. Pages are redacted before searching; dates after a birth label are redacted in every mode; full dates 17+ years old become `[DATE-YYYY]` unless `--keep-dates` is passed.
- Optional encrypted vault: `scripts/records_vault.py` (SQLCipher + Argon2id; header `<db>.kdf.json` holds the salt and settings) and the Unlock window `scripts/vault_ui.py`. Consent to share is asked before the password on every unlock; every request needs a bot id; an HMAC-chained access log inside the encrypted file (with a MAC'd head in `<db>.loghead.json` to catch truncation and rollback) records what was touched and keyed HMACs of snippets sent, never their text; anyone with the password can still rewrite it. See [records-vault](../skills/records-vault/SKILL.md) and SAFETY.md for memory-wiping limits.
- This repository holds fictional test data only (`tests/fake-records/`).

## Where accredited review fits

- **Before acting on outputs:** have a VA-accredited Veterans Service Organization (VSO) representative, attorney, or claims agent review the claim table, the gaps, and the chosen track. Accreditation requirements are in 38 CFR §14.629 (cite from the fetched Part 14 text). Check accreditation at https://www.va.gov/ogc/apps/accreditation/.
- **Before filing:** the user (with their accredited representative) files. The bot only produces the hand-off checklist.
- **In IDES (track A2):** the PEBLO guides the member through the branch's process; an accredited representative can review the VA proposed rating.
- **For gaps:** nexus letters and DBQs come from medical providers; an accredited representative can advise which gaps matter.
