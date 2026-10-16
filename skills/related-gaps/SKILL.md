---
name: related-gaps
description: Identify related or secondary conditions (via §3.310 and links documented in the fetched CFR or in the records themselves) and list the added documentation needed, such as a nexus letter or DBQ. Every row is labeled "needs more documentation", never a claim.
---

# related-gaps

## Steps

1. Start from claim-citations rows (the possible primary conditions).
2. Find candidate related conditions only from:
   - the fetched CFR text (e.g. §3.310, notes inside Part 4 that direct separate evaluation of associated conditions, §3.309 lists), quoted; or
   - the records themselves: a symptom, finding, or provider statement linking one condition to another (file p.# "quote").
   General knowledge alone is not enough to list a candidate.
3. For each candidate, state what documentation is missing: current diagnosis, nexus opinion (medical opinion addressing causation or aggravation by the primary), DBQ, treatment records, lay/buddy statement, service records.
4. Label every row `needs more documentation`.

## Stop rules

- Never present a row as a claim or as claimable.
- No candidate without a CFR quote or a record quote supporting the link.
- No medical speculation; describe only what the source says.

## Output template

```
| Related condition | Linked to | Link source (CFR § or file p.# "quote") | Documentation needed | Status |
|-------------------|-----------|------------------------------------------|----------------------|--------|
| <condition> | <primary> | ... | nexus letter; DBQ | needs more documentation |
```
