"""Connects the AI suggestions to the report workflow (SDS 4.3 process 2.0 and 3.0; REQ-9 to REQ-14).

services/ai.py talks to the AI service. This module decides *when* to ask, remembers what was
shown to the resident, and records what people finally chose:

  1. While the resident fills in the form, the page asks for a category and a duplicate check.
     What was suggested is remembered in the resident's (signed) session, keyed by the form's
     one-time token, so the browser cannot invent or change it.
  2. When the report is saved, those suggestions are written to ai_suggestions in the same
     transaction as the report. If the resident joins an existing report instead, the duplicate
     suggestion is recorded against that report.
  3. After the report is saved, a priority is suggested for the Authority Administrator, who
     later confirms or changes it.
"""
from __future__ import annotations

from flask import session

from ..extensions import db
from ..models import AISuggestion, Category, Report, ReportFollower, User
from . import ai

_SESSION_KEY = "ai_form"
_MAX_REMEMBERED_FORMS = 3            # keep the session cookie small
NEW_REPORT = "new report"            # final value when the resident ignores a duplicate warning
JOINED = "joined"


# ---------------------------------------------------------------- while the form is open
def suggest_category(token: str, description: str) -> Category | None:
    categories = Category.query.filter_by(active=True).order_by(Category.name).all()
    category = ai.suggest_category(description, categories)
    _remember(token, category=category.name if category else None)
    return category


def check_duplicate(token: str, description: str, category_id: int,
                    latitude: float, longitude: float) -> Report | None:
    candidates = ai.nearby_open_reports(category_id, latitude, longitude)
    match = ai.check_duplicate(description, candidates) if candidates else None
    _remember(token, duplicate_id=match.id if match else None)
    return match


def shown_duplicate(token: str) -> Report | None:
    """The duplicate that was shown for this form, if any (the only report the resident may join)."""
    report_id = _forms().get(token, {}).get("duplicate_id")
    return db.session.get(Report, report_id) if report_id else None


# ---------------------------------------------------------------- outcomes
def join_report(resident: User, report: Report, token: str) -> None:
    """REQ-11: the resident follows the existing report instead of creating a new one."""
    already = report.resident_id == resident.id or any(f.resident_id == resident.id for f in report.followers)
    if not already:
        db.session.add(ReportFollower(report=report, resident_id=resident.id))
    ai.record_suggestion(ai.KIND_DUPLICATE, report.reference, JOINED, report=report, accepted=True)
    db.session.commit()
    _forget(token)


def record_form_suggestions(report: Report, token: str) -> None:
    """Add the category and duplicate suggestions shown on this form. Called before the report is
    committed, so they are saved in the same transaction (see create_report's before_commit)."""
    shown = _forms().get(token, {})
    if "category" in shown:
        ai.record_suggestion(ai.KIND_CATEGORY, shown["category"], report.category.name, report=report)
    if shown.get("duplicate_id"):
        original = db.session.get(Report, shown["duplicate_id"])
        if original is not None:
            ai.record_suggestion(ai.KIND_DUPLICATE, original.reference, NEW_REPORT, report=report,
                                 accepted=False)


def forget_form(token: str) -> None:
    _forget(token)


def suggest_priority(report: Report) -> AISuggestion | None:
    """REQ-12: ask for a priority once the report exists; the administrator decides later."""
    result = ai.suggest_priority(report.description, report.category.name)
    if result is None:
        return None
    row = ai.record_suggestion(ai.KIND_PRIORITY, result.priority, None, report=report, reasons=result.reasons)
    db.session.commit()
    return row


def priority_suggestion(report: Report) -> AISuggestion | None:
    return (AISuggestion.query.filter_by(report_id=report.id, kind=ai.KIND_PRIORITY)
            .order_by(AISuggestion.id.desc()).first())


def confirm_priority(report: Report, priority: str) -> None:
    """REQ-14: record whether the administrator kept or changed the suggested priority.
    The caller commits together with the new priority."""
    row = priority_suggestion(report)
    if row is not None:
        row.final_value = priority
        row.accepted = (row.suggested_value or "").lower() == priority.lower()


# ---------------------------------------------------------------- session bookkeeping
def _forms() -> dict:
    return session.get(_SESSION_KEY, {})


def _remember(token: str, **values) -> None:
    if not token:
        return
    forms = dict(_forms())
    entry = dict(forms.pop(token, {}))
    entry.update(values)
    forms[token] = entry
    while len(forms) > _MAX_REMEMBERED_FORMS:          # drop the oldest form
        forms.pop(next(iter(forms)))
    session[_SESSION_KEY] = forms


def _forget(token: str) -> None:
    forms = dict(_forms())
    if forms.pop(token, None) is not None:
        session[_SESSION_KEY] = forms
