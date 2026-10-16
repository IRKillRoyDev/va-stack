#!/usr/bin/env bash
# Double-click on macOS (developer fallback): open VA Stack from .venv.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# shellcheck disable=SC1091
source .venv/bin/activate
exec va-stack "$@"
