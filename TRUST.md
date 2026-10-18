# What VA Stack may touch

This page lists, in plain words, which folders and files the VA Stack
program reads and writes on your computer, what it does over the
internet, and what it does not do. It is based on the program's own code
in this repository. It is not a security promise: read
[SAFETY.md](SAFETY.md) too, and see "Check this program" in the
[README](README.md) for a way to have your own bot check it.

## Folders and files it writes

- **Your vault folder.** This is one folder for your user account, outside
  this repository. The app makes the folder if it is missing (the folder
  only, never the vault file itself):
  - Mac: `~/Library/Application Support/va-stack`
  - Windows: `%APPDATA%\va-stack`
  - Linux: `$XDG_DATA_HOME/va-stack`, or `~/.local/share/va-stack`
- **In that folder:**
  - `vault.db`: your encrypted vault (SQLCipher). It holds the page text of
    the files you import, your review answers, a review you saved for
    later, and the access log. While it saves, SQLite may keep a short-lived
    journal file next to it, which SQLCipher encrypts as well.
  - `vault.db.kdf.json`: the settings needed to turn your passphrase into
    the key (a random salt and the Argon2id settings). It is readable but
    signed, and it does not contain your passphrase.
  - `vault.db.loghead.json`: a signed summary of the access log, so changes
    made without your passphrase can be noticed. It is rewritten through a
    temporary `.tmp` file next to it.
  - `drafts`: draft copies of VA forms, if you make any. Drafts are plain
    PDF files and are **not encrypted**. A new draft never replaces an
    existing file; it gets a new name (`-draft-2.pdf`, `-draft-3.pdf`,
    and so on).
- **A copy of the log summary**, only if you ask for one, at the place you
  choose.
- **This repository's folder, during setup:**
  - `.venv`: the private Python environment the setup script makes.
  - Python's own bookkeeping files (`__pycache__` and
    `scripts/va_stack.egg-info`).
  - pip also keeps its usual download cache in your user account.
- **Screenshots** are written only by the automated tests (to
  `$VA_STACK_SHOTS`, or a `va-stack-shots` folder in your temp folder). The
  app itself does not take screenshots.

## Files it reads

- **The medical file you pick** with "Choose medical file" (a PDF or a
  text file). The app reads it and copies its page text into the vault. The
  file itself is not changed, moved or deleted.
- **A form PDF you choose** for a draft. It is only read; the program checks
  that it is the same before and after reading.
- **A records folder you name**, when you or your bot run
  `scripts/local_search.py`. It reads PDF and text files there and prints
  capped, redacted snippets. It writes nothing.
- **Its own reference files** in this repository (form and rating tables).
  Each is checked against a fixed fingerprint before use.

## Network

What the code shows, checked by searching every script for network modules
(`socket`, `ssl`, `http`, `urllib`, `requests`, `httpx`, `aiohttp`,
`websocket`, `ftplib`, `smtplib`, `subprocess` and similar):

- **The program's scripts** (everything in `scripts/`) import no network
  module. The only related import is `urllib.parse`, which checks the text
  of a web address in a citation; it does not connect to anything. The
  windows use the Qt parts for windows and PDF pages only, not Qt's network
  parts. An automated test fails if a network module is ever added.
- **Setup uses the internet once:** `setup.sh` and `setup.ps1` run
  `pip install -e .`, which downloads the packages listed in
  `pyproject.toml` (such as SQLCipher, Argon2, pypdf, reportlab and
  PySide6) from PyPI, the Python package index. The setup and start
  scripts download nothing else.
- **A maintainer tool,** `tools/build_reference_fixtures.py`, downloads
  public reference data (VA form PDFs and 38 CFR text). The app and setup
  never run it.
- **Your AI bot is a separate program.** If you use VA Stack with an AI
  bot, the text the bot reads is sent to the AI service it runs on, and
  some skills tell the bot to fetch public pages (such as eCFR or VA.gov).
  See [SAFETY.md](SAFETY.md).

This scan covers this repository's code. It does not cover what Python,
Qt, the other packages or your computer's system do on their own.

## Blank forms for your AI bot

For a form without fillable fields, a later version can ask your AI bot
where each answer goes on the page. The code for this is in
`scripts/field_map.py`; it is not used by any window yet. When it is used:

- **It shows you what will leave, and gives it to your AI bot only after you
  answer yes.** Nothing leaves before that, and a no leaves nothing.
- **What leaves:** a sanitized copy of the blank form, its form number and
  title from the program's own list of VA forms, its page count and page
  sizes, the SHA-256 hash of that exact copy, and the names of your data
  points, never their values (for example `ssn`, never the number).
- **The copy is cleaned first:** filled-in answers, metadata (such as the
  author or title), earlier versions saved inside the file, scripts,
  attachments, comments and links are removed. The copy is checked again
  after it is made, and it is refused if anything is left.
- **Flat forms:** if the form has no fillable fields, you are also asked "Is
  this form blank, with nothing of yours typed or filled in?" The program
  checks the page text on your computer and warns if it looks filled in, or
  if a page has no text to check (a scan; offline OCR is not built yet).
  Such a warning also needs your answer to that question. Text that differs
  from the bundled blank form only warns.
- **Your data-point values** stay in the encrypted vault. The access log
  keeps only counts and keyed hashes of the names, never the names or values.

This is checked by `tests/test_field_map_send.py` and
`tests/test_vault_data_points.py`.

## What it never does

- It never submits, files, signs or sends a claim or form to VA or anyone
  else. Drafts are for you to check and finish by hand.
- It never gives your AI bot a blank form or your data-point names without
  asking you first, and it never gives it your data-point values.
- It never changes your original record files. Corrections you type do not
  overwrite the original page text stored in the vault.
- It never takes your passphrase from the command line or an environment
  variable, and it never saves, prints or logs it.
- The setup scripts never read, move, overwrite or delete your vault or
  record files.
- Its access log never stores medical or record text, your typed corrections or
  form answers. Each entry holds only the kind of action, the time, ids, counts,
  page references, Yes or No answers and flag names, and keyed hashes (HMACs) or
  fingerprints. The names of imported files and forms are logged too, with names
  and numbers hidden, so keep medical details out of file names. This is checked
  by `tests/test_log_no_medical_text.py`.

## Limits

- Draft PDFs are not encrypted. Keep them somewhere safe and delete them
  when you are done.
- Redaction (hiding names and numbers) is pattern-based and can miss
  things. Always read what you share.
- Anyone who knows your passphrase can open the vault. Use full-disk
  encryption, and close the program when you are done.
