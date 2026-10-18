"""Round 6 red-team regressions (fake data only). Written to fail first."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake-records"
BOT = "test-bot-fake"
PW = b"correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
INPUT = json.loads(
    (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rv = sys.modules["records_vault"]
ls = rv.local_search


def gate():
    g = rv.ConsentGate()
    g.acknowledge()
    return g


def make(tmp_path, bots=None, **kw):
    db = tmp_path / "v.db"
    kw.setdefault("kdf_params", FAST)
    kw.setdefault("insecure_test_kdf", True)
    rv.create_vault(db, bytearray(PW), bots=bots, **kw)
    return db


def edit_header(db, change):
    path = rv.header_path(db)
    header = json.loads(path.read_text())
    change(header)
    path.write_text(json.dumps(header))


@pytest.fixture
def vault(tmp_path):
    db = make(tmp_path)
    v = rv.Vault.open(db, bytearray(PW), gate())
    v.ingest_folder(FAKE, bot_id=BOT)
    yield v
    v.close()


# ---- 1. signed KDF header ---------------------------------------------
def test_header_bots_edit_fails_closed(tmp_path):
    db = make(tmp_path, bots=["good-bot"])
    edit_header(db, lambda h: h["bots"].append("evil-bot"))
    with pytest.raises(rv.VaultError, match="header"):
        rv.Vault.open(db, bytearray(PW), gate())


def test_header_unsigned_fails_closed(tmp_path):
    db = make(tmp_path)
    edit_header(db, lambda h: h.pop("mac"))
    with pytest.raises(rv.VaultError, match="header"):
        rv.Vault.open(db, bytearray(PW), gate())


def test_header_insecure_flag_cannot_be_added(tmp_path):
    db = make(tmp_path, kdf_params=dict(rv.MIN_KDF), insecure_test_kdf=False)
    edit_header(db, lambda h: h.update(insecure_test_kdf=True, time_cost=1))
    with pytest.raises(rv.VaultError):
        rv.Vault.open(db, bytearray(PW), gate())


def test_open_enforces_min_kdf(tmp_path):
    db = make(tmp_path, kdf_params=dict(rv.MIN_KDF), insecure_test_kdf=False)
    edit_header(db, lambda h: h.update(time_cost=1))
    with pytest.raises(rv.VaultError, match="minimum"):
        rv.Vault.open(db, bytearray(PW), gate())


def test_registered_bots_still_enforced(tmp_path):
    db = make(tmp_path, bots=["good-bot"])
    with rv.Vault.open(db, bytearray(PW), gate()) as v:
        v.search(["knee"], bot_id="good-bot")
        with pytest.raises(rv.BotIdRequired):
            v.search(["knee"], bot_id="evil-bot")


# ---- 5. consent state not importable ----------------------------------
def test_gate_state_not_module_sets():
    for name in ("_ACKED", "_SPENT", "_LIVE", "_TOKENS"):
        assert not hasattr(rv, name), name


def test_vault_cannot_be_built_without_open(tmp_path):
    for token in (None, object()):
        with pytest.raises(rv.ConsentRequired):
            rv.Vault(None, bytearray(32), token, tmp_path / "x.db", {})


def test_gate_attribute_forgery_rejected(tmp_path):
    db = make(tmp_path)
    g = rv.ConsentGate()
    with pytest.raises((AttributeError, rv.ConsentRequired)):
        g.acknowledged = True
        rv.Vault.open(db, bytearray(PW), g)
    g2 = gate()
    rv.Vault.open(db, bytearray(PW), g2).close()
    g2.acknowledge()
    with pytest.raises(rv.ConsentRequired):
        rv.Vault.open(db, bytearray(PW), g2)


# ---- 6. exported head --------------------------------------------------
def test_exported_head_catches_joint_rollback(tmp_path):
    db = make(tmp_path)
    head = rv.head_path(db)
    keep = tmp_path / "elsewhere" / "head.json"
    keep.parent.mkdir()
    with rv.Vault.open(db, bytearray(PW), gate()) as v:
        v.search(["knee"], bot_id=BOT)
    snap = {p: p.read_bytes() for p in (db, head)}
    with rv.Vault.open(db, bytearray(PW), gate()) as v:
        v.search(["strain"], bot_id=BOT)
        v.export_head(keep)
        assert v.verify_log(external_head=keep)
    for p, data in snap.items():
        p.write_bytes(data)
    with rv.Vault.open(db, bytearray(PW), gate()) as v:
        assert v.verify_log()  # documented limit
        assert not v.verify_log(external_head=keep)


def test_safety_states_password_holder_limit():
    text = " ".join((ROOT / "SAFETY.md").read_text().split())
    assert "anyone with the password can rewrite the whole access log" in text
    assert "export_head" in text


# ---- 2/3. pinned manifest, criteria checks, forms on build path -------
def test_manifest_pins_both_files():
    assert {"forms.json", "rating-criteria.json"} <= set(pi.MANIFEST)
    pi.check_manifest()


@pytest.mark.parametrize("name", ["forms.json", "rating-criteria.json"])
def test_tampered_json_refused(tmp_path, monkeypatch, vault, name):
    skill = tmp_path / "skill"
    shutil.copytree(pi.SKILL_DIR, skill)
    data = json.loads((skill / name).read_text())
    data["tampered"] = True
    (skill / name).write_text(json.dumps(data))
    monkeypatch.setattr(pi, "SKILL_DIR", skill)
    with pytest.raises(ValueError, match="manifest"):
        pi.build_report(INPUT, vault, bot_id=BOT)


def test_criteria_cites_checked_against_fixture():
    assert pi.check_criteria(pi.load_criteria()) == []
    crit = copy.deepcopy(pi.load_criteria())
    crit["dcs"]["7806"]["factors"][0]["bands"][2]["cite"] = "Invented 60 percent text"
    assert pi.check_criteria(crit)


@pytest.mark.parametrize(
    "cite",
    [
        "lesions involving 20 to 40 percent of the entire body",
        "Characteristic lesions involving 20 to 40",
        "favorable ankylosis of the entire spine",
    ],
)
def test_criteria_cherry_picked_phrase_rejected(cite):
    crit = copy.deepcopy(pi.load_criteria())
    dc = "5237" if "ankylosis" in cite else "7806"
    crit["dcs"][dc]["factors"][0]["bands"][0]["cite"] = cite
    assert pi.check_criteria(crit)


def test_unknown_dc_is_insufficient(vault):
    data = copy.deepcopy(INPUT)
    data["issues"][0]["dc"] = "9999"
    row = pi.build_report(data, vault, bot_id=BOT)["cited_report"][0]
    assert row["estimate"] == pi.INSUFFICIENT
    assert row["notice"] == pi.ESTIMATE_NOTICE


def test_build_report_runs_verify_forms(monkeypatch, vault):
    monkeypatch.setattr(pi, "verify_forms", lambda forms=None: ["x is UNVERIFIED"])
    with pytest.raises(ValueError, match="UNVERIFIED"):
        pi.build_report(INPUT, vault, bot_id=BOT)


# ---- 4. quotes -----------------------------------------------------------
@pytest.mark.parametrize(
    "quote",
    ["", "   ", "20 percent", "about 20 percent of", "CHARACTERISTIC LESIONS ON"],
)
def test_weak_quotes_rejected(vault, quote):
    data = copy.deepcopy(INPUT)
    data["issues"][0]["evidence"][0]["quote"] = quote
    with pytest.raises(ValueError):
        pi.build_report(data, vault, bot_id=BOT)


# ---- MED/LOW -------------------------------------------------------------
@pytest.mark.parametrize(
    "text,secret",
    [
        ("date of birth\n\n\n\nThursday the fourteenth of March, 1961", "1961"),
        ("Born: Mar. 14 '61", "'61"),
        ("Lives in Austin TX 78701 with spouse", "78701"),
    ],
)
def test_redaction_gaps(text, secret):
    assert secret not in ls.redact(text, keep_dates=True)[0]


def test_ref_is_part_label():
    # A part label still means "not a phone"; the 10-digit number is hidden
    # (round 18 tags it as an identifier next to "ref"; any non-phone tag).
    out, counts = ls.redact("ref 555-123-4567", keep_dates=True)
    assert out.startswith("ref [REDACTED-") and "PHONE" not in counts, out
    assert "4567" not in out


@pytest.mark.parametrize(
    "phrase",
    [
        "zero\u00a0retention",
        "HIPAA compliance certified",
        "zero-data retention",
        "zero data-retention",
        "fully-private vault",
        "100 percent private",
        "de_identified data",
        "records remain on-device",
        "data never leaves the appliance",
    ],
)
def test_overclaim_scan_round6(tmp_path, phrase):
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    line = next(ln for ln in ci.splitlines() if "OVERCLAIM=" in ln)
    pattern = line.split("OVERCLAIM=", 1)[1].strip().strip('"')
    (tmp_path / "tests").mkdir()
    shutil.copy(ROOT / "tests" / "overclaim_scan.sh", tmp_path / "tests")
    (tmp_path / "doc.md").write_text(f"Fake product page: {phrase}.\n")

    def run(*a):
        return subprocess.run(a, cwd=tmp_path, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "add", "-A")
    assert run("bash", "tests/overclaim_scan.sh", pattern).returncode == 1


@pytest.mark.parametrize("bad", ["03/14/1961", "01-1899", "01-2099", "13-2004"])
def test_form_rows_check_dates(bad):
    issue = dict(INPUT["issues"][0], approx_date=bad)
    with pytest.raises(ValueError):
        pi._form_rows("21-526EZ", [issue])


@pytest.mark.parametrize(
    "bad", ["03/14/1961", "2024-13-01", "02-30-2024", "01-01-2099"]
)
def test_decision_date_validated(vault, bad):
    data = copy.deepcopy(INPUT)
    data["track"] = "B2-supplemental"
    data["issues"][0]["decision_date"] = bad
    with pytest.raises(ValueError):
        pi.build_report(data, vault, bot_id=BOT)


def test_decision_date_ok(vault):
    data = copy.deepcopy(INPUT)
    data["track"] = "B2-supplemental"
    for issue in data["issues"]:
        issue["decision_date"] = "05-01-2025"
    rows = pi.build_report(data, vault, bot_id=BOT)["form"]["rows"]
    assert rows[0]["21B. DATE OF VA DECISION NOTICE"] == "05-01-2025"


def test_estimate_carries_notice():
    dc = pi.load_criteria()["dcs"]["7806"]
    est = pi.estimate(dc, {"body_area_percent": 20})
    assert est["estimate"] == 30 and est["notice"] == pi.ESTIMATE_NOTICE
    est = pi.estimate(dc, {})
    assert est["estimate"] == pi.INSUFFICIENT and est["notice"] == pi.ESTIMATE_NOTICE
