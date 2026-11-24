"""AI evaluation against the prepared test set (SRS 6.4, AIT-1 to AIT-7).

    flask --app wsgi evaluate-ai                 # run the 100 test reports through the AI
    flask --app wsgi ai-usage                    # AIT-6 figures from user testing

How the test is run:
  * Category (AIT-3): each description is sent to suggest_category with the system's active
    categories, exactly as on the report form.
  * Priority (AIT-5): each description is sent to suggest_priority with its agreed category,
    as happens after a report is saved.
  * Duplicates (AIT-4): reports are "submitted" one after another in file order. For each one,
    the earlier reports of the same agreed category within DUPLICATE_RADIUS_M are the candidates
    (nearest first, at most DUPLICATE_MAX_CANDIDATES), as in the live system. Nothing is written
    to the database.
  * Every AI request is timed and its outcome counted from the AI_CALL log lines (AIT-6).
All results are reported as measured, including missed targets (AIT-7).
"""
from __future__ import annotations

import csv
import json
import logging
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from flask import current_app, render_template
from sqlalchemy import func

from ..extensions import db
from ..models import AISuggestion, Category
from . import ai

LANGUAGES = {"en": "English", "sw": "Kiswahili", "mixed": "Mixed (English and Kiswahili)"}
PRIORITIES = ("High", "Medium", "Low")
COLUMNS = ("id", "language", "category", "priority", "duplicate_group", "latitude", "longitude", "description")

# Targets from SRS Table 19. The figures are targets for the prototype, not results.
TARGETS = {
    "min_duplicate_pairs": 20,          # AIT-1
    "min_other_language_reports": 20,   # AIT-2
    "category_accuracy": 0.80,          # AIT-3
    "duplicate_precision": 0.70,        # AIT-4
    "duplicate_recall": 0.70,           # AIT-4
    "priority_agreement": 0.70,         # AIT-5
    "max_high_as_low": 0.05,            # AIT-5
}

_AI_CALL_RE = re.compile(r"AI_CALL kind=(\w+) outcome=(\w+) ms=(\d+)")


class DatasetError(ValueError):
    """The test set file cannot be used. The message says which row and why."""


@dataclass(frozen=True)
class TestReport:
    __test__ = False          # not a pytest test class
    id: str
    language: str
    category: str
    priority: str
    duplicate_group: str
    latitude: float
    longitude: float
    description: str


@dataclass
class Prediction:
    id: str
    language: str
    category_expected: str
    category_suggested: str | None
    priority_expected: str
    priority_suggested: str | None
    priority_reasons: list[str] = field(default_factory=list)
    duplicate_expected: str | None = None       # earlier report of the same pair, if any
    duplicate_suggested: str | None = None
    duplicate_candidates: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 1. load and check the test set
def load_dataset(path: str | Path, category_names: list[str]) -> list[TestReport]:
    """Read the CSV and reject anything that would make the results wrong."""
    names = {n.lower(): n for n in category_names}
    reports, seen_ids, groups = [], set(), defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(f"Missing column(s): {', '.join(missing)}")
        for line, row in enumerate(reader, start=2):
            where = f"Row {line} ({row.get('id') or 'no id'})"
            rid = (row["id"] or "").strip()
            if not rid or rid in seen_ids:
                raise DatasetError(f"{where}: id is empty or repeated")
            language = (row["language"] or "").strip().lower()
            if language not in LANGUAGES:
                raise DatasetError(f"{where}: language must be one of {', '.join(LANGUAGES)}")
            category = names.get((row["category"] or "").strip().lower())
            if category is None:
                raise DatasetError(f"{where}: category '{row['category']}' is not an active category")
            priority = (row["priority"] or "").strip().capitalize()
            if priority not in PRIORITIES:
                raise DatasetError(f"{where}: priority must be High, Medium or Low")
            try:
                latitude, longitude = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                raise DatasetError(f"{where}: latitude and longitude must be numbers") from None
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise DatasetError(f"{where}: coordinates are out of range")
            description = (row["description"] or "").strip()
            if len(description) < 10:
                raise DatasetError(f"{where}: description must be at least 10 characters")
            group = (row["duplicate_group"] or "").strip()
            report = TestReport(rid, language, category, priority, group, latitude, longitude, description)
            reports.append(report)
            seen_ids.add(rid)
            if group:
                groups[group].append(report)

    for group, members in groups.items():
        if len(members) != 2:
            raise DatasetError(f"Duplicate group {group} has {len(members)} reports; each group needs exactly 2")
        if members[0].category != members[1].category:
            raise DatasetError(f"Duplicate group {group}: both reports must have the same category")
    return reports


