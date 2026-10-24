#!/usr/bin/env python3
"""Score record sentences against fetched schedule criteria.

Confidence is how many distinctive words of a criterion appear in a
sentence. It is not a diagnosis and not a VA decision. The user reviews
each hit before it can be selected.

When to use: after records are in the vault, before the criteria review.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "local_search", Path(__file__).resolve().parent / "local_search.py"
)
local_search = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("local_search", local_search)
_SPEC.loader.exec_module(local_search)

_SKIP = frozenset(
    """with from that this under over without other than into each such more
    only when where which note rate rated rating code diagnostic evaluate
    following there their about after before have been also will your they
    were""".split()
)


def confidence_level(score: int) -> str:
    """Map a 0-100 score to high, medium, or low."""
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


_DIAGNOSIS = re.compile(
    r"\b(?:diagnosis|diagnosed|dx|impression|assessment)\b",
    re.IGNORECASE,
)
_SYMPTOM = re.compile(r"\b(?:reports?|complains?|notes?|endorses|describes)\b", re.I)
_EXAM = re.compile(r"\b(?:exam|examination|objective|observed|reveals)\b", re.I)
_MEASURE = re.compile(r"\d+\s*(?:%|percent|degrees|degree)\b", re.I)
_PAGE_CLINICAL = re.compile(r"\b(?:history|assessment|plan|exam|diagnosis)\b", re.I)


def clearly_diagnosis(sentence: str, criterion: str) -> bool:
    """True when the sentence states this criterion's condition as a diagnosis.

    A symptom or an exam measurement is not a clear diagnosis, even when it
    shares words with the schedule.
    """
    if not _DIAGNOSIS.search(sentence or ""):
        return False
    name = re.split(r"\d", criterion or "", maxsplit=1)[0]
    words = [word.lower() for word in re.findall(r"[A-Za-z]{5,}", name)]
    blob = (sentence or "").lower()
    return any(word in blob for word in words[:4])


def structure_confidence(sentence: str, page: str) -> int:
    """Score a non-diagnosis finding from sentence shape and page context.

    When to use: after a sentence has already matched a schedule criterion.
    The number is not a diagnosis and not a VA decision.
    """
    score = 25
    if _SYMPTOM.search(sentence or ""):
        score += 20
    if _EXAM.search(sentence or ""):
        score += 15
    if _MEASURE.search(sentence or ""):
        score += 20
    if _PAGE_CLINICAL.search(page or ""):
        score += 10
    if (page or "").count(".") >= 2:
        score += 5
    return min(score, 100)


def needed_to_count(dc: str, criterion: str) -> str:
    """What the file still lacks when a finding has no diagnosis.

    When to use: in the Missing Diagnosis section. Names the documents a
    claim uses. Does not tell the user to seek care for a rating.
    """
    shown = " ".join((criterion or "").split())[:120]
    return (
        f"Missing diagnosis for DC {dc}. The records identify a finding"
        f" ({shown}) but do not state a diagnosis of that condition."
        " For this finding to be used in a claim, the file still needs a"
        " current diagnosis of the condition written by a provider, an"
        " in-service event, injury, or exposure, and a medical opinion on"
        " whether the condition is related to that event. This finding is"
        " not that diagnosis. Confirm what is missing with a VA-accredited"
        " representative."
    )


def missing_diagnoses(candidates: list[dict]) -> list[dict]:
    """Findings whose diagnostic code has no clear diagnosis in the scan."""
    diagnosed = {
        item["dc"]
        for item in candidates
        if clearly_diagnosis(item["sentence"], item["criterion"])
    }
    groups: dict[str, list[dict]] = {}
    for item in candidates:
        if item["dc"] in diagnosed:
            continue
        if clearly_diagnosis(item["sentence"], item["criterion"]):
            continue
        groups.setdefault(item["dc"], []).append(item)
    missing = []
    for dc, items in groups.items():
        missing.append(
            {
                "dc": dc,
                "finding_count": len(items),
                "criterion": items[0]["criterion"],
                "needed": needed_to_count(dc, items[0]["criterion"]),
            }
        )
    missing.sort(key=lambda item: item["dc"])
    return missing


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    out = []
    for word in words:
        if word in _SKIP:
            continue
        if word.isdigit() or len(word) >= 4:
            out.append(word)
    return out


def _phrases(catalog: dict, models: dict | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for code, entry in catalog.items():
        cite = (entry.get("cite") or "").strip()
        if cite:
            found.append((str(code), cite))
    for code, model in (models or {}).get("dcs", {}).items():
        for factor in model.get("factors") or []:
            for band in factor.get("bands") or []:
                cite = (band.get("cite") or "").strip()
                if cite:
                    found.append((str(code), cite))
    return found


def _score(sentence_tokens: set[str], phrase_tokens: list[str]) -> int | None:
    if len(phrase_tokens) < 1:
        return None
    hits = [token for token in phrase_tokens if token in sentence_tokens]
    if not hits:
        return None
    if len(hits) == 1 and not (len(hits[0]) >= 8 and not hits[0].isdigit()):
        return None
    return round(100 * len(hits) / len(phrase_tokens))


def match_pages(
    pages: list[dict], catalog: dict, models: dict | None = None
) -> list[dict]:
    """Return candidate matches, highest confidence first.

    Each page is {file, page, text}. Sentences are redacted before scoring.
    `raw_sentence` is kept so a PDF viewer can highlight the original line.
    The text shown to review is `sentence`.
    """
    phrases = []
    for code, cite in _phrases(catalog, models):
        tokens = _tokens(cite)
        if tokens:
            phrases.append((code, cite, tokens[:12]))
    candidates = []
    seen: set[tuple] = set()
    for page in pages:
        raw_page = page.get("text") or ""
        redacted, _counts = local_search.redact(raw_page)
        raw_units = local_search.split_sentences(raw_page)
        red_units = local_search.split_sentences(redacted)
        # Pair by index when the split counts match; otherwise score redacted
        # units and use the same text to highlight.
        if len(raw_units) != len(red_units):
            pairs = [(unit, unit) for unit in red_units]
        else:
            pairs = list(zip(raw_units, red_units))
        for raw_unit, red_unit in pairs:
            sentence = " ".join(red_unit.split())
            raw_sentence = " ".join(raw_unit.split())
            if len(sentence) < 20:
                continue
            have = set(_tokens(sentence))
            best = None
            for code, cite, tokens in phrases:
                score = _score(have, tokens)
                if score is None or score < 20:
                    continue
                if best is None or score > best[0]:
                    best = (score, code, cite)
            if best is None:
                continue
            overlap, code, cite = best
            stated = clearly_diagnosis(sentence, cite)
            if stated:
                confidence = 100
            else:
                confidence = structure_confidence(sentence, redacted)
            key = (page["file"], page["page"], code, sentence)
            if key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha256("\x1f".join(str(part) for part in key).encode())
            candidates.append(
                {
                    "candidate_id": digest.hexdigest()[:16],
                    "file": page["file"],
                    "page": page["page"],
                    "sentence": sentence,
                    "raw_sentence": raw_sentence,
                    "dc": code,
                    "criterion": cite[:240],
                    "overlap": overlap,
                    "confidence": confidence,
                    "level": confidence_level(confidence),
                    "clear_diagnosis": stated,
                }
            )
    candidates.sort(key=lambda item: (-item["confidence"], item["dc"], item["file"]))
    return candidates[:100]
