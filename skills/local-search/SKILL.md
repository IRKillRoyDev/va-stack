---
name: local-search
description: Search the user's local records folder on their own computer with scripts/local_search.py (no network, in-memory only), return redacted snippets, and get the user's approval of the snippet list before any record text goes to the bot's AI service.
---

# local-search

Use this before records-intake and condition-map read any record text.

## Why

The bot's AI service processes any text the bot reads. This skill keeps that to the smallest set of snippets the user has approved. The script itself runs on the user's computer, makes no network calls, and writes nothing to disk.

## Steps

1. Ask the user to install the pinned dependency once on their computer: `pip install -r requirements.txt`.
2. Ask the user to run, on their computer, a dry run with the search terms the next skill needs (diagnoses, symptoms, dates, "at least as likely as not", unit or location names):
   `python3 scripts/local_search.py "<folder>" -t "<term>" -t "<term>" --dry-run`
   It prints counts only (files searched, matches per term, hits per file). No record text.
3. Show the counts. Agree on terms and caps (`--max-hits-per-term`, default 20; `--max-total`, default 100; `--context`, default 80 characters each side).
4. Ask the user to run the same command without `--dry-run`. Output is JSON: `file`, `page`, `line`, `term`, `context`. SSNs, dates of birth, phone numbers, emails, street addresses, VA file numbers and DoD ID numbers are replaced with `[REDACTED-<KIND>]` before printing.
5. The user reviews the snippet list and removes anything they do not want shared. Only the snippets the user approves may be pasted to the bot.
6. A whole page may be read only if the user explicitly approves that specific file and page, after being told its text goes to the AI service.

## Stop rules

- No approval of the snippet list → stop; read nothing.
- Never read a whole file or page without explicit approval for that page.
- Redaction is pattern-based and can miss things. Ask the user to check every snippet before approving it.
- Never run the script on a folder inside a git repository or a public sync folder.

## Output template

```
Folder: <path> (searched on the user's computer; nothing sent yet)
Dry run: files <n>, matches <term: n, ...>, truncated: <yes/no>
Approved snippets (user-reviewed):
| File | Page | Line | Term | Context |
|------|------|------|------|---------|
Whole pages approved: <none | file p.#>
```