def dataset_summary(reports: list[TestReport]) -> dict:
    """AIT-1 and AIT-2: what the test set contains, checked against the proposed minimums."""
    pairs = len({r.duplicate_group for r in reports if r.duplicate_group})
    by_language = Counter(r.language for r in reports)
    other_language = by_language["sw"] + by_language["mixed"]
    return {
        "reports": len(reports),
        "duplicate_pairs": pairs,
        "by_language": {LANGUAGES[k]: by_language[k] for k in LANGUAGES},
        "by_category": dict(Counter(r.category for r in reports).most_common()),
        "by_priority": {p: sum(r.priority == p for r in reports) for p in PRIORITIES},
        "pairs_target_met": pairs >= TARGETS["min_duplicate_pairs"],
        "other_language_reports": other_language,
        "other_language_target_met": other_language >= TARGETS["min_other_language_reports"],
    }


# ---------------------------------------------------------------- 2. run the AI on every report
@dataclass
class _Candidate:
    """What check_duplicate needs from an earlier report (only the description is sent)."""
    id: str
    description: str


def run(reports: list[TestReport], categories: list[Category], pause_seconds: float = 0.0,
        progress=None) -> tuple[list[Prediction], list[dict]]:
    """Return one Prediction per report, and the AI_CALL records captured during the run."""
    radius = current_app.config["DUPLICATE_RADIUS_M"]
    limit = current_app.config["DUPLICATE_MAX_CANDIDATES"]
    calls: list[dict] = []
    capture = _CallCapture(calls)
    previous_level = ai.log.level
    ai.log.setLevel(logging.INFO)          # AI_CALL lines are INFO; make sure they are not filtered out
    ai.log.addHandler(capture)
    try:
        predictions, submitted = [], []
        for index, report in enumerate(reports, start=1):
            category = ai.suggest_category(report.description, categories)
            priority = ai.suggest_priority(report.description, report.category)

            nearby = sorted(
                ((ai.distance_m(report.latitude, report.longitude, e.latitude, e.longitude), e)
                 for e in submitted if e.category == report.category),
                key=lambda pair: pair[0])
            candidates = [e for d, e in nearby if d <= radius][:limit]
            match = ai.check_duplicate(report.description,
                                       [_Candidate(e.id, e.description) for e in candidates]) if candidates else None

            expected = next((e.id for e in submitted
                             if report.duplicate_group and e.duplicate_group == report.duplicate_group), None)
            predictions.append(Prediction(
                id=report.id, language=report.language,
                category_expected=report.category, category_suggested=category.name if category else None,
                priority_expected=report.priority,
                priority_suggested=priority.priority.capitalize() if priority else None,
                priority_reasons=priority.reasons if priority else [],
                duplicate_expected=expected, duplicate_suggested=match.id if match else None,
                duplicate_candidates=[e.id for e in candidates],
            ))
            submitted.append(report)
            if progress:
                progress(index, len(reports))
            if pause_seconds:
                time.sleep(pause_seconds)       # stay well inside the API rate limit
    finally:
        ai.log.removeHandler(capture)
        ai.log.setLevel(previous_level)
    return predictions, calls


