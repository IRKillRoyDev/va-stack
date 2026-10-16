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
  tests/test_round5.py
  tests/test_round6.py
  tests/test_vault.py
)
mapfile -t files < <(git ls-files | grep -Fxv -f <(printf '%s\n' "${skip[@]}"))
# Normalize dashes and runs of spaces first so "zero–retention" or
# "records stay  local" cannot slip past the phrase list.
found=0
for f in "${files[@]}"; do
  grep -qI . "$f" 2>/dev/null || continue
  # Unicode spaces (NBSP, thin, narrow NBSP, ideographic) become plain
  # spaces and underscores become hyphens before matching. A failed
  # normalization fails the scan instead of silently matching nothing.
  if ! text=$(sed -e 's/[              　]/ /g' -e 's/_/-/g' \
      -e 's/[‐‑‒–—―−]/-/g' -e 's/[[:space:]][[:space:]]*/ /g' "$f"); then
    echo "normalization failed for $f"
    exit 2
  fi
  if printf '%s\n' "$text" | grep -niIE -- "$pattern" | sed "s|^|$f:|"; then
    found=1
  fi
done
if [ "$found" = 1 ]; then
  echo "privacy overclaim found"
  exit 1
fi
echo "no privacy overclaims"
