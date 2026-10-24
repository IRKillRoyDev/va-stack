---
name: condition-map
description: Map each diagnosis from records-intake to its 38 CFR Part 4 diagnostic code and its Part 3 basis (direct, presumptive §3.307/§3.309, secondary §3.310, aggravation §3.306), stating what the records show versus what is missing. Any unofficial estimate comes only from proposed-issues, with its notice.
---

# condition-map

## PII gate

Do not read record files, vault page text, PDF bytes, or `raw_sentence`. Read only sterilized sentences, stored diagnosis sentences, and Missing Diagnosis text the program already wrote. Do not paste record text into chat, commits, issues, or pull requests. Names without a label are not redacted. If a sentence still shows a name, stop and ask the user to remove it before anything is sent.

Requires: a successful `cfr-fetch` session and `records-intake` extractions.

Run [local-search](../local-search/SKILL.md) first for any extra evidence terms (diagnosis names, onset dates, nexus wording). Use only snippets the user approved; whole pages only with explicit user approval.

## Steps

1. Take each diagnosis from `_extract.json`. One row per diagnosis.
2. Find the diagnostic code by searching the fetched Part 4 text (e.g. §4.71a musculoskeletal, §4.130 mental disorders, §4.87 ear, §4.97 respiratory). Record the DC and the section it sits in. If no exact DC exists, record whether the fetched text provides for rating by analogy (§4.20) and quote it; otherwise mark `DC not found`.
3. Evaluate each possible Part 3 basis against the fetched text:
   - **Direct** (§3.303): current diagnosis + in-service event/injury + nexus.
   - **Presumptive** (§3.307 / §3.309, and other presumptive sections present in the fetched text): listed disease + qualifying service/exposure + manifestation within the stated period. Quote the list entry.
   - **Secondary** (§3.310): current diagnosis + a service-connected (or claimed) primary condition + evidence the primary caused or aggravated it.
   - **Aggravation** (§3.306): condition noted at entry + increase in severity during service.
4. For each basis, fill three elements with `shown` (with file p.# "quote") or `missing`: current diagnosis, in-service event (or presumptive qualifier / primary condition), nexus (or presumption).
5. Quote the rating schedule criteria for the DC verbatim. Any estimate is made only by [proposed-issues](../proposed-issues/SKILL.md), from structured factors, with this notice: Unofficial estimate, not a VA decision. It is likely to differ after the VA exam (C&P exam). Confirm with a VA-accredited representative.

## Stop rules

- No cfr-fetch for this session → stop and run cfr-fetch.
- Never assign a DC or basis without quoting the fetched CFR text it rests on.
- Never mark an element `shown` without a record cite. If unsure, it is `missing`.
- No "likely to be granted" statements. Percentage estimates come only from proposed-issues and carry its notice.

## Output template

```
As of: <eCFR date>
| Diagnosis | DC (section) | Basis | Current dx | In-service / qualifier | Nexus | CFR quote |
|-----------|--------------|-------|------------|------------------------|-------|-----------|
| <dx> | DC 5237 (§4.71a) | Direct §3.303 | shown: file p.# "..." | shown/missing | shown/missing | "..." |
Schedule criteria (quoted): <quote>
```
