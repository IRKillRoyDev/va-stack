"""The master data window: your saved details, one tab per category.

Every catalog data point shows under its own category's tab
(master_data.MasterData). Each row has Add (when it holds no value) or
Edit, and Delete, which asks in a confirm dialog first. Every value is
masked by default, sensitive or not, and each row has its own Show toggle;
the value being typed is masked too, with its own Show. A data point that
is linked to another tab (the vault's data_point_links) is marked *Linked
in every place it shows; editing it in one place changes it everywhere.

Values live only in the encrypted vault; the access log keeps keyed hashes
and counts only. There is no copy feature (no copy button, no context menu,
copy / cut keys do nothing, values cannot be selected) and no network code.

When to use: after the vault is unlocked, to review or change your details.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


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


qt = _load("qt_ui")
master_data = _load("master_data")
records_vault = _load("records_vault")

LINKED = "  *Linked"
EMPTY = "(empty)"


class MasterDataWindow(qt.Window):
    """Tabs by category; add, edit, delete; masked values with Show."""

    def __init__(self, vault, *, bot_id: str) -> None:
        app = qt.app()  # the QApplication comes before any widget
        super().__init__("Your saved details")
        self.app = app
        self.mu_qt = qt
        self.model = master_data.MasterData(vault, bot_id=bot_id)
        self.vault = vault
        self.bot_id = bot_id
        self.categories = tuple(master_data.field_map.CATEGORIES)
        self.editing: tuple[str, str] | None = None  # (name, "add" or "edit")

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabs")
        outer.addWidget(self.tabs, 1)

        self.editor = QWidget()
        row = QHBoxLayout(self.editor)
        row.setContentsMargins(0, 0, 0, 0)
        self.editing_label = qt._label("", bold=True, name="editing")
        self.entry = qt.EntryBox()
        self.entry.setObjectName("entry")
        self.entry_show = QPushButton("Show")
        self.entry_show.setObjectName("entry_show")
        self.entry_show.setCheckable(True)
        self.entry_show.toggled.connect(self._show_entry)
        row.addWidget(self.editing_label)
        row.addWidget(self.entry, 1)
        row.addWidget(self.entry_show)
        row.addWidget(qt._button("Save", self.save, "Save"))
        row.addWidget(qt._button("Cancel", self.cancel, "Cancel"))
        outer.addWidget(self.editor)
        self.editor.hide()
        self.status = qt._label("", name="status")
        outer.addWidget(self.status)
        self.resize(1000, 720)
        self.refresh()

    # ---- the tabs -------------------------------------------------------
    def refresh(self) -> None:
        """Re-draw every tab from the vault; every value starts masked."""
        keep = max(self.tabs.currentIndex(), 0)
        while self.tabs.count():
            page = self.tabs.widget(0)
            self.tabs.removeTab(0)
            for child in [page, *page.findChildren(QWidget)]:
                child.setObjectName("")  # gone for lookups before deletion
            page.deleteLater()
        tabs = self.model.tabs()
        linked = {name for name, _ in self.vault.data_point_links(self.bot_id)}
        for category in self.categories:
            body = QWidget()
            grid = QGridLayout(body)
            for r, entry in enumerate(tabs[category]):
                self._row(grid, r, category, entry, entry.name in linked)
            grid.setRowStretch(len(tabs[category]), 1)
            grid.setColumnStretch(1, 1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(body)
            self.tabs.addTab(scroll, category.capitalize())
        self.tabs.setCurrentIndex(min(keep, self.tabs.count() - 1))

    def _row(self, grid, r, category, entry, linked) -> None:
        name = entry.name
        key = f"{category}:{name}"
        title = qt.data_point_title(name) + (LINKED if linked else "")
        grid.addWidget(qt._label(title, name=f"name:{key}"), r, 0)
        value = qt._label(master_data.MASK if entry.has_value else EMPTY)
        value.setObjectName(f"value:{key}")
        value.setWordWrap(False)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        grid.addWidget(value, r, 1)
        if entry.has_value:
            show = QPushButton("Show")
            show.setObjectName(f"show:{key}")
            show.setCheckable(True)
            show.toggled.connect(
                lambda on, n=name, v=value, b=show: self._show_value(n, v, b, on)
            )
            grid.addWidget(show, r, 2)
            grid.addWidget(
                qt._button("Edit", lambda n=name: self.start(n, "edit"), f"edit:{key}"),
                r,
                3,
            )
            grid.addWidget(
                qt._button("Delete", lambda n=name: self.delete(n), f"delete:{key}"),
                r,
                4,
            )
        else:
            grid.addWidget(
                qt._button("Add", lambda n=name: self.start(n, "add"), f"add:{key}"),
                r,
                3,
            )

    def _show_value(self, name, label, button, on: bool) -> None:
        """One value's own Show toggle (read from the vault on demand)."""
        button.setText("Hide" if on else "Show")
        if on:
            label.setText(self.model.reveal(name) or "")
        else:
            label.setText(master_data.MASK)

    # ---- add / edit -----------------------------------------------------
    def start(self, name: str, mode: str) -> None:
        """Open the entry box to add or edit one data point."""
        self.editing = (name, mode)
        verb = "Add" if mode == "add" else "New value for"
        self.editing_label.setText(f"{verb} {qt.data_point_title(name)}:")
        self.entry.clear()
        self.entry_show.setChecked(False)
        self._show_entry(False)
        self.editor.show()
        self.entry.setFocus()
        self.status.setText("")

    def _show_entry(self, on: bool) -> None:
        """The typed value's own Show toggle."""
        mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        self.entry.setEchoMode(mode)
        self.entry_show.setText("Hide" if on else "Show")

    def cancel(self) -> None:
        self.entry.clear()
        self.editing = None
        self.editor.hide()

    def save(self) -> None:
        """Store the typed value in the vault (add or edit), then re-draw."""
        if self.editing is None:
            return
        name, mode = self.editing
        text = self.entry.text()
        if not text.strip():
            self.status.setText("Type a value first.")
            return
        try:
            if mode == "add":
                self.model.add(name, text)
            else:
                self.model.edit(name, text)
        except (master_data.MasterDataError, records_vault.VaultError) as exc:
            self.status.setText(f"Not saved: {exc}")
            return
        self.cancel()
        self.refresh()
        self.status.setText(f"Saved {qt.data_point_title(name)} to your vault.")

    # ---- delete ---------------------------------------------------------
    def delete(self, name: str) -> None:
        """Delete one value (and its links) after the user confirms."""
        title = qt.data_point_title(name)
        question = (
            f"Delete the value saved for {title}? It is removed from your vault"
            " and from every tab it shows in."
        )
        if not qt.ask(self, "Delete a saved value", question):
            self.status.setText("Nothing was deleted.")
            return
        try:
            self.model.delete(name, confirm=True)
        except (master_data.MasterDataError, records_vault.VaultError) as exc:
            self.status.setText(f"Not deleted: {exc}")
            return
        if self.editing and self.editing[0] == name:
            self.cancel()
        self.refresh()
        self.status.setText(f"Deleted {title} from your vault.")


def run_master(vault, *, bot_id: str) -> None:
    """Open the master data window and wait until it closes."""
    qt._exec(MasterDataWindow(vault, bot_id=bot_id))
