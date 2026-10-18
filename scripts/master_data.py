"""The master-data model behind the tabs: data points by catalog category.

Model only; no window is built here. Every catalog data point
(schemas/data_points.json) shows under its own category's tab, in the
catalog's order. A *linked* point is stored once in the vault and shown under
more tabs: the vault's data_point_links table says where else it shows, so an
edit in one place shows in every place. Values of sensitive points (those
identifier_blocklist.blocked_name() flags: SSN, date of birth, addresses,
phone, email, service and bank numbers) are masked unless the caller asks to
reveal one. Delete needs confirm=True.

Values live in the encrypted records vault; every vault call logs keyed
hashes and counts only. There is no network code here.

When to use: behind the master data window (a later step), or from tests.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path


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


field_map = _load("field_map")
identifier_blocklist = _load("identifier_blocklist")

MASK = "\u2022" * 8


class MasterDataError(ValueError):
    """A master-data change was refused. Messages never hold a value."""


def is_sensitive(name: str) -> bool:
    """True when the existing blocklist flags this data-point name."""
    return identifier_blocklist.blocked_name(name) is not None


@dataclasses.dataclass(frozen=True)
class Entry:
    """One data point as one tab shows it."""

    name: str
    category: str  # its own (catalog) category
    type: str
    has_value: bool
    display: str  # the value, MASK, or "" when it holds none
    masked: bool
    sensitive: bool
    linked: bool  # shown here because of a link, not its own tab


class MasterData:
    """Group, add, edit, delete and link data points in an open vault."""

    def __init__(self, vault, *, bot_id: str):
        vault.check_open()
        self._vault = vault
        self._bot_id = bot_id

    def _name(self, name) -> str:
        if type(name) is not str or name not in field_map.CATALOG:
            raise MasterDataError("not a catalog data-point name")
        return name

    def tabs(self, *, reveal=frozenset()) -> dict[str, list[Entry]]:
        """{category: [Entry, ...]} in catalog order; links add places.

        Sensitive values show as MASK unless their name is in reveal.
        """
        values = self._vault.data_point_values(field_map.CATALOG, self._bot_id)
        extra: dict[str, list[str]] = {c: [] for c in field_map.CATEGORIES}
        for name, category in self._vault.data_point_links(self._bot_id):
            extra[category].append(name)
        out: dict[str, list[Entry]] = {c: [] for c in field_map.CATEGORIES}
        for name, info in field_map.CATALOG.items():
            out[info["category"]].append(self._entry(name, values, reveal, False))
        for category, names in extra.items():
            for name in names:
                out[category].append(self._entry(name, values, reveal, True))
        return out

    def _entry(self, name, values, reveal, linked) -> Entry:
        info = field_map.CATALOG[name]
        sensitive = is_sensitive(name)
        value = values.get(name)
        masked = value is not None and sensitive and name not in reveal
        display = "" if value is None else MASK if masked else value
        return Entry(
            name=name,
            category=info["category"],
            type=info["type"],
            has_value=value is not None,
            display=display,
            masked=masked,
            sensitive=sensitive,
            linked=linked,
        )

    def _has(self, name) -> bool:
        return name in self._vault.data_point_values([name], self._bot_id)

    def add(self, name: str, value: str) -> None:
        """Store a value for a data point that holds none yet."""
        name = self._name(name)
        if self._has(name):
            raise MasterDataError("this data point already holds a value; edit it")
        self._vault.set_data_point(name, value, bot_id=self._bot_id)

    def edit(self, name: str, value: str) -> None:
        """Replace a held value; every tab that shows it shows the new one."""
        name = self._name(name)
        if not self._has(name):
            raise MasterDataError("this data point holds no value yet; add it")
        self._vault.set_data_point(name, value, bot_id=self._bot_id)

    def delete(self, name: str, *, confirm) -> None:
        """Delete a held value and its links. Needs confirm=True exactly."""
        name = self._name(name)
        if confirm is not True:
            raise MasterDataError("delete needs confirm=True")
        if not self._has(name):
            raise MasterDataError("this data point holds no value")
        self._vault.delete_data_point(name, bot_id=self._bot_id)

    def reveal(self, name: str) -> str | None:
        """Return one value unmasked (the vault logs a keyed hash)."""
        return self._vault.get_data_point(self._name(name), bot_id=self._bot_id)

    def link(self, name: str, category: str) -> None:
        """Also show a data point under another catalog category."""
        self._vault.link_data_point(self._name(name), category, bot_id=self._bot_id)

    def unlink(self, name: str, category: str) -> None:
        """Stop showing a data point under an extra category."""
        self._vault.unlink_data_point(self._name(name), category, bot_id=self._bot_id)
