"""The fill window: fill a VA form from your vault, one data point at a time.

The form shows in a QPdfView (QtPdf). A side panel shows one data point at
a time with a checklist of every place it goes on the form, and Next (Finish
on the last one). Two settings, saved in the vault's one rules table:

- "By data point": one step per data point, holding every place it goes.
  Off, the steps follow the form by page and field. Both are orderings of
  the same field list (fill_engine.places / fill_engine.steps).
- "Require each item checked": Next and Finish stay disabled until every
  place of the current data point is ticked.

A data point missing from the vault gets an entry box that saves to the
vault; sensitive ones (master_data.is_sensitive, the existing name rule)
are typed hidden, with a Show button. There is no copy feature: no copy
button, no context menu, and copy / cut keys do nothing in the entry box.
Finish writes "<name> fillable.pdf" next to the form and "<name>
completed.pdf" in the Completed forms folder (fill_engine.fill_form);
identifiers go only into that local completed file. No network code.

When to use: after the user accepted a field map for a form.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
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
fill_engine = _load("fill_engine")
master_data = _load("master_data")

MISSING = "Not in your vault yet. Type it and save it, or leave it blank."
SAVED = "Saved in your vault: "


_UPPER = {"ssn", "va", "dob", "id", "zip", "dd"}


def _title(name: str) -> str:
    """A data-point name for display: "va_file_number" -> "VA file number"."""
    words = [w.upper() if w in _UPPER else w for w in name.split("_")]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


class EntryBox(QLineEdit):
    """A line edit with no copy feature: no context menu, copy / cut ignored."""

    def __init__(self) -> None:
        super().__init__()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setDragEnabled(False)

    def keyPressEvent(self, event) -> None:  # Qt's method name
        if event.matches(QKeySequence.StandardKey.Copy) or event.matches(
            QKeySequence.StandardKey.Cut
        ):
            event.accept()
            return
        super().keyPressEvent(event)


class FillWindow(qt.Window):
    """QPdfView of the form plus a side panel, one data point at a time."""

    def __init__(
        self, vault, source, raw_map, request, *, bot_id: str, completed_dir=None
    ) -> None:
        app = qt.app()  # the QApplication comes before any widget
        super().__init__("Fill a form")
        self.app = app
        self.vault = vault
        self.bot_id = bot_id
        self.source = Path(source)
        self.raw_map = raw_map
        self.request = request
        self.completed_dir = completed_dir
        self.all_places = fill_engine.places(raw_map, request)
        self.steps: list = []
        self.index = 0
        self.boxes: list[QCheckBox] = []
        rules = vault.rules(bot_id)

        self.document = QPdfDocument(self)
        self.document.load(str(self.source))
        self.view = QPdfView(self)
        self.view.setObjectName("form_view")
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        panel = QWidget()
        side = QVBoxLayout(panel)
        settings = QGroupBox("Settings")
        row = QVBoxLayout(settings)
        self.by_point = QCheckBox("By data point")
        self.by_point.setObjectName("by_data_point")
        self.by_point.setChecked(rules["by_data_point"])
        self.require = QCheckBox("Require each item checked")
        self.require.setObjectName("require_each_checked")
        self.require.setChecked(rules["require_each_checked"])
        self.by_point.toggled.connect(lambda on: self._rule("by_data_point", on))
        self.require.toggled.connect(lambda on: self._rule("require_each_checked", on))
        row.addWidget(self.by_point)
        row.addWidget(self.require)
        side.addWidget(settings)

        self.title = qt._label("", size=15, bold=True, name="data_point")
        self.value = qt._label("", name="value")
        side.addWidget(self.title)
        side.addWidget(self.value)
        entry_row = QHBoxLayout()
        self.entry = EntryBox()
        self.entry.setObjectName("entry")
        self.show_btn = QPushButton("Show")
        self.show_btn.setObjectName("Show")
        self.show_btn.setCheckable(True)
        self.show_btn.toggled.connect(self._show)
        self.save_btn = qt._button("Save to vault", self.save, "Save to vault")
        entry_row.addWidget(self.entry, 1)
        entry_row.addWidget(self.show_btn)
        entry_row.addWidget(self.save_btn)
        side.addLayout(entry_row)
        side.addWidget(qt._label("Places it goes on the form:", bold=True))
        self.checklist = QVBoxLayout()
        side.addLayout(self.checklist)
        side.addStretch(1)
        buttons = QHBoxLayout()
        self.next_btn = qt._button("Next", self.go_next, "Next")
        self.finish_btn = qt._button("Finish", self.finish, "Finish")
        buttons.addWidget(self.next_btn)
        buttons.addWidget(self.finish_btn)
        side.addLayout(buttons)
        self.status = qt._label("", name="status")
        side.addWidget(self.status)

        split = QSplitter()
        split.addWidget(self.view)
        split.addWidget(panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer = QHBoxLayout(self)
        outer.addWidget(split)
        self.resize(1100, 760)
        self._rebuild()

    # ---- steps ----------------------------------------------------------
    def _rebuild(self) -> None:
        self.steps = fill_engine.steps(
            self.all_places, by_data_point=self.by_point.isChecked()
        )
        self.index = 0
        self.draw()

    def _rule(self, rule: str, on: bool) -> None:
        self.vault.set_rule(rule, bool(on), self.bot_id)
        if rule == "by_data_point":
            self._rebuild()
        else:
            self._update_buttons()

    def draw(self) -> None:
        if not self.steps:
            self.title.setText("This field map fills no named fields.")
            self.next_btn.hide()
            self.finish_btn.hide()
            return
        step = self.steps[self.index]
        name = step.data_point
        sensitive = master_data.is_sensitive(name)
        self.title.setText(_title(name))
        self.title.setProperty("data_point", name)
        held = self.vault.data_point_values([name], self.bot_id)
        if name in held:
            shown = master_data.MASK if sensitive else held[name]
            self.value.setText(SAVED + shown)
            for w in (self.entry, self.show_btn, self.save_btn):
                w.hide()
        else:
            self.value.setText(MISSING)
            self.entry.clear()
            self.show_btn.setChecked(False)
            self._show(False)
            self.entry.show()
            self.save_btn.show()
            self.show_btn.setVisible(sensitive)
            if not sensitive:
                self.entry.setEchoMode(QLineEdit.EchoMode.Normal)
        for box in self.boxes:
            self.checklist.removeWidget(box)
            box.hide()
            box.setObjectName("")
            box.deleteLater()
        self.boxes = []
        for k, place in enumerate(step.places):
            box = QCheckBox(f"Page {place.page}: {place.field}")
            box.setObjectName(f"place_{k}")
            box.toggled.connect(lambda _on: self._update_buttons())
            self.checklist.addWidget(box)
            self.boxes.append(box)
        last = self.index == len(self.steps) - 1
        self.next_btn.setVisible(not last)
        self.finish_btn.setVisible(last)
        nav = self.view.pageNavigator()
        nav.jump(step.places[0].page - 1, QPointF(), nav.currentZoom())
        self._update_buttons()

    def _ready(self) -> bool:
        return not self.require.isChecked() or all(b.isChecked() for b in self.boxes)

    def _update_buttons(self) -> None:
        ok = self._ready()
        self.next_btn.setEnabled(ok)
        self.finish_btn.setEnabled(ok)

    def _show(self, on: bool) -> None:
        if master_data.is_sensitive(self.title.property("data_point") or ""):
            mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            self.entry.setEchoMode(mode)
        self.show_btn.setText("Hide" if on else "Show")

    # ---- actions --------------------------------------------------------
    def save(self) -> None:
        """Save the typed value of a missing data point to the vault."""
        name = self.title.property("data_point")
        text = self.entry.text()
        if not text.strip():
            self.status.setText("Type a value first.")
            return
        try:
            self.vault.set_data_point(name, text, bot_id=self.bot_id)
        except fill_engine.form_fill.records_vault.VaultError as exc:
            self.status.setText(f"Not saved: {exc}")
            return
        self.entry.clear()
        keep = [b.isChecked() for b in self.boxes]
        self.draw()
        for box, on in zip(self.boxes, keep):
            box.setChecked(on)
        self.status.setText("Saved to your vault.")

    def go_next(self) -> None:
        if not self._ready() or self.index >= len(self.steps) - 1:
            return
        self.index += 1
        self.status.setText("")
        self.draw()

    def finish(self) -> None:
        """Write the fillable and completed files (fill_engine.fill_form)."""
        if not self._ready():
            return
        errors = (
            fill_engine.FillError,
            fill_engine.field_map.FieldMapError,
            fill_engine.form_fill.records_vault.VaultError,
        )
        try:
            res = fill_engine.fill_form(
                self.vault,
                self.source,
                self.raw_map,
                self.request,
                bot_id=self.bot_id,
                completed_dir=self.completed_dir,
            )
        except errors as exc:
            self.status.setText(f"Not filled: {exc}")
            return
        self.finish_btn.setEnabled(False)
        self.status.setText(
            f"Saved {res.completed_path.name} in your"
            f" {res.completed_path.parent.name} folder, and"
            f" {res.fillable_path.name} next to the form."
        )


def run_fill(vault, source, raw_map, request, *, bot_id: str) -> None:
    """Open the fill window and wait until it closes."""
    win = FillWindow(vault, source, raw_map, request, bot_id=bot_id)
    qt._exec(win)
