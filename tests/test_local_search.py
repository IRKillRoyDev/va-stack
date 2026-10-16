"""Tests for scripts/local_search.py using synthetic records only."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "local_search", ROOT / "scripts" / "local_search.py"
)
local_search = importlib.util.module_from_spec(_SPEC)
sys.modules["local_search"] = local_search
_SPEC.loader.exec_module(local_search)

FAKE = ROOT / "tests" / "fake-records"
IDENTIFIERS = [
    "000-12-3456",
    "000123456",
    "01/02/1980",
    "12345678",
    "1234567890",
    "010-0199",
    "010-0123",
    "pat.example@example.invalid",
    "123 Example Street",
]


def make_pdf(path: Path, pages: list[str]) -> None:
    """Write a minimal one-font PDF with one text line per page (test only).

    When to use: to build a synthetic PDF fixture in a temp folder.
    """
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_id = len(objs)
        objs.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(out)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any socket creation fail so a network call breaks the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_socket_guard_active():
    with pytest.raises(AssertionError):
        socket.socket()


def test_redaction_of_all_identifier_kinds():
    result = local_search.search(
        FAKE, ["Patient", "VA file", "Phone", "Address", "tinnitus"]
    )
    blob = json.dumps(result["hits"])
    for ident in IDENTIFIERS:
        assert ident not in blob, ident
    for kind in ["SSN", "DOB", "VA_FILE", "DOD_ID", "PHONE", "EMAIL", "ADDRESS"]:
        assert f"[REDACTED-{kind}]" in blob, kind
    assert "tinnitus" in blob


def test_redact_unit():
    text, counts = local_search.redact("call 555-010-0100 or SSN 000-11-2222")
    assert "555" not in text and "2222" not in text
    assert counts == {"SSN": 1, "PHONE": 1}


def test_hits_have_location_fields():
    hit = local_search.search(FAKE, ["sick call"])["hits"][0]
    assert hit["file"] == "str-fake.txt"
    assert hit["page"] == 2 and hit["line"] == 1
    assert set(hit) == {"file", "page", "line", "term", "context"}


def test_per_term_cap():
    result = local_search.search(FAKE, ["tinnitus"], max_hits_per_term=2)
    assert result["hits_returned"] == 2
    assert result["matches_found"]["tinnitus"] > 2
    assert result["truncated"] is True


def test_total_cap():
    result = local_search.search(FAKE, ["tinnitus", "strain", "fictional"], max_total=3)
    assert result["hits_returned"] == 3
    assert result["truncated"] is True


def test_context_window_limits_output():
    hit = local_search.search(FAKE, ["lumbosacral"], context=5)["hits"][0]
    assert len(hit["context"]) < 40


def test_pdf_extraction_in_memory(tmp_path):
    make_pdf(tmp_path / "fake.pdf", ["FICTIONAL page one", "tinnitus DOB 03/04/1975"])
    before = sorted(p.name for p in tmp_path.iterdir())
    result = local_search.search(tmp_path, ["tinnitus"])
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    hit = result["hits"][0]
    assert hit["file"] == "fake.pdf" and hit["page"] == 2
    assert "1975" not in hit["context"]
    assert "[REDACTED-DOB]" in hit["context"]


def test_dry_run_outputs_counts_only(capsys):
    assert local_search.main([str(FAKE), "-t", "tinnitus", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert "hits" not in out
    assert out["hits_returned"] >= 1
    assert "tinnitus" not in json.dumps(out["hits_per_file"])


def test_cli_rejects_zero_cap():
    with pytest.raises(SystemExit):
        local_search.main([str(FAKE), "-t", "x", "--max-total", "0"])
