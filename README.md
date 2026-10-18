# va-stack

Skills for a bot that helps veterans **prepare** (not file) VA disability claims using primary-source 38 CFR from eCFR.

Read [SAFETY.md](SAFETY.md) first. Not legal or medical advice.

## Install

### Recommended: let your bot install it

Your assistant bot can install and start va-stack for you with one setup script. You approve each step before it runs. You can run this again any time, to update or to fix a failed install. It won't delete or reset anything, and your saved records and answers stay as they are.

- **What the bot runs**, from the va-stack folder after `git clone https://github.com/IRKillRoyDev/va-stack.git`:
  - macOS and Linux: `bash setup.sh`
  - Windows (PowerShell): `.\setup.ps1`
- **What the script does:** makes a private `.venv` folder inside va-stack (or uses the one from last time), installs va-stack and the programs it needs into that folder (`python -m pip install -e .`), checks that the encryption works, makes your vault folder if it is missing, lets the double-click start files run (macOS and Linux), and opens the VA Stack window. Add `--no-launch` (`-NoLaunch` on Windows) to set up without opening it.
- **What it never touches:** it never opens, reads, moves, overwrites or deletes your vault or your medical records. You choose your files in the app window yourself, and only you type the passphrase.
- **Where your vault lives:** in your own user folder, not inside the va-stack folder, so installing again never touches it. macOS: `~/Library/Application Support/va-stack`. Windows: `%APPDATA%\va-stack`. Linux: `~/.local/share/va-stack` (or `$XDG_DATA_HOME/va-stack`).

Ask your bot: "Install va-stack for me from github.com/IRKillRoyDev/va-stack with its setup script and start it. Ask me before each step."

### Manual install

You need about 10 minutes and an internet connection. Each step has one command: copy it, paste it into the window, and press Enter. Pick your computer below.

#### macOS

Open the **Terminal** app (press Cmd+Space, type `Terminal`, press Enter).

1. Check that Python 3.10 or newer is installed. You can be in any folder.

   ```bash
   python3 --version
   ```

   Success looks like `Python 3.12.7` (3.10 or newer is fine; 3.12 is the version we test). If you see "command not found", install Python from <https://www.python.org/downloads/macos/> and open a new Terminal window.

2. Download va-stack into your home folder.

   ```bash
   cd ~ && git clone https://github.com/IRKillRoyDev/va-stack.git
   ```

   Success looks like `Cloning into 'va-stack'...` and then the prompt again. If macOS asks to install the "command line developer tools", click Install, wait, and run this step again.

3. Go into the va-stack folder. Every step after this one runs in this folder.

   ```bash
   cd ~/va-stack
   ```

   Success looks like no message at all; the prompt now ends in `va-stack`.

4. Make a private folder called `.venv` that holds va-stack and the programs it needs, kept apart from the rest of your computer.

   ```bash
   python3 -m venv .venv
   ```

   Success looks like no message at all.

5. Turn it on. Do this again each time you open a new Terminal window.

   ```bash
   source .venv/bin/activate
   ```

   Success looks like `(.venv)` at the start of the prompt.

6. Install va-stack and what it needs, including the encrypted database driver.

   ```bash
   python -m pip install -e .
   ```

   Success looks like a last line starting with `Successfully installed`.

7. Check that the encryption works.

   ```bash
   python -c "import sqlcipher3; print(sqlcipher3.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0])"
   ```

   Success looks like a version such as `4.19.0 community`. If it prints nothing or an error, stop: the vault will refuse to run without encryption.

**If this fails (macOS):**
- `pip: command not found` or `pip3: command not found`: always use `python -m pip` (inside `(.venv)`) or `python3 -m pip` (outside it) instead of `pip`, as in step 6.
- `Could not find a version that satisfies the requirement sqlcipher3-binary`: your copy is old. In the va-stack folder run `git pull`, then repeat step 6.
- `No module named venv` or `python3` opens an installer: install Python from python.org (not the Xcode copy), open a new Terminal window, and start again at step 3.
- `(.venv)` is gone: you opened a new window. Run step 3 and step 5 again.
- Step 6 shows `Building wheel for sqlcipher3` and then an error: there is no ready-made encryption package for your Python version (there is one for Python 3.10 to 3.14). Use Python 3.12 from python.org, or, only as a last resort, install Homebrew from <https://brew.sh>, run `brew install sqlcipher`, and repeat step 6. Step 7 must still print a version.

