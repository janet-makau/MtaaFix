"""The report lifecycle: every rule about who may change a report's status, and how.

Statuses (SRS glossary):  Submitted -> Assigned -> In Progress -> Resolved
                          any open status -> Closed (with a reason)       REQ-27
                          Resolved or Closed -> Submitted (reopen)        REQ-28

All status changes go through this module, so the rules live in one place and every change
is recorded in the status history (REQ-20) and emailed to residents (REQ-22).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case

from ..extensions import db
from ..models import (Assignment, CloseReason, Priority, Report, ReportFollower, Role, Status, StatusChange,
                      User, utcnow)
from . import notifications

# The only forward moves a Field Officer may make, and only on their own reports (REQ-19).
FORWARD = {Status.ASSIGNED: Status.IN_PROGRESS, Status.IN_PROGRESS: Status.RESOLVED}


class WorkflowError(Exception):
    """A rule was broken. The message is safe to show to the user."""


@dataclass
class Outcome:
    change: StatusChange
    email_sent: bool   # False means the change was saved but the email failed (REQ-22)


# ---------------------------------------------------------------- queries
def allowed_next_statuses(report: Report, user: User) -> list[str]:
    """Statuses this user may move the report to with a simple status update."""
    nxt = FORWARD.get(report.status)
    if nxt is None:
        return []
    if user.role == Role.AUTHORITY_ADMIN:
        return [nxt]
    if user.role == Role.FIELD_OFFICER and report.assigned_officer_id == user.id:
        return [nxt]
    return []


def open_reports_for_officer(officer: User) -> list[Report]:
    """REQ-18: the officer's open reports, most urgent first (High, Medium, Low, not set), oldest first within each."""
    urgency = case({Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}, value=Report.priority, else_=3)
    return (Report.query.filter(Report.assigned_officer_id == officer.id, Report.status.in_(Status.OPEN))
            .order_by(urgency, Report.created_at).all())


def active_officers() -> list[User]:
    return (User.query.filter_by(role=Role.FIELD_OFFICER, active=True).order_by(User.full_name).all())


def is_stale(report: Report, days: int, now: datetime | None = None) -> bool:
    """REQ-30: an open report whose status has not changed for `days` days."""
    now = now or utcnow()
    return report.is_open and report.status_changed_at <= now - timedelta(days=days)


# ---------------------------------------------------------------- actions
def assign(report: Report, officer: User, actor: User, note: str | None = None) -> Outcome:
    """REQ-17: the Authority Administrator assigns a Submitted report to an active Field Officer."""
    _require_admin(actor)
    if report.status != Status.SUBMITTED:
        raise WorkflowError("Only Submitted reports can be assigned. Use Reassign for reports already assigned.")
    _require_active_officer(officer)
    _set_officer(report, officer, actor)
    return _change_status(report, Status.ASSIGNED, actor, note or f"Assigned to {officer.full_name}.")


def reassign(report: Report, officer: User, actor: User, note: str | None) -> Outcome:
    """REQ-25: move an Assigned or In Progress report to another Field Officer, with a reason."""
    _require_admin(actor)
    if report.status not in (Status.ASSIGNED, Status.IN_PROGRESS):
        raise WorkflowError("Only Assigned or In Progress reports can be reassigned.")
    if not note:
        raise WorkflowError("Give a reason for reassigning the report.")
    _require_active_officer(officer)
    if officer.id == report.assigned_officer_id:
        raise WorkflowError(f"The report is already assigned to {officer.full_name}.")
    _set_officer(report, officer, actor)
    return _change_status(report, Status.ASSIGNED, actor, f"Reassigned to {officer.full_name}. Reason: {note}")


def update_status(report: Report, new_status: str, actor: User, note: str | None = None) -> Outcome:
    """REQ-19: move a report one step forward (Assigned -> In Progress -> Resolved)."""
    allowed = allowed_next_statuses(report, actor)
    if new_status not in allowed:
        if actor.role == Role.FIELD_OFFICER and report.assigned_officer_id != actor.id:
            raise WorkflowError("You can only update reports that are assigned to you.")
        hint = f" The allowed next status is {Status.LABELS[allowed[0]]}." if allowed else ""
        raise WorkflowError("That status change is not allowed." + hint)
    return _change_status(report, new_status, actor, note)


def close(report: Report, reason: str, actor: User, note: str | None = None,
          original_reference: str | None = None) -> Outcome:
    """REQ-27: close a report that will not be fixed, with a reason."""
    _require_admin(actor)
    if not report.is_open:
        raise WorkflowError("Only open reports can be closed.")
    if reason not in CloseReason.LABELS:
        raise WorkflowError("Choose a reason for closing the report.")

    text = f"Closed: {CloseReason.LABELS[reason]}."
    if reason == CloseReason.DUPLICATE:
        original = Report.query.filter_by(reference=(original_reference or "").strip().upper()).first()
        if original is None or original.id == report.id:
            raise WorkflowError("Enter the valid reference number of the original report.")
        report.duplicate_of = original
        _link_residents_to(original, report)
        text += f" Original report: {original.reference}."
    if note:
        text += f" {note}"
    return _change_status(report, Status.CLOSED, actor, text)


def reopen(report: Report, actor: User, note: str | None) -> Outcome:
    """REQ-28: reopen a Resolved or Closed report so it can be reviewed and assigned again."""
    _require_admin(actor)
    if report.status not in (Status.RESOLVED, Status.CLOSED):
        raise WorkflowError("Only Resolved or Closed reports can be reopened.")
    if not note:
        raise WorkflowError("Give a reason for reopening the report.")
    report.assigned_officer = None
    report.duplicate_of = None
    return _change_status(report, Status.SUBMITTED, actor, f"Reopened. Reason: {note}")


# ---------------------------------------------------------------- helpers
def _change_status(report: Report, new_status: str, actor: User, note: str | None) -> Outcome:
    old_status = report.status
    change = StatusChange(report=report, old_status=old_status, new_status=new_status,
                          note=note or None, changed_by=actor, changed_at=utcnow())
    report.status = new_status
    report.status_changed_at = change.changed_at
    db.session.add(change)
    db.session.commit()   # the change is saved before any email is attempted (REQ-22)

    email_sent = True
    if new_status != old_status:
        email_sent = notifications.notify_status_change(report, change)
    return Outcome(change=change, email_sent=email_sent)


def _set_officer(report: Report, officer: User, actor: User) -> None:
    report.assigned_officer = officer
    db.session.add(Assignment(report=report, officer=officer, assigned_by=actor))


def _link_residents_to(original: Report, duplicate: Report) -> None:
    """Residents of a duplicate report follow the original, so they receive its updates."""
    resident_ids = {duplicate.resident_id} | {f.resident_id for f in duplicate.followers}
    already = {original.resident_id} | {f.resident_id for f in original.followers}
    for resident_id in resident_ids - already:
        db.session.add(ReportFollower(report=original, resident_id=resident_id))


def _require_admin(actor: User) -> None:
    if actor.role != Role.AUTHORITY_ADMIN:
        raise WorkflowError("Only the Authority Administrator can do this.")


def _require_active_officer(officer: User | None) -> None:
    if officer is None or officer.role != Role.FIELD_OFFICER or not officer.active:
        raise WorkflowError("Choose an active Field Officer.")
