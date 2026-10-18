"""Step (d): offline OCR for pages with no text layer (RapidOCR on onnxruntime).

page_ocr reads a page image with the OCR models bundled in the pinned
rapidocr wheel: nothing is downloaded and no network is used. Every text it
reads goes through identifier_detect.scan() inside page_ocr before anything
is returned, so callers (the field-map blank check today) never see raw OCR
text. field_map's "no text layer, so it was not checked" warning goes away
only for a page OCR actually ran on; when OCR is missing, fails or is
skipped, the warning stays. An identifier OCR finds on a scanned page blocks
the release, as identifier text in a text layer does. Fake data only.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAKE_SSN = "123-45-6789"
NAMES = ("approx_date", "condition", "currently_homeless")


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def po():
    assert (ROOT / "scripts" / "page_ocr.py").exists(), "scripts/page_ocr.py is missing"
    return _load("page_ocr")


@pytest.fixture(scope="module")
def fm():
    return _load("field_map")


def _block_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    _block_network(monkeypatch)


def _image(lines, size=(1000, 80)):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (size[0], size[1] * len(lines) + 40), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=36)
    for k, line in enumerate(lines):
        draw.text((30, 30 + k * size[1]), line, fill="black", font=font)
    return np.array(img)


def _scanned_pdf(path, lines):
    """A fictional scanned form: one page that is only an image of lines."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.fromarray(_image(lines, size=(1700, 90)))
    page = Image.new("RGB", (1700, 2200), "white")
    page.paste(img, (0, 150))
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    buf.seek(0)
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 0, 0, width=612, height=792)
    c.showPage()
    c.save()
    return path


class _Names:
    def data_point_names(self, bot_id):
        return list(NAMES)


def _prepare(fm, pdf, **kw):
    return fm.prepare_request(
        pdf, form_id="21-0781", names_source=_Names(), bot_id=BOT, **kw
    )


def _not_checked(prep):
    return [w for w in prep.warnings if "no text layer" in w]


# ---------------- bundled models, no download ----------------
def test_the_models_are_the_ones_bundled_in_the_pinned_wheel(po):
    import rapidocr

    bundled = Path(rapidocr.__file__).resolve().parent / "models"
    paths = po.model_paths()
    assert set(paths) == {"det", "rec", "cls"}
    for path in paths.values():
        assert (
            path.parent == bundled and path.is_file() and path.stat().st_size > 100_000
        )
    assert po.available()


def test_the_engine_is_built_from_the_bundled_files_only(po, monkeypatch):
    import rapidocr

    seen = {}

    class Spy:
        def __init__(self, config_path=None, params=None):
            seen.update(params or {})

    monkeypatch.setattr(rapidocr, "RapidOCR", Spy)
    monkeypatch.setattr(po, "_ENGINE", None)
    po._engine()
    paths = po.model_paths()
    assert seen["Det.model_path"] == str(paths["det"])
    assert seen["Rec.model_path"] == str(paths["rec"])
    assert seen["Cls.model_path"] == str(paths["cls"])


def test_ocr_reads_text_with_the_network_blocked(po, monkeypatch):
    monkeypatch.setattr(po, "_ENGINE", None)  # build the engine under the block too
    res = po.read_image(_image(["Condition knee pain since 2019"]))
    assert "knee" in res.text.lower() and "condition" in res.text.lower()


def test_page_ocr_has_no_network_code():
    tree = ast.parse((ROOT / "scripts" / "page_ocr.py").read_text(encoding="utf-8"))
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            tops.add(node.module.split(".")[0])
    assert not tops & {"socket", "urllib", "requests", "http", "urllib3", "ssl"}


# ---------------- scanned before anything uses it ----------------
def test_an_ssn_in_an_image_is_scanned_before_it_is_returned(po, monkeypatch):
    detect = _load("identifier_detect")
    real_scan, seen = detect.scan, []

    def spy(text, *a, **k):
        seen.append(text)
        return real_scan(text, *a, **k)

    monkeypatch.setattr(detect, "scan", spy)
    res = po.read_image(_image([f"Veteran SSN {FAKE_SSN}"]))
    assert any(FAKE_SSN in t for t in seen), "scan() never saw the raw OCR text"
    assert FAKE_SSN not in res.text and "6789" not in res.text
    assert "SSN" in res.kinds
    assert "[REDACTED-SSN]" in res.text


