"""PySide6 windows: start screen, Unlock, and the per-field review window.

All rules live elsewhere: records_vault (vault, consent, log),
review_session (ReviewSession and FieldReview: explicit Yes, the typed
phrase for Confirm all, nothing written before Confirm all) and
va_stack_app (open or create the vault, build the review). This file only
draws them. The page pane re-draws the cited record page from the redacted
text stored in the encrypted vault (QtPdf: QPdfDocument plus QPdfView), in
memory only, and highlights the exact spot of the field you click.

When to use: through va_stack_app.run_gui() / vault_ui; needs a display (or
QT_QPA_PLATFORM=offscreen in tests).
"""

from __future__ import annotations

import importlib.util
import io
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QEventLoop,
    QIODevice,
    QObject,
    QPointF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

FILE_FILTER = "Medical file (PDF or text) (*.pdf *.txt);;All files (*)"
RED = "color: #b00020; font-weight: bold;"
PAGE_NOTE = (
    "Source page, re-drawn from the text in your encrypted vault (personal"
    " details hidden). The yellow box is the spot this field came from."
)
CONFIRMED = "Confirmed and locked."
# Return codes of run_review().
CONFIRMED_ALL, CLOSED, SAVED_FOR_LATER = 0, 1, 2


def _sibling(name: str):
    """Load scripts/<name>.py once (shared through sys.modules)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rs = _sibling("review_session")
safe_path = _sibling("safe_path")
rv = rs.pi.records_vault


# ---- dialogs (tests replace these) --------------------------------------
def info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def error(parent, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def ask(parent, title: str, text: str) -> bool:
    """Yes/No question; True for Yes."""
    answer = QMessageBox.question(parent, title, text)
    return answer == QMessageBox.StandardButton.Yes


def pick_file(parent, title: str) -> str:
    """The file picker; "" when cancelled."""
    return QFileDialog.getOpenFileName(parent, title, "", FILE_FILTER)[0]


# ---- event loop helpers -------------------------------------------------
def app() -> QApplication:
    """The one QApplication (created on first use)."""
    return QApplication.instance() or QApplication(sys.argv[:1])


class Window(QWidget):
    """A top-level window that tells its event loop when it closes."""

    closed = Signal()

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.screen_name = ""
        self._hooks: dict = {}
        self._hook_args: tuple = ()
        self.errors: list = []

    def closeEvent(self, event) -> None:  # Qt's method name
        super().closeEvent(event)
        self.closed.emit()

    def run_hook(self, name: str) -> None:
        """Run a scripted test hook once this screen is up; never hang.

        When to use: tests only. An error closes the window and is raised
        after the loop; a hook that leaves the same screen open closes it.
        """
        hook = self._hooks.pop(name, None)
        if hook is None:
            return
        screen = self.screen_name

        def call() -> None:
            try:
                hook(self, *self._hook_args)
            except Exception as exc:  # noqa: BLE001 (raised after the loop)
                self.errors.append(exc)
                self.close()
                return
            if self.isVisible() and self.screen_name == screen:
                self.close()

        QTimer.singleShot(0, call)


def _exec(win: Window) -> None:
    loop = QEventLoop()
    win.closed.connect(loop.quit)
    win.show()
    if win.isVisible():
        loop.exec()
    if win.errors:
        raise win.errors[0]


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            for child in [w, *w.findChildren(QWidget)]:
                child.setObjectName("")  # gone for lookups before deletion
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _label(text: str, *, size: int = 0, bold: bool = False, name: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    if size or bold:
        font = QFont(lab.font())
        if size:
            font.setPointSize(size)
        font.setBold(bold)
        lab.setFont(font)
    if name:
        lab.setObjectName(name)
    return lab


_UPPER = {"ssn", "va", "dob", "id", "zip", "dd"}


def data_point_title(name: str) -> str:
    """A data-point name for display: "va_file_number" -> "VA file number"."""
    words = [w.upper() if w in _UPPER else w for w in name.split("_")]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


class EntryBox(QLineEdit):
    """A line edit with no copy feature: no context menu, copy / cut ignored.

    Typed text starts masked (password echo); a Show toggle may unmask it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setDragEnabled(False)
        self.setEchoMode(QLineEdit.EchoMode.Password)

    def keyPressEvent(self, event) -> None:  # Qt's method name
        if event.matches(QKeySequence.StandardKey.Copy) or event.matches(
            QKeySequence.StandardKey.Cut
        ):
            event.accept()
            return
        super().keyPressEvent(event)


