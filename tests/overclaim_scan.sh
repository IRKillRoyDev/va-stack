#!/usr/bin/env bash
# Fail if any tracked text file makes a privacy overclaim.
# Usage: bash tests/overclaim_scan.sh "<extended regex>"
# Only these exact paths are skipped, because they hold the phrase list itself.
set -euo pipefail
pattern="$1"
skip=(
  .github/workflows/ci.yml
  tests/overclaim_scan.sh
  tests/test_hardening.py
  tests/test_round3.py
  tests/test_round4.py
)
mapfile -t files < <(git ls-files | grep -Fxv -f <(printf '%s\n' "${skip[@]}"))
if grep -niIE -- "$pattern" "${files[@]}"; then
  echo "privacy overclaim found"
  exit 1
fi
echo "no privacy overclaims"