def test_the_raw_text_never_leaves_page_ocr(po, monkeypatch, tmp_path):
    class Fake:
        txts = (f"SSN {FAKE_SSN}", "Condition knee")

    monkeypatch.setattr(po, "_ENGINE", lambda img: Fake())
    for res in (
        po.read_image(_image(["x"])),
        po.read_pdf_page(_scanned_pdf(tmp_path / "s.pdf", ["x"]).read_bytes(), 0),
    ):
        assert set(vars(res)) == {"text", "kinds"}
        assert FAKE_SSN not in repr(res) and "6789" not in repr(res)
    tree = ast.parse((ROOT / "scripts" / "page_ocr.py").read_text(encoding="utf-8"))
    users = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(isinstance(n, ast.Attribute) and n.attr == "txts" for n in ast.walk(fn))
    }
    assert users == {"_read"}


def test_a_pdf_page_is_rendered_at_the_benchmarked_dpi(po, tmp_path):
    data = _scanned_pdf(tmp_path / "s.pdf", ["x"]).read_bytes()
    assert po.DPI == 200
    img = po.render_page(data, 0)
    assert img.shape == (2200, 1700, 3)


def test_ocr_unavailable_raises_ocr_error(po, monkeypatch, tmp_path):
    monkeypatch.setattr(po, "available", lambda: False)
    with pytest.raises(po.OcrError):
        po.read_pdf_page(_scanned_pdf(tmp_path / "s.pdf", ["x"]).read_bytes(), 0)


# ---------------- the field-map blank check ----------------
def test_the_not_checked_warning_goes_only_when_ocr_ran(fm, po, tmp_path):
    pdf = _scanned_pdf(tmp_path / "f.pdf", ["Fictional claim form", "Condition"])
    prep = _prepare(fm, pdf)
    assert _not_checked(prep) == []
    assert prep.flat and prep.needs_blank_confirm  # a flat form still asks


@pytest.mark.parametrize("how", ["missing", "fails", "skipped"])
def test_the_not_checked_warning_stays_when_ocr_did_not_run(
    fm, po, tmp_path, monkeypatch, how
):
    pdf = _scanned_pdf(tmp_path / "f.pdf", ["Fictional claim form", "Condition"])
    kw = {}
    if how == "missing":
        monkeypatch.setattr(po, "available", lambda: False)
    elif how == "fails":

        def boom(*a, **k):
            raise RuntimeError("fake OCR failure")

        monkeypatch.setattr(po, "_ENGINE", boom)
    else:
        kw["ocr"] = False
    prep = _prepare(fm, pdf, **kw)
    assert len(_not_checked(prep)) == 1 and "page 1" in _not_checked(prep)[0]
    assert prep.needs_blank_confirm


def test_an_ssn_on_a_scanned_form_never_reaches_anything_unscanned(fm, po, tmp_path):
    pdf = _scanned_pdf(tmp_path / "f.pdf", ["Fictional claim form", f"SSN {FAKE_SSN}"])
    prep = _prepare(fm, pdf)
    assert any("filled in" in w and "SSN" in w for w in prep.warnings)
    assert _not_checked(prep) == []
    prep.answer(True, blank_confirmed=True)
    shown = [
        "\n".join(prep.warnings),
        prep.prompt_text(),
        json.dumps(prep.request),
        json.dumps(prep.consent_detail()),
    ]
    for text in shown:
        assert FAKE_SSN not in text and "6789" not in text
    with pytest.raises(fm.FieldMapError):
        prep.release()  # the page image itself would carry it out


def test_a_clean_scanned_form_still_releases(fm, po, tmp_path):
    pdf = _scanned_pdf(tmp_path / "f.pdf", ["Fictional claim form", "Condition"])
    prep = _prepare(fm, pdf)
    prep.answer(True, blank_confirmed=True)
    payload, data = prep.release()
    assert payload["form"]["page_count"] == 1 and data


# ---------------- pins ----------------
def test_ocr_is_pinned_everywhere():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    cons = (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines()
    proj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for pin in ("rapidocr==3.10.0", "onnxruntime==1.23.2"):
        assert pin in req and pin in cons, pin
        assert f'"{pin}"' in proj, pin
    for pin in (
        "numpy==2.2.6",
        "opencv-python-headless==4.12.0.88",
        "shapely==2.1.2",
        "pyclipper==1.4.0",
        "omegaconf==2.4.0",
        "pyreadline3==3.5.6",
    ):
        assert any(line.split(";")[0].strip() == pin for line in cons), pin
    assert '"page_ocr"' in proj
