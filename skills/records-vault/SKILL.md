---
name: records-vault
description: Keep the user's records in an encrypted database on their own computer (SQLCipher, key derived from their password with Argon2id), unlock it only after a consent-to-share screen every session, require the requesting bot id on every request, and keep an HMAC-chained access log inside the encrypted file.
---

# records-vault

Use this when the user wants records encrypted at rest between sessions. It wraps [local-search](../local-search/SKILL.md): searches run through the vault with the same redaction and caps.

## Steps

1. The user installs the pinned packages on their computer: `pip install -r requirements.txt` (pypdf, sqlcipher3-binary, argon2-cffi).
2. Create the vault once: `python3 scripts/records_vault.py init "<db>"`. The password is typed at a local prompt, twice. It is never accepted as an argument or environment variable, and never printed or logged.
3. Every session starts with the consent-to-share screen: the Unlock window (`python3 scripts/vault_ui.py "<db>" --bot-id <id>`) or the CLI. The password field is disabled until the user ticks consent. Consent is asked every session and never remembered. If the user declines, the vault stays locked (CLI exit code 3).
4. Ingest: `python3 scripts/records_vault.py ingest "<db>" "<folder>" --bot-id <id>`.
5. Search: `python3 scripts/records_vault.py search "<db>" --bot-id <id> -t "<term>"`. Output is the same redacted JSON as local-search. The user approves the snippet list before anything goes to the AI service.
6. Log each approved snippet with `Vault.record_sent()` (file, page, line, keyed HMAC-SHA256 of the text, no text). Use `Vault.approve_page()` for a whole page, and only after the user explicitly approves that page.
7. Check the log: `python3 scripts/records_vault.py verify "<db>" --bot-id <id>`. Read it: `... log "<db>" --bot-id <id>`.
8. End the session: the vault closes, the key buffer is overwritten with zeros, and the process exits.

## Stop rules

- No consent this session → stop; the vault stays locked.
- No bot id → the request is refused.
- Wrong password → stop; never retry by passing the password any other way.
- Log verification fails → tell the user the access log was changed and stop.

## Files

- `<db>`: the encrypted database (records and access log).
- `<db>.kdf.json`: unencrypted header with the Argon2id salt and parameters. These are not secret, but without them the vault cannot be opened, so back up both files together.