class _CallCapture(logging.Handler):
    def __init__(self, calls: list[dict]):
        super().__init__(level=logging.INFO)
        self.calls = calls

    def emit(self, record: logging.LogRecord) -> None:
        found = _AI_CALL_RE.search(record.getMessage())
        if found:
            self.calls.append({"kind": found[1], "outcome": found[2], "ms": int(found[3])})


# ---------------------------------------------------------------- 3. measure
def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def category_metrics(predictions: list[Prediction]) -> dict:
    """AIT-3 (and AIT-2 by language): share of reports whose suggestion matches the agreed category."""
    def score(items):
        correct = sum(p.category_suggested == p.category_expected for p in items)
        return {"correct": correct, "total": len(items), "accuracy": _rate(correct, len(items))}

    mistakes = Counter((p.category_expected, p.category_suggested or "No suggestion")
                       for p in predictions if p.category_suggested != p.category_expected)
    result = score(predictions)
    result["by_language"] = {LANGUAGES[k]: score([p for p in predictions if p.language == k]) for k in LANGUAGES}
    result["top_mistakes"] = [{"expected": e, "suggested": s, "count": n} for (e, s), n in mistakes.most_common(5)]
    result["target"] = TARGETS["category_accuracy"]
    result["target_met"] = result["accuracy"] is not None and result["accuracy"] >= TARGETS["category_accuracy"]
    return result


def priority_metrics(predictions: list[Prediction]) -> dict:
    """AIT-5: agreement with the agreed priority, and High reports suggested as Low."""
    def score(items):
        agreed = sum(p.priority_suggested == p.priority_expected for p in items)
        return {"agreed": agreed, "total": len(items), "agreement": _rate(agreed, len(items))}

    high = [p for p in predictions if p.priority_expected == "High"]
    high_as_low = [p.id for p in high if p.priority_suggested == "Low"]
    matrix = {e: {s: 0 for s in (*PRIORITIES, "None")} for e in PRIORITIES}
    for p in predictions:
        matrix[p.priority_expected][p.priority_suggested or "None"] += 1

    result = score(predictions)
    result["by_language"] = {LANGUAGES[k]: score([p for p in predictions if p.language == k]) for k in LANGUAGES}
    result["matrix"] = matrix
    result["high_reports"] = len(high)
    result["high_as_low"] = high_as_low
    result["high_as_low_rate"] = _rate(len(high_as_low), len(high))
    result["target"] = TARGETS["priority_agreement"]
    result["target_met"] = (result["agreement"] is not None and result["agreement"] >= TARGETS["priority_agreement"]
                            and (result["high_as_low_rate"] or 0) <= TARGETS["max_high_as_low"])
    return result


def duplicate_metrics(predictions: list[Prediction]) -> dict:
    """AIT-4: precision of the warnings shown, and recall over the true duplicate pairs."""
    warnings = [p for p in predictions if p.duplicate_suggested]
    true_warnings = [p for p in warnings if p.duplicate_suggested == p.duplicate_expected]
    pairs = [p for p in predictions if p.duplicate_expected]           # second report of each pair
    detected = [p for p in pairs if p.duplicate_suggested == p.duplicate_expected]
    precision, recall = _rate(len(true_warnings), len(warnings)), _rate(len(detected), len(pairs))
    return {
        "warnings": len(warnings), "true_warnings": len(true_warnings), "precision": precision,
        "pairs": len(pairs), "detected": len(detected), "recall": recall,
        "false_warnings": [{"report": p.id, "warned_about": p.duplicate_suggested} for p in warnings
                           if p.duplicate_suggested != p.duplicate_expected],
        "missed_pairs": [{"report": p.id, "original": p.duplicate_expected,
                          "original_was_a_candidate": p.duplicate_expected in p.duplicate_candidates}
                         for p in pairs if p.duplicate_suggested != p.duplicate_expected],
        "target_precision": TARGETS["duplicate_precision"], "target_recall": TARGETS["duplicate_recall"],
        "target_met": (precision is not None and recall is not None
                       and precision >= TARGETS["duplicate_precision"] and recall >= TARGETS["duplicate_recall"]),
    }


