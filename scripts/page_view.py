#!/usr/bin/env python3
"""Render one record page and mark the sentence under review.

PDF pages are drawn with PyMuPDF from the bytes stored in the vault. The
highlight is a box around the original sentence. Text records are not
turned into a fake PDF; the caller highlights the sentence in a text pane.

When to use: from the criteria review window, for the selected candidate.
"""

from __future__ import annotations


def render_pdf_highlight(
    data: bytes, page_number: int, needle: str
) -> tuple[bytes | None, bool]:
    """Return PNG bytes and whether a highlight box was found.

    page_number is 1-based. Returns (None, False) when PyMuPDF is not
    installed or the page does not exist. The PDF bytes are not modified
    on disk; the highlight is drawn on an in-memory copy.
    """
    try:
        import fitz
    except ImportError:
        return None, False
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if page_number < 1 or page_number > doc.page_count:
            return None, False
        page = doc[page_number - 1]
        flat = " ".join((needle or "").split())
        queries = []
        if flat:
            queries.append(flat[:180])
            words = flat.split()
            if len(words) > 6:
                queries.append(" ".join(words[:6]))
        rects = []
        for query in queries:
            rects = page.search_for(query)
            if rects:
                break
        for rect in rects:
            page.add_highlight_annot(rect)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        return pix.tobytes("png"), bool(rects)
    finally:
        doc.close()
