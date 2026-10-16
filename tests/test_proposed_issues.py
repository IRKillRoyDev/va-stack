"""Tests for form-mapped proposed issues and unofficial rating estimates. Fake data."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake-records"
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
INPUT = json.loads(
    (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
)
ECFR = json.loads(
    (ROOT / "tests/fixtures/ecfr-part4-ratings-2026-10-14.json").read_text()
)
PUSHY = re.compile(
    r"worsen|to increase your rating|get more|raise your rating|higher rating by"
    r"|boost|maximi[sz]e|exaggerat|so you (?:can )?qualify|for the rating",
    re.IGNORECASE,
)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rv = sys.modules["records_vault"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any socket creation fail so a network call breaks the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def vault(tmp_path):
    db = tmp_path / "r.db"
    rv.create_vault(db, bytearray(b"pw fake"), kdf_params=FAST, insecure_test_kdf=True)
    gate = rv.ConsentGate()
    gate.acknowledge()
    v = rv.Vault.open(db, bytearray(b"pw fake"), gate)
    v.ingest_folder(FAKE, bot_id=BOT)
    yield v
    v.close()


@pytest.fixture
def report(vault):
    return pi.build_report(INPUT, vault, bot_id=BOT)


# ---- forms -------------------------------------------------------------
def test_every_form_column_verified_against_cached_pdf():
    assert pi.verify_forms() == []


def test_unverified_column_fails():
    forms = copy.deepcopy(pi.load_forms())
    forms["forms"]["21-526EZ"]["columns"][0]["status"] = "UNVERIFIED"
    assert any("UNVERIFIED" in p for p in pi.verify_forms(forms))
    forms = copy.deepcopy(pi.load_forms())
    forms["forms"]["21-526EZ"]["columns"][0]["fields"].append("F[0].Invented[0]")
    assert any("not in PDF" in p for p in pi.verify_forms(forms))
    forms = copy.deepcopy(pi.load_forms())
    forms["forms"]["20-0995"]["columns"][0]["label"] = "21A. MADE UP LABEL"
    assert any("label" in p for p in pi.verify_forms(forms))


def test_each_track_maps_to_its_form():
    tracks = pi.load_forms()["tracks"]
    assert tracks["A1"] == tracks["B1"] == tracks["B2-increase"] == "21-526EZ"
    assert tracks["B2-supplemental"] == "20-0995"
    assert tracks["B2-hlr"] == "20-0996"
    assert tracks["B2-board"] == "10182"
    assert tracks["C-dic"] == "21P-534EZ"
    assert tracks["intent-to-file"] == "21-0966"
    assert tracks["A2"] is None


def test_form_rows_use_only_verified_labels(report):
    form = pi.load_forms()["forms"]["21-526EZ"]
    labels = {c["label"] for c in form["columns"]}
    assert report["form"]["form"] == "21-526EZ"
    assert report["form"]["revision"] == "JAN 2026"
    for row in report["form"]["rows"]:
        assert set(row) == labels
    assert report["form"]["rows"][0]["CURRENT DISABILITY(IES)"].startswith("Eczema")


# ---- report structure --------------------------------------------------
def test_full_cited_report_kept_alongside_form(report):
    assert report["cited_report"] and report["form"]["rows"]
    assert len(report["cited_report"]) == len(INPUT["issues"])


def test_every_row_has_a_b_c_and_notice(report):
    for row in report["cited_report"]:
        assert row["links"], row["condition"]
        for link in row["links"]:
            assert re.fullmatch(r"vault://[0-9a-f]{16}#page=\d+", link["link"])
            assert ".txt" not in link["link"] and "/" not in link["link"][8:]
        assert row["rating_wording"]["section"] and row["rating_wording"]["dc"]
        assert row["rating_wording"]["ecfr_url"].startswith("https://www.ecfr.gov/")
        assert "gap" in row
        assert row["notice"] == pi.ESTIMATE_NOTICE
    assert report["combined"]["notice"] == pi.ESTIMATE_NOTICE


def test_notice_wording():
    n = pi.ESTIMATE_NOTICE.lower()
    for part in ("unofficial", "not a va decision", "c&p", "accredited representative"):
        assert part in n


def test_estimates_cite_part4_and_record(report):
    for row in report["cited_report"]:
        if isinstance(row["estimate"], int):
            quotes = row["rating_wording"]["quotes"]
            assert quotes
            text = ECFR["sections"][row["rating_wording"]["section"]]["text"]
            for q in quotes:
                assert q in text
            assert row["links"]


def test_insufficient_evidence_path(report):
    spine = [r for r in report["cited_report"] if r["dc"] == "5237"][0]
    assert spine["estimate"] == "insufficient evidence to estimate"
    statuses = {f["status"] for f in spine["gap"]["factors"]}
    assert statuses == {"not documented"}


def test_eczema_treatment_gap(report):
    skin = [r for r in report["cited_report"] if r["dc"] == "7806"][0]
    assert skin["estimate"] == 30
    gap = skin["gap"]
    assert gap["next_level"] == 60
    by_key = {f["factor"]: f for f in gap["factors"]}
    assert by_key["treatment"]["status"] == "not met"
    assert (
        "ask your provider whether other treatment options"
        in (by_key["treatment"]["question"])
    )
    assert by_key["body_area_percent"]["status"] == "not met"
    assert by_key["exposed_area_percent"]["status"] == "not documented"


def test_factor_status_and_cites_match_fixture(report):
    for row in report["cited_report"]:
        gap = row["gap"]
        if gap.get("at_max"):
            continue
        text = ECFR["sections"][row["rating_wording"]["section"]]["text"]
        for f in gap["factors"]:
            assert f["status"] in {"met", "not met", "not documented"}
            assert f["cite"] in text, f["cite"]


def test_max_level_case(report):
    tin = [r for r in report["cited_report"] if r["dc"] == "6260"][0]
    assert tin["estimate"] == 10
    assert tin["gap"]["at_max"] is True
    assert "highest" in tin["gap"]["text"].lower()


def test_gap_text_is_never_pushy(report):
    blob = json.dumps([r["gap"] for r in report["cited_report"]])
    assert not PUSHY.search(blob), PUSHY.search(blob)


def test_pushy_guard_rejects_bad_text():
    with pytest.raises(ValueError):
        pi.check_gap_text("Worsen symptoms to increase your rating")


# ---- combined ratings (§4.25 table) ------------------------------------
@pytest.mark.parametrize(
    "ratings,exact,final",
    [
        ([50, 30], 65, 70),
        ([40, 20], 52, 50),
        ([60, 40, 20], 81, 80),
        ([60, 30], 72, 70),
        ([10, 10], 19, 20),
        ([30], 30, 30),
    ],
)
def test_combined_matches_table(ratings, exact, final):
    res = pi.combine(ratings)
    assert res["combined_value"] == exact and res["final"] == final


def test_table_lookup_cached_from_ecfr():
    table = pi.load_table()
    assert table[(60, 30)] == 72 and table[(76, 20)] == 81 and table[(50, 30)] == 65


def test_bilateral_case_from_4_26_example():
    res = pi.combine([60, 20], bilateral=[10, 10])
    assert res["bilateral_value"] == 21
    assert res["combined_value"] == 74 and res["final"] == 70


def test_report_combined_block(report):
    comb = report["combined"]
    assert comb["ratings"] == [30, 10]
    assert comb["final"] == 40
    assert "§4.25" in comb["cite"] and comb["as_of"] == "2026-10-16"
    assert "1 -" in comb["formula"]


# ---- vault links -------------------------------------------------------
def test_link_open_is_logged_and_redacted(vault):
    link = vault.link_for("identifiers-fake.txt", 1)
    assert "identifiers" not in link
    text = vault.open_link(link, bot_id=BOT)
    assert "000-12-3456" not in text and "[REDACTED-SSN]" in text
    entry = vault.read_log()[-1]
    assert entry["action"] == "link_opened" and entry["bot_id"] == BOT
    assert entry["detail"] == {"record_id": link[8:24], "page": 1}
    with pytest.raises(rv.BotIdRequired):
        vault.open_link(link, bot_id="")


def test_quotes_in_report_are_redacted(vault):
    data = copy.deepcopy(INPUT)
    data["issues"][1]["evidence"].append(
        {"file": "identifiers-fake.txt", "page": 1, "quote": "SSN 000-12-3456"}
    )
    rep = pi.build_report(data, vault, bot_id=BOT)
    assert "000-12-3456" not in json.dumps(rep)


# ---- audit additions ---------------------------------------------------
def test_evidence_quotes_appear_in_record_page(vault, report):
    for row in report["cited_report"]:
        for link in row["links"]:
            page = vault.open_link(link["link"], bot_id=BOT)
            assert " ".join(link["quote"].split()) in " ".join(page.split())


def test_invented_quote_is_rejected(vault):
    data = copy.deepcopy(INPUT)
    data["issues"][0]["evidence"][0]["quote"] = "lesions on 80 percent of the body"
    with pytest.raises(ValueError):
        pi.build_report(data, vault, bot_id=BOT)


def test_dc_wording_matches_fixture(report):
    for row in report["cited_report"]:
        text = ECFR["sections"][row["rating_wording"]["section"]]["text"]
        assert row["rating_wording"]["dc_cite"] in text
        assert row["rating_wording"]["as_of"] == ECFR["as_of"]
        assert row["rating_wording"]["api_url"].startswith(
            "https://www.ecfr.gov/api/versioner/v1/full/2026-10-14/title-38.xml"
        )


@pytest.mark.parametrize(
    "text",
    [
        "Your symptoms may worsen.",
        "Do this to increase your rating.",
        "Try to get more visits.",
        "This can raise your rating.",
        "You could get a higher rating by changing treatment.",
    ],
)
def test_pushy_guard_rejects_each_phrase(text):
    with pytest.raises(ValueError):
        pi.check_gap_text(text)


@pytest.mark.parametrize("bad", ["2004-06-14", "06/14/2004", "June 2004", "13-2004"])
def test_approx_date_must_be_month_year(vault, bad):
    data = copy.deepcopy(INPUT)
    data["issues"][0]["approx_date"] = bad
    with pytest.raises(ValueError):
        pi.build_report(data, vault, bot_id=BOT)


def test_skill_documents_every_form_and_date_procedure():
    skill = (ROOT / "skills/proposed-issues/SKILL.md").read_text()
    forms = pi.load_forms()
    for name, f in forms["forms"].items():
        row = next(ln for ln in skill.splitlines() if ln.startswith(f"| {name} |"))
        for part in (f["revision"], f["vagov_url"], f["pdf_url"], forms["as_of"]):
            assert part in row
        for col in f["columns"]:
            assert col["label"] in row and col["fields"][0] in row
    for part in ("--keep-dates", "birth label", "every mode", "MM-YYYY", "confirmed"):
        assert part in skill
    assert pi.ESTIMATE_NOTICE in skill and pi.INSUFFICIENT in skill
