---
name: records-intake
description: Ingest a veteran's medical and service records only from a local private folder the user names, index them by file, page and date, and extract diagnoses, symptoms, service dates, nexus statements and treatment. The skill writes and copies nothing outside that folder (the text it reads is processed by the bot's AI service); redact outputs.
---

# records-intake

Requires: an approved snippet list from [local-search](../local-search/SKILL.md). Work from those snippets first.

## Steps

1. Ask the user for the absolute path of a local private folder (their own folder for the record files). Accept records **only** from that folder. Refuse records pasted into chat, attached to issues, or located inside any repository; ask the user to save them to the private folder instead.
2. Confirm the folder is not inside a git working tree and is not synced to a public location. If it is, stop and tell the user.
3. Run [local-search](../local-search/SKILL.md) first: the user runs `scripts/local_search.py` on their computer, reviews the redacted snippets, and approves the list. Read only approved snippets. Read a whole page only when the user explicitly approves that page. For scans with no text layer, the user runs local OCR first. Do not upload or copy the files anywhere. Tell the user once, before reading: the files stay in their folder, but the text the bot reads is processed by the AI service it runs on; a fully offline setup needs a local model.
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
8. If any page is a mental-health record, include the Veterans Crisis Line line in the output (see template).

## Stop rules

- No approved local-search snippet list → stop. Whole pages only with explicit user approval per page.
- No folder given, folder unreadable, or folder in a repo/public sync → stop.
- Never write record files, extractions, or quotes to any location outside the user's folder (no repo, no gist), and quote in chat only what the user needs.
- Never guess dates or diagnoses. Unknown stays unknown.

## Redaction

In every output, redact: SSN, VA file/claim number, DOB (keep year only if age matters), addresses, phone numbers, email, names of providers' other patients or family members, and any condition the user has said to exclude. Use `[REDACTED]`.

## Output template

```
Records folder: <path> (files stay in this folder; text read is processed by the bot's AI service)
Files: <n>  Pages: <n>  Unclear pages: <list>
Service: <branch>, <start>–<end>, locations: <list>   (cite file p.#)
| File | Page | Date | Type | Item | Quote |
|------|------|------|------|------|-------|
If any mental-health records are present (e.g. conditions rated under §4.130), add:
Veterans Crisis Line: dial 988, then press 1 (verify at https://www.veteranscrisisline.net/).
```