def call_metrics(calls: list[dict]) -> dict:
    """AIT-6: how often the AI service failed, and how long suggestions took."""
    times = sorted(c["ms"] for c in calls if c["outcome"] == "ok")
    outcomes = Counter(c["outcome"] for c in calls)
    return {
        "requests": len(calls),
        "ok": outcomes["ok"], "failed": outcomes["failed"], "invalid": outcomes["invalid"],
        "failure_rate": _rate(outcomes["failed"] + outcomes["invalid"], len(calls)),
        "by_kind": {k: sum(c["kind"] == k for c in calls) for k in (ai.KIND_CATEGORY, ai.KIND_DUPLICATE, ai.KIND_PRIORITY)},
        "ms_mean": round(statistics.fmean(times)) if times else None,
        "ms_median": round(statistics.median(times)) if times else None,
        "ms_p90": times[min(len(times) - 1, int(round(0.9 * (len(times) - 1))))] if times else None,
        "ms_max": times[-1] if times else None,
    }


def usage_metrics(log_path: str | Path | None) -> dict:
    """AIT-6 from user testing: acceptance of each kind of suggestion (database) and the AI_CALL
    lines in the application log (failures and response times). Rotated log files are included."""
    rows = (db.session.query(AISuggestion.kind, AISuggestion.accepted, func.count())
            .group_by(AISuggestion.kind, AISuggestion.accepted).all())
    acceptance = {}
    for kind in (ai.KIND_CATEGORY, ai.KIND_DUPLICATE, ai.KIND_PRIORITY):
        counts = {accepted: n for k, accepted, n in rows if k == kind}
        decided = counts.get(True, 0) + counts.get(False, 0)
        acceptance[kind] = {"suggestions": sum(counts.values()), "accepted": counts.get(True, 0),
                            "changed": counts.get(False, 0), "undecided": counts.get(None, 0),
                            "acceptance_rate": _rate(counts.get(True, 0), decided)}

    calls = []
    if log_path:
        base = Path(log_path)
        for file in sorted(base.parent.glob(base.name + "*")):
            with open(file, encoding="utf-8", errors="replace") as f:
                calls += [{"kind": m[1], "outcome": m[2], "ms": int(m[3])}
                          for m in map(_AI_CALL_RE.search, f) if m]
    return {"acceptance": acceptance, "calls": call_metrics(calls)}


# ---------------------------------------------------------------- 4. write the results
def evaluate(dataset_path: str | Path, out_dir: str | Path, pause_seconds: float = 0.0,
             limit: int | None = None, progress=None) -> dict:
    categories = Category.query.filter_by(active=True).order_by(Category.name).all()
    reports = load_dataset(dataset_path, [c.name for c in categories])
    if limit:
        reports = reports[:limit]
    predictions, calls = run(reports, categories, pause_seconds, progress)
    results = {
        "dataset": dataset_summary(reports),
        "model": current_app.config["AI_MODEL"],
        "category": category_metrics(predictions),
        "priority": priority_metrics(predictions),
        "duplicates": duplicate_metrics(predictions),
        "calls": call_metrics(calls),
    }
    write_results(results, predictions, out_dir)
    return results


def write_results(results: dict, predictions: list[Prediction] | None, out_dir: str | Path,
                  name: str = "results") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    if predictions is not None:
        with open(out / "predictions.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(predictions[0]).keys()) if predictions else ["id"])
            writer.writeheader()
            for p in predictions:
                row = asdict(p)
                row["priority_reasons"] = " | ".join(row["priority_reasons"])
                row["duplicate_candidates"] = " ".join(row["duplicate_candidates"])
                writer.writerow(row)
    report = out / f"{name}.md"
    report.write_text(render_template("evaluation/results.md", r=results, pct=_pct), encoding="utf-8")
    return report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
