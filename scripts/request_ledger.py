"""The local request ledger: which field-map request was sent for which form.

When a field-map request is released (field_map.PreparedRequest.release),
one line is appended to "form requests.jsonl" in the app data folder:
{"form": <sha256 of the original form bytes>, "request": <request id>}. The
request id is the payload's own form.sha256, the hash of the exact sanitized
copy that is sent; the map answer already carries it as form_sha256, so
binding adds nothing to what leaves the machine. The latest line for a form
is its stored entry, so a map that answers an older request is refused.

The file holds hashes only (two 64-character hex strings per line), never a
name, value or path. It is appended to, never rewritten. Nothing here sends
anything. When to use: field_map records on release; form_convert looks up
the stored entry before converting.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

LEDGER_NAME = "form requests.jsonl"
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _load(name):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


app_paths = _load("app_paths")
safe_path = _load("safe_path")


def _check(value, what):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{what}: not a sha256 hex digest")
    return value


class Ledger:
    """The ledger file; path defaults to the app data folder."""

    def __init__(self, path=None):
        self.path = (
            Path(path) if path is not None else app_paths.data_dir() / LEDGER_NAME
        )

    def record(self, original_sha256: str, request_id: str) -> None:
        """Append one entry. Both values must be sha256 hex digests."""
        line = json.dumps(
            {
                "form": _check(original_sha256, "form"),
                "request": _check(request_id, "request"),
            },
            sort_keys=True,
        )
        try:  # pinned folder; O_APPEND | O_NOFOLLOW via dir fd, fsync'd
            root = safe_path.pin_root(self.path.parent)
            safe_path.append_text(root, self.path.name, line + "\n")
        except safe_path.SafePathError as exc:
            raise ValueError(f"ledger path is not allowed: {exc}") from exc

    def request_id(self, original_sha256: str) -> str | None:
        """The stored (latest) request id for this form, or None."""
        if not isinstance(original_sha256, str) or not _HEX64.fullmatch(
            original_sha256
        ):
            return None
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        found = None
        for line in text.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or set(entry) != {"form", "request"}:
                continue
            form, request = entry["form"], entry["request"]
            if not all(
                isinstance(v, str) and _HEX64.fullmatch(v) for v in (form, request)
            ):
                continue
            if form == original_sha256:
                found = request
        return found
