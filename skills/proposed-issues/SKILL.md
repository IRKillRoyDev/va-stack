---
name: proposed-issues
description: Produce two outputs side by side - the full cited claim report and a proposed-issues list laid out column-for-column like the verified VA form for the user's track - with an unofficial Part 4 rating estimate per issue, a gap analysis for the next level, and a combined estimate using the 38 CFR §4.25 table.
---

# proposed-issues

Requires: claim-citations rows, condition-map diagnostic codes, and the encrypted vault ([records-vault](../records-vault/SKILL.md)). Run `python3 scripts/proposed_issues.py "<db>" "<input.json>" --bot-id <id>` on the user's computer.

## Outputs (both, always)

1. **Full cited report** (primary): one row per claimed issue with condition, basis (direct, secondary, presumptive, aggravation), in-service event or exposure with an approximate date, evidence links, the Part 4 wording for the estimate, the estimate, and the gap analysis.
2. **Form-mapped list** (additional): the same issues laid out under the exact column labels of the verified form for the user's track. Fields that are not on the form (basis, evidence, estimate) stay in the cited report.

## Track → form

- A1: 21-526EZ
- B1: 21-526EZ
- B2-increase: 21-526EZ
- B2-new-condition: 21-526EZ
- B2-supplemental: 20-0995
- B2-hlr: 20-0996
- B2-board: 10182
- C-dic: 21P-534EZ
- intent-to-file: 21-0966
- A2: no verified form; full cited report only

## Verified forms (two sources each: VA.gov form page and the form PDF; as of 2026-10-16)

Each label below was found in the PDF's text and each field name in its AcroForm field list (`tests/fixtures/va-forms/<form>.json`, rebuilt with `tools/build_reference_fixtures.py`). The VA.gov page's "Form revision date" matches the PDF footer edition. A column whose label or field cannot be verified is marked UNVERIFIED, and the tests fail until it is verified or removed.

| Form | Edition | VA.gov page | PDF | As of | Section | Columns (label → first AcroForm field) |
|---|---|---|---|---|---|---|
| 21-526EZ | JAN 2026 | https://www.va.gov/find-forms/about-form-21-526ez/ | https://www.vba.va.gov/pubs/forms/VBA-21-526EZ-ARE.pdf | 2026-10-16 | SECTION V: CLAIM INFORMATION (PDF page 11) | "CURRENT DISABILITY(IES)" → `F[0].#subform[10].CURRENTDISABILITY[0]` (+14 more rows); "IF DUE TO EXPOSURE, EVENT, OR INJURY, PLEASE SPECIFY" → `F[0].#subform[10].Specify_Type_Of_Exposure_Event_Or_Injury[0]` (+14 more rows); "EXPLAIN HOW THE DISABILITY(IES) RELATES TO THE IN-SERVICE EVENT/EXPOSURE/INJURY" → `F[0].#subform[10].ExplainHowDisabilityRelatesToEvent_Exposure_Injury[0]` (+14 more rows); "APPROXIMATE DATE DISABILITY(IES) BEGAN OR WORSENED" → `F[0].#subform[10].Date_Disability_Began_Or_Worsened[0]` (+14 more rows) |
| 21-0966 | MAY 2026 | https://www.va.gov/find-forms/about-form-21-0966/ | https://www.vba.va.gov/pubs/forms/VBA-21-0966-ARE.pdf | 2026-10-16 | SECTION III: GENERAL BENEFIT ELECTION (PDF page 2) | "19. I INTEND TO FILE FOR THE GENERAL BENEFIT(S) CHECKED BELOW" → `F[0].#subform[1].Compensation[0]` |
| 20-0995 | MAY 2024 | https://www.va.gov/find-forms/about-form-20-0995/ | https://www.vba.va.gov/pubs/forms/VBA-20-0995-ARE.pdf | 2026-10-16 | SECTION IV: ISSUE(S) FOR SUPPLEMENTAL CLAIM (PDF page 5) | "21A. SPECIFIC ISSUE(S)" → `form1[0].#subform[17].Table2[0].Row1[0].SPECIFICISSUE1[0]` (+6 more rows); "21B. DATE OF VA DECISION NOTICE" → `form1[0].#subform[17].Table2[0].Row1[0].#subform[0].#subform[1].Month[0]` (+20 more rows) |
| 20-0996 | MAR 2024 | https://www.va.gov/find-forms/about-form-20-0996/ | https://www.vba.va.gov/pubs/forms/VBA-20-0996-ARE.pdf | 2026-10-16 | SECTION V - ISSUES FOR HIGHER-LEVEL REVIEW (PDF page 4) | "18A. SPECIFIC ISSUE(S) OF DISAGREEMENT (REQUIRED)" → `form1[0].#subform[3].SPECIFICISSUE1[0]` (+12 more rows); "18B. DATE OF VA DECISION NOTIFICATION" → `form1[0].#subform[3].Date_Month[0]` (+38 more rows) |
| 10182 | JUN 2026 | https://www.va.gov/find-forms/about-form-10182/ | https://www.va.gov/vaforms/va/pdf/VA10182.pdf | 2026-10-16 | PART III - SPECIFIC ISSUE(S) TO BE APPEALED TO A VETERANS LAW JUDGE AT THE BOARD (PDF page 1) | "A. Specific Issue(s)" → `form1[0].#subform[0].Table1[0].Row1[0].SpecificIssues1[0]` (+4 more rows); "B. Date of Decision" → `form1[0].#subform[0].Table1[0].Row1[0].DateDecision1[0]` (+4 more rows) |
| 21P-534EZ | AUG 2025 | https://www.va.gov/find-forms/about-form-21p-534ez/ | https://www.vba.va.gov/pubs/forms/VBA-21P-534EZ-ARE.pdf | 2026-10-16 | SECTION VII: DEPENDENCY AND INDEMNITY COMPENSATION (D.I.C.) (PDF page 14) | "7A. WHAT BENEFIT ARE YOU CLAIMING?" → `form1[0].#subform[152].Dependency_And_Indemnity_Compensation_DIC[0]` |

