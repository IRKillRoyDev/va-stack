"""Confidence matches, agreement, selection, and PDF highlight. Fake data."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake-records"
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
match = _load("criteria_match")
review_mod = _load("criteria_review")
view = _load("page_view")
rv = sys.modules["records_vault"]


def _pages():
    clinic = (FAKE / "va-clinic-fake.txt").read_text().split("=== PAGE 3 ===", 1)[1]
    skin = (FAKE / "derm-fake.txt").read_text().split("=== PAGE 1 ===", 1)[1]
    return [
        {"file": "va-clinic-fake.txt", "page": 3, "text": clinic},
        {"file": "derm-fake.txt", "page": 1, "text": skin},
    ]


def _catalog():
    return pi.schedule_catalog.parse_schedule(pi.load_ecfr())


def test_confidence_ranks_schedule_wording():
    found = match.match_pages(_pages(), _catalog(), pi.load_criteria())
    ringing = next(item for item in found if "recurrent ringing" in item["sentence"])
    assert ringing["dc"] == "6260"
    assert ringing["confidence"] >= 50
    assert ringing["level"] in {"medium", "high"}
    skin = next(
        item for item in found if "20 percent of the entire body" in item["sentence"]
    )
    assert skin["dc"] == "7806"
    assert skin["confidence"] >= 50
    assert skin["level"] in {"medium", "high"}
    assert "000-12-3456" not in skin["sentence"]


def test_selection_requires_agreement_and_builds_input():
    review = review_mod.CriteriaReview(
        [
            {
                "candidate_id": "a",
                "file": "note.txt",
                "page": 1,
                "sentence": "Patient reports recurrent ringing in both ears.",
                "raw_sentence": "Patient reports recurrent ringing in both ears.",
                "dc": "6260",
                "criterion": "Tinnitus, recurrent",
                "confidence": 50,
                "level": "medium",
            },
            {
                "candidate_id": "b",
                "file": "note.txt",
                "page": 1,
                "sentence": "Mild ache with no measured finding on this page.",
                "raw_sentence": "Mild ache with no measured finding on this page.",
                "dc": "5237",
                "criterion": "forward flexion",
                "confidence": 20,
                "level": "low",
            },
        ]
    )
    assert [item["candidate_id"] for item in review.visible()] == ["a"]
    with pytest.raises(ValueError, match="agree"):
        review.set_selected("a", True)
    review.agree("a")
    review.set_selected("a", True)
    stated = {
        "candidate_id": "c",
        "file": "note.txt",
        "page": 1,
        "sentence": "Diagnosis: tinnitus, bilateral.",
        "raw_sentence": "Diagnosis: tinnitus, bilateral.",
        "dc": "6260",
        "criterion": "Tinnitus, recurrent",
        "confidence": 100,
        "level": "high",
        "clear_diagnosis": True,
    }
    review.candidates.append(stated)
    assert [item["candidate_id"] for item in review.visible()] == ["a"]
    assert [item["candidate_id"] for item in review.for_sanitization()] == ["a"]
    assert review.to_input()["issues"] == []
    assert not review_mod.clearly_diagnosis(
        "Patient reports recurrent ringing in both ears.", "Tinnitus, recurrent"
    )
    assert review_mod.clearly_diagnosis(
        "Diagnosis: tinnitus, bilateral.", "Tinnitus, recurrent"
    )


def test_pdf_pane_highlights_the_sentence():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 120), "Patient reports recurrent ringing in both ears.")
    data = doc.tobytes()
    doc.close()
    png, found = view.render_pdf_highlight(
        data, 1, "Patient reports recurrent ringing in both ears."
    )
    assert found is True
    assert png.startswith(b"\x89PNG")
    png, found = view.render_pdf_highlight(data, 1, "this sentence is not on the page")
    assert found is False
    assert png.startswith(b"\x89PNG")


def test_agreed_selection_is_stored_as_a_proposal(tmp_path):
    db = tmp_path / "r.db"
    rv.create_vault(db, bytearray(b"pw fake"), kdf_params=FAST, insecure_test_kdf=True)
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(b"pw fake"), gate)
    try:
        vault.ingest_folder(FAKE, bot_id=BOT)
        kind, data = vault.get_original("va-clinic-fake.txt", bot_id=BOT)
        assert kind == "text" and b"recurrent ringing" in data
        found = match.match_pages(
            vault.record_pages(BOT), _catalog(), pi.load_criteria()
        )
        review = review_mod.CriteriaReview(found)
        review.set_min_level("low")
        item = next(
            row for row in review.visible() if "recurrent ringing" in row["sentence"]
        )
        added = vault.store_diagnoses(
            [
                row
                for row in found
                if match.clearly_diagnosis(row["sentence"], row["criterion"])
            ],
            BOT,
        )
        assert added >= 1
        stored_dx = vault.list_diagnoses(BOT)
        assert any("tinnitus" in row["sentence"].lower() for row in stored_dx)
        assert all("ringing" not in row["sentence"].lower() for row in stored_dx)
        missing = match.missing_diagnoses(found)
        vault.store_missing_diagnoses(missing, BOT)
        assert all(row["dc"] != "6260" for row in missing)
        assert pi.PUSHY.search(" ".join(row["needed"] for row in missing)) is None
        assert vault.list_missing_diagnoses(BOT) == [
            {
                "dc": row["dc"],
                "finding_count": row["finding_count"],
                "needed": row["needed"],
            }
            for row in missing
        ]
        review.agree(item["candidate_id"])
        review.set_selected(item["candidate_id"], True)
        assert item in review.for_sanitization()
        assert vault.enqueue_for_sanitization(review.for_sanitization(), BOT) == 1
        packet = vault.run_sanitization(BOT)
        assert any("ringing" in row["sentence"] for row in packet["sentences"])
        logged = __import__("json").dumps(vault.read_log())
        assert "recurrent ringing" not in logged
    finally:
        vault.close()