#### Windows

Open **PowerShell** (press the Windows key, type `PowerShell`, press Enter).

1. Check that Python 3.10 or newer is installed. You can be in any folder.

   ```powershell
   py --version
   ```

   Success looks like `Python 3.12.7` (3.10 or newer is fine; 3.12 is the version we test). If you see "py is not recognized", install Python from <https://www.python.org/downloads/windows/> (keep "py launcher" ticked) and open a new PowerShell window.

2. Download va-stack into your home folder.

   ```powershell
   cd ~; git clone https://github.com/IRKillRoyDev/va-stack.git
   ```

   Success looks like `Cloning into 'va-stack'...` and then the prompt again. If "git is not recognized", install Git from <https://git-scm.com/download/win>, open a new PowerShell window, and run this step again.

3. Go into the va-stack folder. Every step after this one runs in this folder.

   ```powershell
   cd ~\va-stack
   ```

   Success looks like the prompt now ending in `va-stack>`.

4. Make a private folder called `.venv` that holds va-stack and the programs it needs, kept apart from the rest of your computer.

   ```powershell
   py -m venv .venv
   ```

   Success looks like no message at all.

5. Turn it on. Do this again each time you open a new PowerShell window.

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

   Success looks like `(.venv)` at the start of the prompt.

6. Install va-stack and what it needs, including the encrypted database driver.

   ```powershell
   python -m pip install -e .
   ```

   Success looks like a last line starting with `Successfully installed`.

7. Check that the encryption works.

   ```powershell
   python -c "import sqlcipher3; print(sqlcipher3.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0])"
   ```

   Success looks like a version such as `4.19.0 community`. If it prints nothing or an error, stop: the vault will refuse to run without encryption.

