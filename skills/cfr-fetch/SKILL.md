---
name: cfr-fetch
description: Fetch the current text of 38 CFR Parts 3, 4, 14 and 20 from the official eCFR versioner API, record the "as of" date, and make sections citable. Never cite from memory; stop if the fetch fails.
---

# cfr-fetch

Parts fetched: **3** (adjudication), **4** (rating schedule), **14** (legal services, General Counsel, and accreditation of representatives, e.g. §14.629), **20** (Board of Veterans' Appeals rules of practice).

## Verified endpoints

- Title metadata (gives `up_to_date_as_of`):
  `GET https://www.ecfr.gov/api/versioner/v1/titles.json` → entry with `"number": 38`
- Full text of one part on a date:
  `GET https://www.ecfr.gov/api/versioner/v1/full/{DATE}/title-38.xml?part=3`
  `GET https://www.ecfr.gov/api/versioner/v1/full/{DATE}/title-38.xml?part=4`
  `GET https://www.ecfr.gov/api/versioner/v1/full/{DATE}/title-38.xml?part=14`
  `GET https://www.ecfr.gov/api/versioner/v1/full/{DATE}/title-38.xml?part=20`
- The `full` endpoint **requires a compressed response**. Send `Accept-Encoding: gzip` (e.g. `curl --compressed`); without it the API returns HTTP 406.

## Steps

1. GET `titles.json`. Read `up_to_date_as_of` for title 38. That is `{DATE}` and the "as of" date for every citation in this session.
2. GET Parts 3, 4, 14 and 20 with `--compressed` for `{DATE}`. Require HTTP 200 and a non-trivial body.
3. Sanity check: Part 3 contains `N="3.303"`, `N="3.306"`, `N="3.307"`, `N="3.309"`, `N="3.310"`; Part 4 contains `N="4.71a"` and `N="4.130"`; Part 14 contains `N="14.629"` and `N="14.631"`; Part 20 contains `N="20.202"`. Cite Part 14 and Part 20 sections only from this fetched text. If any is missing, treat the fetch as failed.
4. Split each part by section (`DIV8 N="..."`). Keep heading and text keyed by section number. Keep diagnostic-code tables keyed by DC number within their section.
5. Store the fetched XML in the session's private working folder (never in this repo) with the date in the filename.
6. Cite as: `38 CFR §3.310(a) (eCFR, as of {DATE})`; for codes: `38 CFR §4.71a, DC 5242 (eCFR, as of {DATE})`. Quote text exactly from the fetched copy.

## Stop rules

- Any non-200 response, timeout, parse error, or failed sanity check → **stop**. Tell the user: "Could not fetch current 38 CFR from eCFR ({status/error}). No citations will be produced." Do not fall back to memory, cached web pages, or secondary sites.
- A section or DC not found in the fetched text is not cited. Say it was not found.
- Never paraphrase CFR text as if it were a quote.

## Output template

```
CFR source: eCFR versioner API
Endpoint(s): <exact URLs>
As of: <DATE>
Sanity checks: <pass/fail per section>
Sections loaded: Part 3: <n>, Part 4: <n>, Part 14: <n>, Part 20: <n>
```