def _button(text: str, slot, name: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName(name or text)
    btn.setMinimumHeight(36)
    btn.clicked.connect(lambda _checked=False: slot())  # drop Qt's flag
    return btn


# ---- the page pane: re-draw one record page and mark a spot -------------
COLS = 64
MARGIN = 36.0
FONT_SIZE = 10.0
CHAR_W = FONT_SIZE * 0.6  # Courier is monospaced
LINE_H = 12.0
PAGE_W = MARGIN * 2 + COLS * CHAR_W
MIN_PAGE_H = 792.0


def _rows(text: str) -> list[tuple[int, int, str]]:
    """(source line index, start column, text) per drawn row (word-wrapped)."""
    out = []
    for n, line in enumerate((text or "").splitlines() or [""]):
        line = line.expandtabs(4)
        if not line:
            out.append((n, 0, ""))
        start = 0
        while start < len(line):
            end = start + COLS
            if end < len(line):
                cut = line.rfind(" ", start, end)
                if cut > start:
                    end = cut + 1  # wrap after a space, not inside a word
            out.append((n, start, line[slice(start, end)]))
            start = end
    return out


def page_of_link(link: str) -> int:
    """The 1-based page number in a vault link ("vault://<id>#page=3" -> 3)."""
    head, sep, page = str(link).rpartition("#page=")
    if not sep or not head or not page.isdigit() or int(page) < 1:
        raise ValueError("not a vault page link")
    return int(page)


def quote_rects(text: str, quote: str, line: int = 1) -> list[tuple[float, ...]]:
    """Boxes (x0, y0, x1, y1 in points, top-left origin) around the quote."""
    return quote_spot(text, quote, line)[0]


def quote_spot(text: str, quote: str, line: int = 1) -> tuple[list, int]:
    """(boxes around the quote, 1-based source line where the boxes start).

    When to use: by render_page() and the page pane. Lines are counted from
    1 in the page text, before word-wrapping, the same way vault links and
    report rows count them. Whitespace is compared loosely (the quote is
    one line of words; the page may wrap it). When the quote is on the page
    more than once, the copy nearest the cited line wins. Not found: no
    boxes and the cited line.
    """
    rows = _rows(text)
    flat: list[str] = []
    where: list[tuple[int, int] | None] = []
    for r, (n, _start, row) in enumerate(rows):
        # A wrapped row continues its source line: no space between them.
        if r and rows[r - 1][0] != n and flat and flat[-1] != " ":
            flat.append(" ")
            where.append(None)
        for c, ch in enumerate(row):
            if ch.isspace():
                if flat and flat[-1] != " ":
                    flat.append(" ")
                    where.append(None)
            else:
                flat.append(ch)
                where.append((r, c))
    joined = "".join(flat)
    target = " ".join((quote or "").split())
    if not target:
        return [], line
    hits, pos = [], joined.find(target)
    while pos != -1:
        hits.append(pos)
        pos = joined.find(target, pos + 1)
    if not hits:
        return [], line

    def distance(p: int) -> int:
        first = next(w for w in where[slice(p, None)] if w is not None)
        return abs(rows[first[0]][0] - (line - 1))

    pos = min(hits, key=distance)
    spans: dict[int, list[int]] = {}
    for w in where[slice(pos, pos + len(target))]:
        if w is not None:
            spans.setdefault(w[0], []).append(w[1])
    boxes = [
        (
            MARGIN + min(cols) * CHAR_W,
            MARGIN + r * LINE_H,
            MARGIN + (max(cols) + 1) * CHAR_W,
            MARGIN + (r + 1) * LINE_H,
        )
        for r, cols in sorted(spans.items())
    ]
    return boxes, rows[min(spans)][0] + 1


def _drawable(text: str) -> str:
    """Same length, only characters the built-in Courier font can draw."""
    return "".join(ch if ch.encode("cp1252", "ignore") else "?" for ch in text)


def render_page(text: str, quote: str = "", line: int = 1) -> tuple[bytes, list]:
    """A one-page PDF (bytes, in memory) of the page text, quote boxed.

    When to use: for the page pane. Nothing is written to disk.
    """
    from reportlab.pdfgen import canvas

    rows = _rows(text)
    rects = quote_rects(text, quote, line) if quote else []
    height = max(MIN_PAGE_H, MARGIN * 2 + len(rows) * LINE_H)
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(PAGE_W, height), invariant=1)
    pdf.setFillColorRGB(1.0, 0.9, 0.2)
    for x0, y0, x1, y1 in rects:
        pdf.rect(x0 - 1, height - y1 - 1, x1 - x0 + 2, y1 - y0 + 2, stroke=0, fill=1)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Courier", FONT_SIZE)
    for r, (_n, _start, row) in enumerate(rows):
        baseline = height - MARGIN - r * LINE_H - FONT_SIZE
        pdf.drawString(MARGIN, baseline, _drawable(row))
    pdf.showPage()
    pdf.save()
    return buf.getvalue(), rects


