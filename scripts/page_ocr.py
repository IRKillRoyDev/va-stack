"""Offline OCR for pages with no text layer: RapidOCR on onnxruntime.

The OCR models are the three inside the pinned rapidocr wheel (PP-OCRv6
small detection and recognition, PP-OCR mobile v2.0 text-angle classifier;
Apache-2.0, see the wheel's MODEL_LICENSES.md). The engine is built from
explicit paths to those bundled files, so nothing is downloaded at runtime,
and this module has no network code. A page is rendered in memory with
QtPdf at DPI (200: the best accuracy and speed in the benchmark on public
blank VA forms) and read at RapidOCR's default 2000-pixel cap.

Every text the engine reads goes through identifier_detect.scan() (by
local_search.redact, the same call the form blocklist makes) inside this
module before anything is returned: the raw text never leaves _read().
Callers get a PageText with the scanned text and the identifier kinds found.

When to use: field_map's local blank-form check, for a page whose text layer
is empty. Raises OcrError when OCR is not installed or fails, so a caller
can keep its "not checked" warning.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import sys
from pathlib import Path

DPI = 200
MODEL_FILES = {
    "det": "PP-OCRv6_det_small.onnx",
    "rec": "PP-OCRv6_rec_small.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}


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


local_search = _load("local_search")


class OcrError(RuntimeError):
    """OCR is not available or failed. Messages never hold page text."""


@dataclasses.dataclass(frozen=True)
class PageText:
    """What OCR read on one page, after identifier_detect.scan()."""

    text: str  # identifiers replaced by [REDACTED-<KIND>] tags
    kinds: dict  # identifier kind -> count


_ENGINE = None


def model_paths() -> dict:
    """The bundled model files, by role (det, rec, cls)."""
    spec = importlib.util.find_spec("rapidocr")
    if spec is None or spec.origin is None:
        raise OcrError("RapidOCR is not installed")
    folder = Path(spec.origin).resolve().parent / "models"
    return {role: folder / name for role, name in MODEL_FILES.items()}


def available() -> bool:
    """True when rapidocr, onnxruntime and the three bundled models are there."""
    try:
        if importlib.util.find_spec("onnxruntime") is None:
            return False
        return all(p.is_file() for p in model_paths().values())
    except (OcrError, ImportError, ValueError):
        return False


def _engine():
    global _ENGINE
    if _ENGINE is None:
        paths = model_paths()
        missing = [role for role, p in paths.items() if not p.is_file()]
        if missing:  # never fall back to a download
            raise OcrError("a bundled OCR model is missing")
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        import rapidocr

        _ENGINE = rapidocr.RapidOCR(
            params={
                "Det.model_path": str(paths["det"]),
                "Rec.model_path": str(paths["rec"]),
                "Cls.model_path": str(paths["cls"]),
                "Global.log_level": "warning",
            }
        )
    return _ENGINE


def _read(rgb) -> str:
    """Raw OCR text of an RGB image array. Never returned outside this module."""
    result = _engine()(rgb)
    return "\n".join(result.txts or ())


def read_image(rgb) -> PageText:
    """OCR an RGB image array (height x width x 3); the text comes back scanned."""
    if not available():
        raise OcrError("offline OCR is not available")
    try:
        raw = _read(rgb)
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("OCR failed on this page") from exc
    text, kinds = local_search.redact(raw)
    del raw
    return PageText(text=text, kinds=dict(kinds))


def render_page(pdf_bytes: bytes, index: int, dpi: int = DPI):
    """Render one PDF page to an RGB array in memory with QtPdf."""
    import numpy as np
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QImage
    from PySide6.QtPdf import QPdfDocument

    buf = QBuffer()
    buf.setData(QByteArray(pdf_bytes))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument(None)
    doc.load(buf)
    if doc.status() != QPdfDocument.Status.Ready or not 0 <= index < doc.pageCount():
        raise OcrError("the page could not be rendered")
    size = doc.pagePointSize(index)
    image = doc.render(
        index, QSize(round(size.width() * dpi / 72), round(size.height() * dpi / 72))
    )
    if image.isNull():
        raise OcrError("the page could not be rendered")
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    raw = np.frombuffer(image.constBits(), dtype=np.uint8, count=stride * height)
    rows = raw.reshape(height, stride)
    rgb = rows[:, slice(0, width * 3)].reshape(height, width, 3).copy()
    doc.close()
    return rgb


def read_pdf_page(pdf_bytes: bytes, index: int, dpi: int = DPI) -> PageText:
    """Render page index (0-based) and OCR it; the text comes back scanned."""
    if not available():
        raise OcrError("offline OCR is not available")
    try:
        rgb = render_page(pdf_bytes, index, dpi)
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("the page could not be rendered") from exc
    return read_image(rgb)
