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
  tests/test_round7.py
  tests/test_vault.py
)
mapfile -t files < <(git ls-files | grep -Fxv -f <(printf '%s\n' "${skip[@]}"))
# Normalize dashes and runs of spaces first so "zero–retention" or
# "records stay  local" cannot slip past the phrase list.
found=0
# Normalize before matching: NFKC (fullwidth and compatibility forms),
# drop zero-width characters and soft hyphens, Unicode dashes become "-",
# underscores become "-", and all whitespace including line breaks becomes
# one space, so a phrase split across lines is still caught. A failed
# normalization fails the scan instead of silently matching nothing.
normalize='
import re, sys, unicodedata
t = unicodedata.normalize("NFKC", open(sys.argv[1], encoding="utf-8", errors="replace").read())
t = re.sub("[\u200b-\u200d\u2060\ufeff\u00ad\u180e]", "", t)
t = re.sub("[\u2010-\u2015\u2212]", "-", t).replace("_", "-")
print(re.sub(r"\s+", " ", t))
'
found=0
for f in "${files[@]}"; do
  grep -qI . "$f" 2>/dev/null || continue
  if ! text=$(python3 -c "$normalize" "$f"); then
    echo "normalization failed for $f"
    exit 2
  fi
  if hits=$(printf '%s\n' "$text" | grep -oiE -- "$pattern"); then
    printf '%s\n' "$hits" | sed "s|^|$f: |"
    found=1
  fi
done
if [ "$found" = 1 ]; then
  echo "privacy overclaim found"
  exit 1
fi
echo "no privacy overclaims"