class PagePane(QWidget):
    """QPdfView of one re-drawn record page with the cited spot boxed."""

    def __init__(self, vault, bot_id: str) -> None:
        super().__init__()
        self.vault, self.bot_id = vault, bot_id
        self.document = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setObjectName("page_view")
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.caption = _label("", name="page_caption")
        self.field_id = None
        self.page = 0
        self.line = 0
        self.rects: list = []
        self._buffer = None
        self._pages: dict[str, str] = {}
        box = QVBoxLayout(self)
        box.addWidget(_label(PAGE_NOTE))
        box.addWidget(self.caption)
        box.addWidget(self.view, 1)

    def show_field(self, field: dict) -> None:
        """Load the field's page and box the exact quote.

        The page number comes from the field's vault link and must equal
        the page the field cites; the line is where the boxed words start
        on that page (1-based, before wrapping).
        """
        link = field["link"]
        page = page_of_link(link)
        if page != field["page"]:
            raise ValueError("the field's link and page disagree")
        if link not in self._pages:
            self._pages[link] = self.vault.open_link(link, bot_id=self.bot_id)
        text = self._pages[link]
        data, self.rects = render_page(text, field["value"], field["line"])
        _boxes, line = quote_spot(text, field["value"], field["line"])
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(data))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.document.load(self._buffer)
        for _ in range(200):
            if self.document.status() == QPdfDocument.Status.Ready:
                break
            QApplication.processEvents()
        self.field_id = field["id"]
        self.page, self.line = page, line
        where = f"{field['label']} (page {page}, line {line})"
        if not self.rects:
            where += " (the exact words were not found on the page; read it all)"
        self.caption.setText(where)
        top = self.rects[0][1] if self.rects else 0.0
        self.view.pageNavigator().jump(0, QPointF(0.0, max(0.0, top - 48.0)), 0)
        self.view.viewport().update()

    def highlighted_text(self) -> str:
        """The page text inside the boxes (what the yellow box covers)."""
        parts = []
        for x0, y0, x1, y1 in self.rects:
            mid = (y0 + y1) / 2
            sel = self.document.getSelection(
                0, QPointF(x0 + 0.5, mid), QPointF(x1 - 0.5, mid)
            )
            parts.append(sel.text())
        return " ".join(" ".join(parts).split())


# ---- the review window --------------------------------------------------
class _ClickFilter(QObject):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event) -> bool:  # Qt's method name
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            self.callback()
        return False


