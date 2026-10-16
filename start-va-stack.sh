#!/usr/bin/env bash
# Developer fallback: open VA Stack from this folder's .venv.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# shellcheck disable=SC1091
source .venv/bin/activate
exec va-stack "$@"
