"""Step B 3: the master-data model behind the tabs (model only, no window).

master_data.MasterData groups every catalog data point by its catalog
category (the tab order is the catalog's), adds, edits and deletes values
(delete needs confirm=True), and masks the values of sensitive points
(the existing identifier_blocklist.blocked_name() flags them) unless the
caller asks to reveal one. A *linked* data point is stored once and shown
under more tabs: the vault's link table says where else it shows, so an
edit in one place shows in every place. Values live in the encrypted
vault; the access log keeps keyed hashes and counts only. Fake data only.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load("records_vault")
fm = _load("field_map")


@pytest.fixture(scope="module")
def md():
    path = ROOT / "scripts" / "master_data.py"
    assert path.exists(), "scripts/master_data.py is missing"
    return _load("master_data")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _pw():
    return bytearray(b"correct horse fake")


def _gate():
    gate = rv.ConsentGate()
    gate.acknowledge()
    return gate


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    rv.create_vault(path, _pw(), kdf_params=FAST, insecure_test_kdf=True)
    return path


@pytest.fixture
def vault(db):
    v = rv.Vault.open(db, _pw(), _gate())
    yield v
    v.close()


@pytest.fixture
def model(md, vault):
    return md.MasterData(vault, bot_id=BOT)


def _entry(tabs, category, name):
    return next(e for e in tabs[category] if e.name == name)


def test_tabs_follow_the_catalog_categories_in_order(model):
    tabs = model.tabs()
    assert list(tabs) == list(fm.CATEGORIES)
    for name, info in fm.CATALOG.items():
        homes = [
            c for c, entries in tabs.items() if any(e.name == name for e in entries)
        ]
        assert homes == [info["category"]], name


def test_add_edit_and_show(model):
    model.add("condition", "Tinnitus quorvane fake")
    e = _entry(model.tabs(), "claim", "condition")
    assert e.has_value and e.display == "Tinnitus quorvane fake" and not e.masked
    model.edit("condition", "Knee strain fake")
    assert _entry(model.tabs(), "claim", "condition").display == "Knee strain fake"


def test_add_refuses_an_existing_value_and_edit_a_missing_one(md, model):
    model.add("condition", "Tinnitus quorvane fake")
    with pytest.raises(md.MasterDataError):
        model.add("condition", "other")
    with pytest.raises(md.MasterDataError):
        model.edit("email", "fake.user@mail.example.test")


@pytest.mark.parametrize("name", ["social_security_number", "SSN", "", None])
def test_only_catalog_names(md, model, name):
    with pytest.raises((md.MasterDataError, rv.VaultError)):
        model.add(name, "x-fake")


def test_delete_needs_an_explicit_confirm(md, model):
    model.add("condition", "Tinnitus quorvane fake")
    for confirm in (False, "yes", 1, None):
        with pytest.raises(md.MasterDataError):
            model.delete("condition", confirm=confirm)
        assert _entry(model.tabs(), "claim", "condition").has_value
    with pytest.raises(TypeError):
        model.delete("condition")  # no default: the flag must be given
    model.delete("condition", confirm=True)
    e = _entry(model.tabs(), "claim", "condition")
    assert not e.has_value and e.display == ""


def test_delete_of_a_missing_value_is_refused(md, model):
    with pytest.raises(md.MasterDataError):
        model.delete("condition", confirm=True)


@pytest.mark.parametrize(
    "name,value,category",
    [
        ("ssn", "123-45-6789", "identity"),
        ("bank_account_number", "000123456789", "banking"),
        ("email", "fake.user@mail.example.test", "contact"),
        ("date_of_birth", "01/02/1970", "identity"),
    ],
)
def test_sensitive_values_are_masked_by_default(md, model, name, value, category):
    model.add(name, value)
    e = _entry(model.tabs(), category, name)
    assert e.sensitive and e.masked and value not in e.display
    assert e.display == md.MASK
    shown = _entry(model.tabs(reveal={name}), category, name)
    assert shown.display == value and not shown.masked
    assert model.reveal(name) == value


def test_sensitive_means_the_existing_blocklist_flags_the_name(md, model):
    ib = sys.modules["identifier_blocklist"]
    for name in fm.CATALOG:
        assert md.is_sensitive(name) is (ib.blocked_name(name) is not None), name


def test_non_sensitive_values_show(model):
    model.add("benefit", "COMPENSATION")
    e = _entry(model.tabs(), "claim", "benefit")
    assert not e.sensitive and e.display == "COMPENSATION"


def test_linked_point_is_stored_once_and_shows_in_every_tab(model, vault):
    model.add("mailing_address", "1 Fake St, Faketown")
    model.link("mailing_address", "housing")
    model.link("mailing_address", "banking")
    tabs = model.tabs(reveal={"mailing_address"})
    places = [
        c for c, es in tabs.items() if any(e.name == "mailing_address" for e in es)
    ]
    assert places == ["contact", "housing", "banking"]
    assert _entry(tabs, "housing", "mailing_address").linked is True
    assert _entry(tabs, "contact", "mailing_address").linked is False
    model.edit("mailing_address", "2 Fake Ave, Faketown")
    tabs = model.tabs(reveal={"mailing_address"})
    for cat in places:
        assert _entry(tabs, cat, "mailing_address").display == "2 Fake Ave, Faketown"
    rows = vault._conn.execute(
        "SELECT count(*) FROM data_points WHERE name = 'mailing_address'"
    ).fetchone()
    assert rows[0] == 1


def test_unlink_removes_only_the_extra_place(model):
    model.add("phone", "555-0100")
    model.link("phone", "housing")
    model.unlink("phone", "housing")
    tabs = model.tabs()
    assert not any(e.name == "phone" for e in tabs["housing"])
    assert _entry(tabs, "contact", "phone").has_value


@pytest.mark.parametrize("category", ["contact", "nowhere", "", None])
def test_link_needs_another_catalog_category(md, model, category):
    model.add("phone", "555-0100")
    with pytest.raises((md.MasterDataError, rv.VaultError)):
        model.link("phone", category)  # contact is phone's own tab


def test_links_survive_reopening_and_delete_keeps_the_link_rows_out(md, db, vault):
    m = md.MasterData(vault, bot_id=BOT)
    m.add("phone", "555-0100")
    m.link("phone", "housing")
    vault.close()
    with rv.Vault.open(db, _pw(), _gate()) as again:
        m2 = md.MasterData(again, bot_id=BOT)
        assert any(e.name == "phone" for e in m2.tabs()["housing"])
        m2.delete("phone", confirm=True)
        assert not any(e.name == "phone" for e in m2.tabs()["housing"])


def test_log_holds_keyed_hashes_and_counts_only(md, db, vault):
    m = md.MasterData(vault, bot_id=BOT)
    m.add("mailing_address", "1 Fake St, Faketown")
    m.link("mailing_address", "housing")
    m.edit("mailing_address", "2 Fake Ave, Faketown")
    m.reveal("mailing_address")
    m.tabs()
    m.unlink("mailing_address", "housing")
    m.delete("mailing_address", confirm=True)
    rows = vault._conn.execute("SELECT * FROM access_log").fetchall()
    text = json.dumps(rows) + json.dumps(vault.read_log())
    for secret in ("1 Fake St", "2 Fake Ave", "mailing_address", "housing"):
        assert secret not in text, secret
    actions = {e["action"] for e in vault.read_log()}
    assert {
        "data_point_set",
        "data_point_read",
        "data_point_values",
        "data_point_linked",
        "data_point_unlinked",
        "data_point_deleted",
    } <= actions


def test_values_and_links_stay_encrypted(md, db, vault):
    m = md.MasterData(vault, bot_id=BOT)
    m.add("mailing_address", "1 Fake St, Faketown")
    m.link("mailing_address", "housing")
    vault.close()
    raw = db.read_bytes()
    assert b"Fake St" not in raw and b"data_point_links" not in raw


def test_model_has_no_window_code():
    src = (ROOT / "scripts" / "master_data.py").read_text(encoding="utf-8")
    assert "PySide6" not in src and "QtWidgets" not in src
