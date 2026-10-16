---
name: claim-citations
description: For each claimable condition from condition-map, build a table of condition, basis, CFR section, diagnostic code, evidence (file p.# "quote") and gaps. Any row lacking at least one CFR cite and one record cite is dropped.
---

# claim-citations

## Steps

1. Take condition-map rows where at least the current diagnosis is `shown`.
2. For each, write one row: `condition`, `basis`, `cfr` (list of `38 CFR §x.y` cites with eCFR as-of date), `dc`, `evidence` (list of `{file, page, quote}`), `gaps`.
3. Re-verify every quote: open the cited file at the cited page and confirm the quote appears verbatim. If not, remove that evidence item.
4. Re-verify every CFR cite exists in the fetched eCFR text.
5. **Drop rule:** a row with zero valid CFR cites OR zero valid record cites is dropped and listed under "Dropped" with the reason.
6. List remaining missing elements in `gaps` (e.g. "nexus opinion missing").
7. Save as `claims.json` in the private folder; render the table for the user with redaction applied.

## Stop rules

- Never invent a cite or a quote. Never cite CFR from memory.
- Do not label anything "approved" or "granted". Unofficial percentage estimates appear only in the proposed-issues output, each with this notice: Unofficial estimate, not a VA decision. It is likely to differ after the VA exam (C&P exam). Confirm with a VA-accredited representative.
- Related/secondary conditions without a current diagnosis go to related-gaps, not here.

## Output template

```
As of: <eCFR date>. Not legal or medical advice; verify with a VA-accredited representative, VSO, or attorney.
| Condition | Basis | CFR | DC | Evidence | Gaps |
|-----------|-------|-----|----|----------|------|
| <condition> | <basis> | 38 CFR §3.303; §4.71a | 5237 | file.txt p.2 "..." | <gaps or none> |
Dropped: <condition – reason>
```
