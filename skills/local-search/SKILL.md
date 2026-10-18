---
name: local-search
description: Search the user's local records folder on their own computer with scripts/local_search.py (no network, in-memory only), return redacted snippets, and get the user's approval of the snippet list before any record text goes to the bot's AI service.
---

# local-search

Use this before records-intake and condition-map read any record text.

If the user keeps records in the encrypted vault, run the same search through [records-vault](../records-vault/SKILL.md) instead; redaction and caps are the same.

## Why

The bot's AI service processes any text the bot reads. This skill keeps that to the smallest set of snippets the user has approved. The script itself runs on the user's computer, makes no network calls, and writes nothing to disk.

## Steps

1. Ask the user to install the pinned dependency once on their computer: `pip install -r requirements.txt`.
2. Ask the user to run, on their computer, a dry run with the search terms the next skill needs (diagnoses, symptoms, dates, "at least as likely as not", unit or location names):
   `python3 scripts/local_search.py "<folder>" -t "<term>" -t "<term>" --dry-run`
   It prints counts only (files searched, matches per term, hits per file). No record text.
3. Show the counts. Agree on terms and caps (`--max-hits-per-term`, default 20; `--max-total`, default 100; `--context`, default 80 characters each side).
4. Ask the user to run the same command without `--dry-run`. Output is JSON: `file`, `page`, `line`, `term`, `context`. Before printing, pattern-based redaction replaces these with `[REDACTED-<KIND>]` (labels such as `DOB:` are kept): SSNs; any date that starts within about 25 characters after a birth label (DOB, D.O.B., D-O-B, birth, birthday, birth date, born, or "b." right before a date), in every mode; emails; street addresses, PO boxes and rural routes; state + ZIP after a city or street; VA file numbers; DoD ID numbers; military service numbers (labeled, or two capital letters and 8 digits such as RA12345678); labeled bank routing and account numbers; and phones that are labeled (tel, phone, ph, call, cell, mobile, fax) or written as a full 10-digit number with separators. Each whole page is redacted before it is searched, and search terms and file names in the output are redacted too. Full dates 17 or more years old are shown as `[DATE-YYYY]`; `--keep-dates` turns only that off.
5. The user reviews the snippet list and removes anything they do not want shared. Only the snippets the user approves may be pasted to the bot.
6. A whole page may be read only if the user explicitly approves that specific file and page, after being told its text goes to the AI service.

## Stop rules

- No approval of the snippet list → stop; read nothing.
- Never read a whole file or page without explicit approval for that page.
- Redaction is pattern-based. It can miss identifiers and it can over-redact ordinary numbers. Known gaps: names without a label (including third parties) are not redacted (see below); a bare year such as "born in 1961" is not redacted; an unlabeled phone like "555 010 0199" or an unlabeled 7-digit phone is not redacted; and a minor's (child's) date of birth with no birth label is not redacted, because recent dates are kept. The user reviews every snippet before approving it.
- Names are redacted only when labeled, matching `scripts/local_search.py` and SAFETY.md. Labels (any case, ALL CAPS included): Name, Patient, Pt, Veteran, Claimant, Spouse, Dependent, Beneficiary, Examiner, Provider, Signed / Signed by, compound labels such as "Patient's name", "Name of veteran" and "Last/First/Middle/Full name", and the titles Mr, Mrs, Ms, Mx and Dr. Separators: colon, dash, en/em dash, fullwidth colon, "=", ".", "|", "is", a line break, or plain space (after a plain space, "is", or a title with no period, the name must be capitalized). Letters are Unicode letters (accented names included); up to five words are taken, including initials, particles such as de, la, le, du, di, da, del, della, dos, van, von, der, den, ter, bin, al and St., Jr./Sr./II/III and Mc/Mac/O'/hyphenated names. A negation or common word right after a label ("Not", "denies", "it") is never taken as a name, and a capitalized surname after it is still redacted. Limits: names without a label are not redacted, including third-party names, and a lowercase name is taken only when the first word after the label is lowercase (up to two words). The review's step (c), names we missed, is the backstop.
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