## Approximate dates

Section V of VA Form 21-526EZ asks for the approximate date a disability began or worsened. Old full dates are shown as `[DATE-YYYY]` by default, so:

1. The user reruns the search with `--keep-dates` and reviews the snippets.
2. Dates near a birth label (DOB, born, birthday, and similar) stay redacted in every mode, including `--keep-dates`.
3. Enter approximate dates as month and year (MM-YYYY), only from snippets the user has confirmed.

## Estimate per issue

- Each diagnostic code's criteria are modelled as structured factors in `rating-criteria.json` (e.g. DC 7806 under the General Rating Formula for the Skin: percent of the entire body affected, percent of exposed areas affected, and type and duration of treatment over the past 12 months). Each factor band carries its verbatim eCFR cite, checked against `tests/fixtures/ecfr-part4-ratings-2026-10-14.json` (eCFR versioner API, title 38 as of 2026-10-14; fetched 2026-10-16).
- The estimate is the highest level that any documented factor meets ("at least one of"). If none of the factors the criteria need is documented, output exactly: `insufficient evidence to estimate`.
- Each row shows: (a) evidence links `vault://<record_id>#page=N` with redacted quotes (the vault opens them after unlock and logs each open with the bot id; no file paths appear), (b) the Part 4 wording for the estimated level with section, DC and eCFR link, and (c) the gap analysis.

## Evidence quotes (whole sentences only)

