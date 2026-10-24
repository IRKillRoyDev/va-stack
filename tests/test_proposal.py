"""Sterilized sentences in, estimated proposal stored in the vault."""

from __future__ import annotations

import importlib.util
import json
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
rv = sys.modules["records_vault"]


@pytest.fixture
def vault(tmp_path):
    db = tmp_path / "r.db"
    rv.create_vault(db, bytearray(b"pw fake"), kdf_params=FAST, insecure_test_kdf=True)
    gate = rv.ConsentGate()
    gate.acknowledge()
    opened = rv.Vault.open(db, bytearray(b"pw fake"), gate)
    opened.ingest_folder(FAKE, bot_id=BOT)
    yield opened
    opened.close()


def test_sterilize_hides_identifiers_and_stores_the_packet(vault):
    packet = vault.sterilize(["tinnitus"], bot_id=BOT)
    blob = json.dumps(packet)
    assert packet["id"]
    assert "000-12-3456" not in blob and "000123456" not in blob
    assert "[REDACTED-SSN]" in blob
    assert any("tinnitus" in item["sentence"].lower() for item in packet["sentences"])
    stored = vault.latest_sterile(bot_id=BOT)
    assert stored["id"] == packet["id"]
    assert stored["sentences"] == packet["sentences"]
    log = json.dumps(vault.read_log())
    assert "recurrent ringing" not in log


def test_proposal_stores_the_estimate_from_those_sentences(vault):
    packet = vault.sterilize(["ringing"], bot_id=BOT)
    sentence = next(
        item["sentence"]
        for item in packet["sentences"]
        if "recurrent ringing" in item["sentence"]
    )
    data = {
        "track": "B1",
        "issues": [
            {
                "condition": "Tinnitus",
                "basis": "direct",
                "approx_date": "03-2003",
                "dc": "6260",
                "findings": {"tinnitus": "recurrent"},
                "evidence": [
                    {"file": "va-clinic-fake.txt", "page": 3, "quote": sentence}
                ],
            }
        ],
    }
    report = pi.save_proposal(data, vault, BOT)
    assert report["cited_report"][0]["estimate"] == 10
    assert report["proposal_id"]
    stored = vault.get_proposal(bot_id=BOT)
    assert stored["id"] == report["proposal_id"]
    assert stored["sterile_id"] == packet["id"]
    assert stored["report"]["cited_report"][0]["estimate"] == 10
    assert "recurrent ringing" not in json.dumps(vault.read_log())


def test_proposal_refuses_a_quote_the_program_did_not_sterilize(vault):
    vault.sterilize(["ringing"], bot_id=BOT)
    data = {
        "track": "B1",
        "issues": [
            {
                "condition": "Tinnitus",
                "basis": "direct",
                "approx_date": "03-2003",
                "dc": "6260",
                "findings": {},
                "evidence": [
                    {
                        "file": "va-clinic-fake.txt",
                        "page": 3,
                        "quote": "The examiner invented a sentence about ringing.",
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="sterilized packet"):
        pi.save_proposal(data, vault, BOT)
    assert vault.get_proposal(bot_id=BOT) is None