class ReviewWindow(Window):
    """One form per claim; fields, red flags, issues and the page pane."""

    def __init__(self, report: dict, vault, bot_id: str) -> None:
        super().__init__("Review your records")
        self.vault, self.bot_id = vault, bot_id
        self.fr = rs.FieldReview(report)
        self.code = CLOSED
        self.editors: dict[str, QPlainTextEdit] = {}
        self.current_field: str | None = None
        self._filters: list = []
        self.pane = PagePane(vault, bot_id)
        self.body = QWidget()
        self.form_box = QVBoxLayout(self.body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.body)
        split = QSplitter()
        split.addWidget(scroll)
        split.addWidget(self.pane)
        split.setSizes([620, 520])
        outer = QVBoxLayout(self)
        outer.addWidget(split, 1)
        self.nav = QHBoxLayout()
        outer.addLayout(self.nav)
        self.resize(1200, 820)
        for key, slot in (
            ("Y", self.key_yes),
            ("Escape", self.go_back),
            ("Right", self.go_next),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)

    # ---- drawing -----------------------------------------------------
    def draw(self) -> None:
        _clear(self.form_box)
        _clear(self.nav)
        self.editors = {}
        self._filters = []
        index = self.fr.form_index
        if index is None:
            self.screen_name = self.fr.session.screen
            self._draw_summary()
            return
        self.screen_name = f"form-{index}"
        form = self.fr.forms[index]
        total = len(self.fr.forms)
        self.form_box.addWidget(_label(f"{index + 1} of {total}", name="progress"))
        self.form_box.addWidget(
            _label(form["title"], size=18, bold=True, name="form_title")
        )
        self.form_box.addWidget(_label(rs.NAMES_CHECK))
        for k, field in enumerate(form["fields"]):
            self.form_box.addWidget(self._field_box(k, field))
        self.form_box.addStretch(1)
        self.nav.addWidget(_button("Back", self.go_back))
        self.nav.addStretch(1)
        self.nav.addWidget(_button("Save for later", self.save_later, "save_later"))
        self.nav.addWidget(_button("Next", self.go_next))
        first = next(
            (f for f in form["fields"] if not self.fr.state[f["id"]]["locked"]),
            form["fields"][0],
        )
        self.select(first["id"])

    def _field_box(self, k: int, field: dict) -> QGroupBox:
        st = self.fr.state[field["id"]]
        where = f"page {field['page']}, line {field['line']}"
        box = QGroupBox(f"{field['label']} ({where})")
        box.setObjectName(f"field_box-{k}")
        lay = QVBoxLayout(box)
        edit = QPlainTextEdit(st["value"])
        edit.setObjectName(f"field-{k}")
        edit.setReadOnly(st["locked"])
        edit.setFixedHeight(80)
        filt = _ClickFilter(lambda fid=field["id"]: self.select(fid))
        edit.installEventFilter(filt)
        edit.viewport().installEventFilter(filt)
        self._filters.append(filt)
        self.editors[field["id"]] = edit
        lay.addWidget(edit)
        context = field["context"].replace("[QUOTE]", "").replace("[/QUOTE]", "")
        lay.addWidget(_label("Around it: " + context, name=f"context-{k}"))
        flags = list(field["flags"])
        if st["changed"] and rv.USER_UPDATED not in flags:
            flags.append(rv.USER_UPDATED)
        for j, flag in enumerate(flags):
            lab = _label("Red flag: " + flag, name=f"flag-{k}-{j}")
            lab.setStyleSheet(RED)
            lay.addWidget(lab)
        issues = QListWidget()
        issues.setObjectName(f"issues-{k}")
        issues.addItems(self.fr.issues(field["id"]))
        issues.setFixedHeight(18 * (issues.count() + 1))
        lay.addWidget(_label("Issues:"))
        lay.addWidget(issues)
        if st["locked"]:
            lay.addWidget(_label(CONFIRMED, name=f"locked-{k}"))
        else:
            lay.addWidget(
                _button(
                    "Confirm", lambda fid=field["id"]: self.confirm(fid), f"confirm-{k}"
                )
            )
        return box

    def _draw_summary(self) -> None:
        v = self.fr.session.view()
        self.form_box.addWidget(_label("Summary", size=18, bold=True, name="summary"))
        self.form_box.addWidget(_label(v["names_check"]))
        for r in v["rows"]:
            line = QHBoxLayout()
            line.addWidget(
                _label(f"{r['condition']}: {r['status']}", name=f"status-{r['edit']}")
            )
            line.addWidget(
                _button("Edit", lambda i=r["edit"]: self.edit(i), f"edit-{r['edit']}")
            )
            self.form_box.addLayout(line)
        if v["screen"] == "summary":
            self.form_box.addWidget(
                _label(
                    f"{rs.PHRASE_PROMPT} {v['confirm_phrase']}",
                    size=14,
                    name="phrase_prompt",
                )
            )
            self.phrase = QLineEdit()
            self.phrase.setObjectName("phrase")
            self.form_box.addWidget(self.phrase)
            btn = _button("Confirm all", self.confirm_all, "confirm_all")
            btn.setEnabled(v["can_confirm"])
            self.form_box.addWidget(btn)
        self.form_box.addStretch(1)
        self.nav.addWidget(_button("Back", self.go_back))
        self.nav.addStretch(1)
        self.nav.addWidget(_button("Save for later", self.save_later, "save_later"))

    # ---- actions -----------------------------------------------------
    def select(self, field_id: str) -> None:
        """Clicking a field shows its exact source spot on the page.

        Only fields of the form on screen can be shown, so the pane always
        matches a field the user can see.
        """
        index, field = self.fr._by_id[field_id]
        if index != self.fr.form_index:
            raise ValueError("that field is not on the form on screen")
        self.current_field = field_id
        if self.pane.field_id != field_id:
            self.pane.show_field(field)

    def confirm(self, field_id: str) -> None:
        edit = self.editors[field_id]
        out = self.fr.confirm_field(field_id, edit.toPlainText())
        if out["warning"]:
            warn(self, "Please check", out["warning"])
        self.draw()

    def key_yes(self) -> None:
        """y confirms the selected field as it is (Enter is not a Yes)."""
        index = self.fr.form_index
        if index is None:
            return
        ids = [f["id"] for f in self.fr.forms[index]["fields"]]
        fid = self.current_field if self.current_field in ids else None
        if fid is None or self.fr.state[fid]["locked"]:
            fid = next((i for i in ids if not self.fr.state[i]["locked"]), None)
        if fid is not None:
            self.confirm(fid)

    def go_next(self) -> None:
        index = self.fr.form_index
        if index is None:
            return
        left = self.fr.red_flags(index)
        if left and not ask(
            self,
            "Red flags left",
            f"{len(left)} red flag(s) on this form are not confirmed yet."
            " Go to the next form anyway?",
        ):
            return
        self.fr.next()
        self.draw()

    def go_back(self) -> None:
        self.fr.back()
        self.draw()

    def edit(self, index: int) -> None:
        self.fr.edit(index)
        self.draw()

    def save_later(self) -> None:
        """Keep the unfinished review in the encrypted vault, then close."""
        pending = {fid: e.toPlainText() for fid, e in self.editors.items()}
        self.fr.save_for_later(self.vault, self.bot_id, pending=pending)
        info(
            self,
            "Saved for later",
            "Your review so far is kept in your encrypted vault. Open the"
            " review again to pick up where you left off.",
        )
        self.code = SAVED_FOR_LATER
        self.close()

    def confirm_all(self) -> None:
        try:
            out = self.fr.confirm_all(
                self.vault, self.bot_id, phrase=self.phrase.text()
            )
        except ValueError as exc:
            info(self, "Not finished", str(exc))
            return
        for w in out["warnings"]:
            warn(self, "Personal details hidden", w)
        self.code = CONFIRMED_ALL
        self.close()


