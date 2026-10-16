---
name: records-intake
description: Ingest a veteran's medical and service records only from a local private folder the user names, index them by file, page and date, and extract diagnoses, symptoms, service dates, nexus statements and treatment. Store nothing outside that folder; redact outputs.
---

# records-intake

## Steps

1. Ask the user for the absolute path of a local private folder. Accept records **only** from that folder. Refuse records pasted into chat, attached to issues, or located inside any repository; ask the user to save them to the private folder instead.
2. Confirm the folder is not inside a git working tree and is not synced to a public location. If it is, stop and tell the user.
3. List files (PDF, text, images). For PDFs use text extraction per page; for scans use local OCR only. Never send records to an external service.
4. Build an index, saved inside the same private folder as `_index.json`: one entry per file per page with `file`, `page`, `date` (document date as written; `unknown` if absent), and `doc_type` (STR, VA treatment, private treatment, DD-214, nexus letter, DBQ, other).
5. For each page extract, with an exact quote for each item:
   - diagnoses (as written, plus ICD code if printed)
   - symptoms and severity findings (range of motion, frequency, etc.)
   - service dates, branch, deployments/locations (for presumptive analysis)
   - in-service events, injuries, exposures
   - nexus statements (any "at least as likely as not" type opinion), with author and date
   - treatment and medications
6. Mark anything illegible or ambiguous as `unclear` with file and page. Do not infer.
7. Save extractions to `_extract.json` in the private folder.

## Stop rules

- No folder given, folder unreadable, or folder in a repo/public sync → stop.
- Never write records, extractions, or quotes to any location outside the private folder (no repo, no gist, no chat logs beyond what the user needs).
- Never guess dates or diagnoses. Unknown stays unknown.

## Redaction

In every output, redact: SSN, VA file/claim number, DOB (keep year only if age matters), addresses, phone numbers, email, names of providers' other patients or family members, and any condition the user has said to exclude. Use `[REDACTED]`.

## Output template

```
Records folder: <path> (local, private)
Files: <n>  Pages: <n>  Unclear pages: <list>
Service: <branch>, <start>–<end>, locations: <list>   (cite file p.#)
| File | Page | Date | Type | Item | Quote |
|------|------|------|------|------|-------|
```
