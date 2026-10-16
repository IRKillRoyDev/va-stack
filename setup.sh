#!/usr/bin/env bash
# VA Stack setup and launch for Linux and macOS. Safe to run again.
#   bash setup.sh              # set up, then open the app
#   bash setup.sh --no-launch  # set up only (or VA_STACK_NO_LAUNCH=1)
# It creates or reuses .venv in this folder, installs va-stack into it,
# checks the encryption, makes the per-user vault folder, and starts
# va-stack. It never reads, moves, overwrites or deletes the vault or any
# record file.
set -euo pipefail

launch=1
if [ "${VA_STACK_NO_LAUNCH:-0}" = "1" ]; then
  launch=0
fi
for arg in "$@"; do
  case "$arg" in
    --no-launch) launch=0 ;;
    *) echo "unknown option: $arg (use --no-launch)" >&2; exit 2 ;;
  esac
done

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

echo "Step 1 of 5: Python virtual environment (.venv)"
if [ -x .venv/bin/python ]; then
  echo "  reusing .venv"
else
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Step 2 of 5: install va-stack and its packages"
python -m pip install -e .

echo "Step 3 of 5: check the encryption (SQLCipher)"
version=$(python -c "import sqlcipher3; print(sqlcipher3.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0])")
if [ -z "$version" ]; then
  echo "  no encryption found; stopping (the vault will not run without it)" >&2
  exit 1
fi
echo "  SQLCipher $version"

echo "Step 4 of 5: your vault folder (an existing vault is left as is)"
python -c "import app_paths; p = app_paths.default_vault_path(); print('  vault:', p, '(exists)' if p.exists() else '(will be created in the app)')"

echo "Step 5 of 5: launchers"
chmod +x start-va-stack.sh "Start VA Stack.command"

if [ "$launch" = "1" ]; then
  exec va-stack
fi
echo "Setup done. Start the app with: source .venv/bin/activate && va-stack"