def run_review(report: dict, vault, bot_id: str, on_ready=None) -> int:
    """Show the review window; 0 confirmed, 1 closed, 2 saved for later.

    When to use: after unlock, with a built report. A review saved for
    later for this same report is picked up again. on_ready(win, vault) is
    a test hook that drives the window with real widget events.
    """
    app()
    win = ReviewWindow(report, vault, bot_id)
    if win.fr.restore(vault, bot_id):
        info(win, "Welcome back", "Picked up the review you saved for later.")
    win._hooks = {"review": on_ready} if on_ready else {}
    win._hook_args = (vault,)
    win.draw()
    win.run_hook("review")
    _exec(win)
    return win.code


# ---- Unlock window ------------------------------------------------------
def unlock(db_path: Path, bot_id: str, on_ready=None):
    """Consent first, then the password; the unlocked Vault or None.

    When to use: before the review window. The caller closes the vault.
    """
    app()
    flow = rv.UnlockFlow(db_path)
    win = Window("Unlock records vault")
    win.screen_name = "unlock"
    result = {"vault": None}
    lay = QVBoxLayout(win)
    tick = QCheckBox("I have read this and consent to share approved snippets")
    tick.setObjectName("consent")
    consent = QPlainTextEdit(flow.consent_text)
    consent.setReadOnly(True)
    password = QLineEdit()
    password.setObjectName("password")
    password.setEchoMode(QLineEdit.EchoMode.Password)
    password.setEnabled(False)

    def on_tick(state) -> None:
        if tick.isChecked():
            flow.acknowledge()
            password.setEnabled(True)
            password.setFocus()

    def on_unlock() -> None:
        if not flow.password_enabled:
            info(win, "Consent needed", "Tick the consent box first.")
            return
        buf = bytearray(password.text().encode())
        password.clear()
        try:
            result["vault"] = flow.submit(buf)
        except rv.WrongPassword:
            error(win, "Locked", "Wrong password. The vault stays locked.")
        win.close()

    tick.stateChanged.connect(on_tick)
    password.returnPressed.connect(on_unlock)
    lay.addWidget(tick)
    lay.addWidget(consent)
    lay.addWidget(_label("Vault password"))
    lay.addWidget(password)
    lay.addWidget(_button("Unlock", on_unlock))
    win._hooks = {"unlock": on_ready} if on_ready else {}
    win.run_hook("unlock")
    try:
        _exec(win)
    except BaseException:
        if result["vault"] is not None:
            result["vault"].close()
        raise
    return result["vault"]