**If this fails (Windows):**
- Step 5 says "running scripts is disabled on this system": run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y`, then repeat step 5.
- `pip is not recognized`: always use `python -m pip` (inside `(.venv)`) or `py -m pip` (outside it) instead of `pip`, as in step 6.
- `Could not find a version that satisfies the requirement`: your copy is old. In the va-stack folder run `git pull`, then repeat step 6.
- `(.venv)` is gone: you opened a new window. Run step 3 and step 5 again.

#### Linux

Open a terminal window.

1. Check that Python 3.10 or newer is installed. You can be in any folder.

   ```bash
   python3 --version
   ```

   Success looks like `Python 3.12.7` (3.10 or newer is fine; 3.12 is the version we test).

2. Download va-stack into your home folder.

   ```bash
   cd ~ && git clone https://github.com/IRKillRoyDev/va-stack.git
   ```

   Success looks like `Cloning into 'va-stack'...` and then the prompt again.

3. Go into the va-stack folder. Every step after this one runs in this folder.

   ```bash
   cd ~/va-stack
   ```

   Success looks like no message at all.

4. Make a private folder called `.venv` that holds va-stack and the programs it needs, kept apart from the rest of your computer.

   ```bash
   python3 -m venv .venv
   ```

   Success looks like no message at all.

5. Turn it on. Do this again each time you open a new terminal window.

   ```bash
   source .venv/bin/activate
   ```

   Success looks like `(.venv)` at the start of the prompt.

6. Install va-stack and what it needs, including the encrypted database driver.

   ```bash
   python -m pip install -e .
   ```

   Success looks like a last line starting with `Successfully installed`.

7. Check that the encryption works.

   ```bash
   python -c "import sqlcipher3; print(sqlcipher3.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0])"
   ```

   Success looks like a version such as `4.12.0 community`. If it prints nothing or an error, stop: the vault will refuse to run without encryption.

**If this fails (Linux):**
- `No module named venv` or `ensurepip is not available` (Debian, Ubuntu): run `sudo apt install python3-venv`, then repeat step 4.
- `pip: command not found`: use `python -m pip` (inside `(.venv)`) or `python3 -m pip` (outside it), as in step 6.
- The review window does not open: run `sudo apt install python3-tk`, or use the text walk-through with `--cli` (below).
- `(.venv)` is gone: you opened a new window. Run step 3 and step 5 again.

### Try it with the sample data (optional)

Before using your own records, try va-stack on the made-up records in the `examples` folder. Stay in the va-stack folder with `(.venv)` showing. These commands are the same on macOS, Windows and Linux.

1. Create a sample vault. Type a practice password twice when asked (nothing shows while you type).

   ```bash
   python scripts/records_vault.py init sample.db
   ```

   Success looks like `{"created": "sample.db"}`.

2. Add the sample records. Type `yes` to the consent question, then the same practice password.

   ```bash
   python scripts/records_vault.py ingest sample.db examples/records --bot-id my-bot
   ```

   Success looks like a line with `"pages_ingested"`.

3. Start the sample review. Type `yes` and the practice password again. Add `--cli` at the end for the text walk-through instead of a window.

   ```bash
   python scripts/proposed_issues.py sample.db examples/sample-input.json --bot-id my-bot --review
   ```

   Success looks like the "1 of 3" review screen. Answer `y` (Yes) or `n` (No) for each row, then type back the short phrase shown on the summary and choose Confirm all. The text walk-through ends with `Saved.`

### Start VA Stack

Stay in the va-stack folder with `(.venv)` showing (run step 3 and step 5 again if you opened a new window), then run:

```bash
va-stack
```

The VA Stack window opens. Tick the consent box, choose a passphrase (or type yours to unlock), click **Choose medical file** to add a PDF or text file, then **Start review**. Your vault is kept in the folder named above. Without a screen, `va-stack --cli` asks the same questions in the terminal. Developers can also double-click `Start VA Stack.command` (macOS) or `Start VA Stack.bat` (Windows), or run `./start-va-stack.sh` (Linux); each one only turns on the private `.venv` folder and runs `va-stack`. If a start file says "Permission denied" or will not open, run `bash setup.sh --no-launch` again; it makes the start files runnable.

## Pipeline

| # | Skill | Purpose |
|---|-------|---------|
| 1 | [cfr-fetch](skills/cfr-fetch/SKILL.md) | Pull current 38 CFR Parts 3, 4, 14 and 20 from the eCFR API, record the "as of" date |
| 2 | [local-search](skills/local-search/SKILL.md) | Run `scripts/local_search.py` on the user's computer (no network, in memory) for capped, redacted snippets; the user approves them before anything goes to the AI service |
| 2b | [records-vault](skills/records-vault/SKILL.md) | Optional: the same search through an encrypted SQLCipher vault on the user's computer, with a consent screen every unlock, bot id on every request, and an HMAC-chained access log |
| 3 | [records-intake](skills/records-intake/SKILL.md) | Index the user's local private records by file, page, date |
| 4 | [condition-map](skills/condition-map/SKILL.md) | Map diagnoses to Part 4 diagnostic codes and Part 3 bases; name what is shown vs missing |
| 5 | [claim-citations](skills/claim-citations/SKILL.md) | Build the cited claim table; drop rows lacking a CFR cite AND a record cite |
| 6 | [related-gaps](skills/related-gaps/SKILL.md) | List related/secondary conditions as "needs more documentation" |
| 6b | [proposed-issues](skills/proposed-issues/SKILL.md) | Full cited report plus a form-mapped proposed-issues list (verified VA form columns), unofficial estimates with gap analysis, and the §4.25 combined estimate |
| 7 | [claim-submission](skills/claim-submission/SKILL.md) | Guide (never perform) the submission process after validating VA.gov, forms, and 38 CFR sources live |

## Plugin

`plugin.json` and `.cursor-plugin/plugin.json` describe the plugin (skills in `./skills/`, agents in `./agents/`). The [va-claims agent](agents/va-claims.md) runs the pipeline. More in [docs/README.md](docs/README.md).

## Local search script

```
python -m pip install -r requirements.txt
python3 scripts/local_search.py "/path/to/records" -t "tinnitus" -t "lumbar" --dry-run
python3 scripts/local_search.py "/path/to/records" -t "tinnitus" --max-hits-per-term 20 --max-total 100 --context 80
python3 scripts/local_search.py "/path/to/records" -t "separation" --keep-dates  # keep exact old dates
```

Runs on the user's own computer, makes no network calls, and writes nothing to disk. Output is redacted JSON for the user to review before sharing.

## Encrypted vault (optional)

```
python -m pip install -r requirements.txt
python3 scripts/records_vault.py init records.db
python3 scripts/records_vault.py ingest records.db "/path/to/records" --bot-id <id>
python3 scripts/records_vault.py search records.db --bot-id <id> -t "tinnitus"
python3 scripts/records_vault.py verify records.db --bot-id <id>
python3 scripts/vault_ui.py records.db --bot-id <id>
```

Each command shows the consent-to-share notice, then prompts for the password locally. `tests/test_vault.py` covers wrong password, tampered or deleted log entries, no consent, consent every session, missing bot id, no snippet text in the log, key zeroized after close, and no network use.

## Development and tests (CI)

`.github/workflows/ci.yml` runs on every push, pull request, and manual dispatch. Its steps:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` (Python 3.12)
3. **Check every claim row has a CFR cite and a record cite:** `python3 tests/check_citations.py`. Validates `tests/fixtures/claims-fake.json` against the fictional `tests/fake-records/`: each CFR cite must be a section in the pinned eCFR list `tests/fixtures/ecfr-sections-2026-10-14.json` (Parts 3, 4, 14, 20), each quote must appear on the cited page, and related rows must be "needs more documentation". Confirms `claims-bad.json` and `claims-bad-fake-section.json` (a made-up §3.999) are rejected.
4. **Validate plugin.json files parse and match:** `python3 tests/check_plugin_json.py`
5. **Check claim-submission track step lists:** `python3 tests/check_submission_tracks.py`. Tracks A1, A2, B1, B2, C present; step 1 validates sources; every step cites a VA.gov URL, a govinfo 10 U.S.C. URL, DoDI 1332.18, or a real 38 CFR section; no step has the bot submit, file, e-file, sign, send, transmit, upload, fax, mail, call, or contact VA. Every step of `submission-bad.json` and `submission-bad-phrasings.json` must be rejected.
6. **Skill frontmatter present:** every `skills/*/SKILL.md` starts with frontmatter containing `name:` and `description:`.
7. **claim-submission guardrails present:** the skill contains "never submits", "Step 0: Ask which track", "Track B2", "Track C", "Track A2", "PEBLO", "§20.202", and "§3.155".
8. **SAFETY.md disclaimer present:** SAFETY.md exists and contains "Not legal or medical advice", "No filing", "VA-accredited", the AI-service processing note, and the Veterans Crisis Line; no Markdown file makes a privacy, compliance, or retention overclaim (phrases listed in `ci.yml`).
9. **Estimate notice present:** SAFETY.md, the proposed-issues skill, and the agent each contain the unofficial-estimate notice, and the skill names the insufficient-evidence output.
10. **Lint and tests** (job `python`): `pip install -r requirements-dev.txt` (pinned pypdf, pytest, ruff, flake8), then `ruff check .`, `ruff format --check .`, `flake8 .`, and `pytest -q`. `tests/test_local_search.py` checks redaction of every identifier kind, the per-term and total caps, `--dry-run` counts-only output, in-memory PDF extraction, and that no socket is opened (socket creation is patched to raise). `tests/test_hardening.py` holds the red-team regression tests (redaction before windowing, SSN separator and invisible-character variants, old dates, addresses, phones, redacted terms and file names, line-split identifiers, and the SAFETY/CI wording checks).


11. **Install and test on macOS and Windows** (jobs `macos` and `windows`, Python 3.12): the same commands as the Manual install above (`python3 -m venv .venv` and `source .venv/bin/activate` on macOS; `py -m venv .venv` and `.venv\Scripts\Activate.ps1` in PowerShell on Windows; then `python -m pip install -e .`, the encryption check and `va-stack --help`), then `python -m pip install -r requirements-dev.txt`, `python -m pytest -v -s tests/test_encryption.py` (the vault opens encrypted: the encryption check prints a version and the file cannot be read without the passphrase) and `python -m pytest -q -rs`. The window tests are skipped there because those test computers have no screen; the `gui-headless` job runs them under Xvfb on Linux.