- **Your review is the final check.** The report asks the user to open each record page at its vault link (every row and every flag points to the page, line and date) and answer Yes or No for each row: (a) confirm or correct what each quote means, especially flagged rows; (b) list anything in the file that is missing from the report; (c) list any names or personal details that redaction missed. On No, the user is asked to type the rest of the missing sentence or paragraph from the record, leaving out PII; the text is still run through redaction, the user is warned if anything was caught, and it is saved in the encrypted vault next to the row with the USER_UPDATED flag, the time, and an access-log entry (keyed HMAC only, never the text). The review works like a store checkout (`scripts/review_session.py`, shown in the `scripts/vault_ui.py` window or as a text walk-through): one row per screen with a progress count ("3 of 12"), the plain reading, the quote with its context, the vault pointer and any flags; big Yes and No buttons (Enter is Yes, n is No, Esc goes back, Right goes next); Back and Next always available; then a summary listing every row as confirmed or user updated, with an Edit link back to each row, and one Confirm all button. Answers stay in memory until Confirm all; nothing is written to the vault before that, and Confirm all saves everything in one step with its log entries. Nothing is sent over the network. The original quote and the tool's reading are kept, never overwritten. Every row starts unreviewed; a row is final only after Yes, or after No with the user's text saved. The 21-526EZ column that explains how the disability relates to service is filled only from a Yes (the cited quotes) or that saved text, never by the tool on its own. In short, the user's review is the final check.
- **What a cited quote means.** In short: the person reading checks what the quote means, and the tool only proves the quote is really in the record. Every cited quote is shown with its context (2 sentences before and 2 after, redacted) with the cited sentences marked `[QUOTE]...[/QUOTE]`. A partial quote is widened to the whole sentence(s) it sits in. If any negation or hedge word (for example not, cannot, n't, ruled out, unlikely, less likely than not, insufficient, inconsistent, doubtful, less than 50 percent) appears anywhere in that window, the row is flagged "Possible negative or qualified opinion; read the context." Rows are never dropped, and no row is labeled as backing a claim: every row is labeled "cited, needs your read."

- Every evidence quote must be one or more whole sentences from the cited record page, copied exactly from the start of the first sentence to the end of the last (differences in spacing and line breaks are ignored; letter case is not). Quote the whole line or sentence, for example `Examiner: it is less likely than not that tinnitus is related to military noise exposure.`, never the part after the negation.
- Soft line wraps are joined first: a line break ends a sentence only after `.`, `!` or `?`, or when a blank line follows. Sentences end at `.`, `!` or `?` followed by a space. Common abbreviations (e.g., i.e., Dr., Mr., Mrs., Ms., vs., etc., approx., No.) and decimals such as 3.5 do not end a sentence.
- The quote is compared with the record before redaction, so a quote whose SSN, date, letters or letter case differ from the record is refused. The report then shows it redacted.
- Before the negation check the context is normalized (NFKC, Cyrillic and Greek look-alike letters folded to Latin, curly apostrophes made straight). Negation and hedge words also include deny, none, neither/nor, absence, lacks, speculation, excluded, improbable, contrary, unsupported, rather than, outweighs, and any "less than ... 50" or "50 ... or less" phrasing (digits or "fifty", with or without % or percent).
- The uncertain-boundary flag is also raised when the sentence just beyond the context window holds a negation or hedge word.
- Every row, link and form section carries the note "No flag does not mean the opinion is positive."
- On the 21-526EZ list, the column that explains how the disability relates to the in-service event never asserts a link: it holds "cited, needs your read." plus any flags, for the user to read and fill in. Every form section carries the label, the flags, and that note (`label`, `flags`, `row_notes`).
- Quotes shorter than 20 characters are refused unless they are a whole line. Empty quotes are refused.

## Gap analysis (next level)

- For the next higher level, each factor is `met`, `not met`, or `not documented`, with its eCFR cite.
- `not documented`: ask the provider to document what is already true.
- `not met`: describe what the records show and, for treatment factors, suggest asking the provider whether other treatment options fit the condition.
- Never suggest worsening a condition, or seeking care, for the sake of a rating. Frame everything as care options to discuss with the provider and documentation of what is already true.
- If the estimate is already the highest level for that DC, say so.

## Combined estimate

Order ratings highest first and combine with Table I of 38 CFR §4.25: combined = 1 - (1 - r1)(1 - r2)...(1 - rn), where each step is read from the table as a whole number; convert the final value to the nearest 10, adjusting 5 upward. For a bilateral pair (§4.26), combine the pair first, add 10 percent of that value, and treat the result as one rating.

## Notice (on every estimate)

Unofficial estimate, not a VA decision. It is likely to differ after the VA exam (C&P exam). Confirm with a VA-accredited representative.

## Stop rules

- `verify_forms()` reports any problem → do not produce the form-mapped list.
- No record cite → no estimate for that issue.
- Gap text that pushes toward a rating is rejected by the script.