def unlock_and_review(
    db_path: Path, data: dict, bot_id: str, on_unlock=None, on_review=None
) -> int:
    """Unlock window, build the report, then the review window.

    When to use: from `proposed_issues.py DB INPUT.json --bot-id ID --review`
    with a display. Returns run_review()'s code, or 3 if the vault stayed
    locked. on_unlock/on_review are test hooks.
    """
    vault = unlock(db_path, bot_id, on_ready=on_unlock)
    if vault is None:
        return 3
    try:
        report = rs.pi.build_report(data, vault, bot_id)
        return run_review(report, vault, bot_id, on_ready=on_review)
    finally:
        vault.close()


# ---- the app: start screen, files screen, then the review -----------------
def run_app(db: Path, hooks: dict | None = None, kdf: dict | None = None) -> int:
    """Start screen, file screen, then the review window. 0 when confirmed.

    When to use: from va_stack_app.run_gui(). hooks (tests only): {"start":
    f(win), "files": f(win), "review": f(win, vault)} drive each screen with
    real widget events. Returns 1 if a window was closed first, 2 if the
    review was saved for later, 3 if the vault stayed locked.
    """
    va = _sibling("va_stack_app")
    app()
    db = Path(db)
    new = not db.exists()
    state = {"vault": None, "report": None}
    win = Window(va.START_TITLE)
    win._hooks = dict(hooks or {})
    review_hook = win._hooks.pop("review", None)
    win.resize(720, 640)
    body = QVBoxLayout(win)

    def start_screen() -> None:
        _clear(body)
        win.screen_name = "start"
        title = "Create your private vault" if new else "Unlock your vault"
        body.addWidget(_label(title, size=18, bold=True, name="title"))
        body.addWidget(_label(f"Vault file: {db}"))
        box = QPlainTextEdit(rv.CONSENT_TEXT)
        box.setReadOnly(True)
        body.addWidget(box)
        tick = QCheckBox("I have read this and consent")
        tick.setObjectName("consent")
        body.addWidget(tick)
        body.addWidget(_label("Passphrase", size=14))
        pw = QLineEdit()
        pw.setObjectName("passphrase")
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        body.addWidget(pw)
        again = None
        if new:
            body.addWidget(_label("Type the passphrase again", size=14))
            again = QLineEdit()
            again.setObjectName("again")
            again.setEchoMode(QLineEdit.EchoMode.Password)
            body.addWidget(again)

        def go() -> None:
            if not tick.isChecked():
                info(win, "Consent needed", "Tick the consent box first.")
                return
            buf = bytearray(pw.text().encode())
            rep = bytearray(again.text().encode()) if again is not None else None
            pw.clear()
            if again is not None:
                again.clear()
            try:
                state["vault"] = va._open_or_create(db, buf, rep, kdf)
            except ValueError as exc:
                info(win, "Vault", str(exc))
                return
            except rv.WrongPassword:
                error(win, "Locked", "Wrong passphrase. Try again.")
                return
            finally:
                rv.zeroize(buf)
                if rep is not None:
                    rv.zeroize(rep)
            files_screen()

        body.addWidget(_button("Create vault" if new else "Unlock", go))
        win.run_hook("start")

    def files_screen() -> None:
        _clear(body)
        win.screen_name = "files"
        vault = state["vault"]
        body.addWidget(_label("Your medical files", size=18, bold=True, name="title"))
        body.addWidget(
            _label(
                "Files are copied into your encrypted vault on this computer. "
                "Your original files are not changed."
            )
        )
        listing = QVBoxLayout()
        body.addLayout(listing)
        status = _label("", size=14, name="status")
        review_btn = _button(va.START_REVIEW, lambda: start_review())

        def refresh() -> list:
            _clear(listing)
            files = vault.list_files(bot_id=va.BOT_ID)
            for name, pages in files:
                listing.addWidget(_label(f"{name} ({pages} pages)", size=14))
            if not files:
                listing.addWidget(_label("No files yet.", size=14))
            review_btn.setEnabled(bool(files))
            return files

        def choose() -> None:
            chosen = pick_file(win, va.CHOOSE)
            if not chosen:
                return
            try:
                pages = vault.ingest_file(Path(chosen), bot_id=va.BOT_ID)
            except (rv.VaultError, OSError, ValueError) as exc:
                error(win, "Could not import", str(exc))
                return
            status.setText(f"Imported {Path(chosen).name}: {pages} pages")
            refresh()

        def start_review() -> None:
            report = va.build_review(vault)
            if report is None:
                info(
                    win,
                    "Nothing to review yet",
                    "No supported condition was found in these files. "
                    "Add another file.",
                )
                return
            state["report"] = report
            win.screen_name = "review"
            win.close()

        body.addWidget(_button(va.CHOOSE, choose))
        body.addWidget(status)
        body.addWidget(review_btn)
        body.addStretch(1)
        refresh()
        win.run_hook("files")

    start_screen()
    vault = None
    try:
        _exec(win)
        vault = state["vault"]
        if vault is None:
            return 3
        if state["report"] is None:
            return 1
        return run_review(state["report"], vault, va.BOT_ID, on_ready=review_hook)
    finally:
        vault = vault or state["vault"]
        if vault is not None:
            vault.close()


def shoot(widget: QWidget, path: Path) -> Path:
    """Save a PNG of a window (works offscreen too).

    A window that is not on screen is shown for the picture (QPdfView only
    draws pages for a visible view) and hidden again. QPdfView draws pages
    in the background, so give it a moment first.
    """
    was_hidden = not widget.isVisible()
    if was_hidden:
        widget.show()
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.02)
    path = Path(path)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    saved = widget.grab().save(buf, "PNG")
    buf.close()
    if was_hidden:
        widget.hide()
    if not saved:
        raise OSError(f"could not save {path.name}")
    root = safe_path.pin_root(path.parent)  # pinned; O_NOFOLLOW via dir fd
    return safe_path.write_replace(root, path.name, bytes(data.data()))
