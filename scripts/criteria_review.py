#!/usr/bin/env python3
"""Review state for criteria matches: filter, agree, then select.

A candidate can be selected for processing only after the user agrees
that the highlighted text meets the criterion. Nothing is saved here.

When to use: behind the criteria review window, and in tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RANK = {"low": 1, "medium": 2, "high": 3}


def _matcher():
    name = "criteria_match"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def clearly_diagnosis(sentence: str, criterion: str) -> bool:
    """True when the sentence states this criterion's condition as a diagnosis."""
    return _matcher().clearly_diagnosis(sentence, criterion)


class CriteriaReview:
    """One session of confidence-filtered criteria review."""

    def __init__(self, candidates: list[dict]) -> None:
        self.candidates = list(candidates)
        self.min_level = "medium"
        self.agreed: set[str] = set()
        self.selected: set[str] = set()

    def visible(self) -> list[dict]:
        """Candidates at or above the confidence filter, highest first."""
        floor = _RANK[self.min_level]
        shown = [
            item
            for item in self.candidates
            if not clearly_diagnosis(item["sentence"], item["criterion"])
            and _RANK[item["level"]] >= floor
        ]
        shown.sort(key=lambda item: (-item["confidence"], item["dc"]))
        return shown

    def set_min_level(self, level: str) -> None:
        """Keep high only, medium and high, or every scored candidate."""
        if level not in _RANK:
            raise ValueError("confidence filter must be low, medium, or high")
        self.min_level = level

    def agree(self, candidate_id: str) -> None:
        """Record that the user agreed this text meets the criterion."""
        self._known(candidate_id)
        self.agreed.add(candidate_id)

    def set_selected(self, candidate_id: str, on: bool) -> None:
        """Select an agreed candidate for processing, or clear it."""
        self._known(candidate_id)
        if on and candidate_id not in self.agreed:
            raise ValueError("agree the criterion before selecting it")
        if on:
            self.selected.add(candidate_id)
        else:
            self.selected.discard(candidate_id)

    def chosen(self) -> list[dict]:
        """Selected candidates that are still visible and agreed."""
        visible = {item["candidate_id"] for item in self.visible()}
        return [
            item
            for item in self.candidates
            if item["candidate_id"] in self.selected
            and item["candidate_id"] in self.agreed
            and item["candidate_id"] in visible
        ]

    def for_diagnosis(self) -> list[dict]:
        """Chosen items that clearly state the diagnosis."""
        return [
            item
            for item in self.chosen()
            if clearly_diagnosis(item["sentence"], item["criterion"])
        ]

    def for_sanitization(self) -> list[dict]:
        """Chosen items that are not clearly a diagnosis."""
        return [
            item
            for item in self.chosen()
            if not clearly_diagnosis(item["sentence"], item["criterion"])
        ]

    def to_input(self, track: str = "B1") -> dict:
        """Proposal input built only from clear diagnoses.

        One issue per diagnostic code. Quotes are the redacted sentences.
        Items that are not clearly a diagnosis are left out.
        """
        grouped: dict[str, dict] = {}
        for item in self.for_diagnosis():
            issue = grouped.get(item["dc"])
            if issue is None:
                issue = {
                    "condition": item["criterion"][:80],
                    "basis": "not stated",
                    "approx_date": "",
                    "dc": item["dc"],
                    "findings": {},
                    "evidence": [],
                }
                grouped[item["dc"]] = issue
            issue["evidence"].append(
                {
                    "file": item["file"],
                    "page": item["page"],
                    "quote": item["sentence"],
                }
            )
        return {"track": track, "issues": list(grouped.values())}

    def packet_sentences(self) -> list[dict]:
        """Redacted sentences for items that are not a clear diagnosis."""
        return [
            {
                "file": item["file"],
                "page": item["page"],
                "line": 1,
                "sentence": item["sentence"],
            }
            for item in self.for_sanitization()
        ]

    def _known(self, candidate_id: str) -> None:
        if not any(item["candidate_id"] == candidate_id for item in self.candidates):
            raise KeyError(candidate_id)
