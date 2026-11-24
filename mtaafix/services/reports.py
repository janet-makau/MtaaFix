"""Creating and finding reports (REQ-5 to REQ-8, REQ-21, REQ-29)."""
from collections.abc import Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Category, Report, ReportFollower, Status, StatusChange, User, utcnow


def make_reference(report_id: int) -> str:
    """Reference numbers look like MF-000123 (REQ-8)."""
    return f"MF-{report_id:06d}"


def find_by_token(resident: User, token: str | None) -> Report | None:
    if not token:
        return None
    return Report.query.filter_by(resident_id=resident.id, submission_token=token).first()


def create_report(resident: User, *, description: str, category: Category, latitude: float,
                  longitude: float, photo_url: str | None, token: str,
                  before_commit: Callable[[Report], None] | None = None) -> tuple[Report, bool]:
    """Save a new report with status Submitted.

    Returns (report, created). If the same form was already submitted (double click or page
    refresh), the existing report is returned with created=False and nothing new is saved (REQ-29).
    `before_commit` can add related rows (such as AI suggestions) to the same transaction.
    """
    existing = find_by_token(resident, token)
    if existing:
        return existing, False

    now = utcnow()
    report = Report(resident=resident, category=category, description=description,
                    latitude=latitude, longitude=longitude, photo_url=photo_url,
                    status=Status.SUBMITTED, submission_token=token,
                    created_at=now, updated_at=now, status_changed_at=now)
    db.session.add(report)
    try:
        db.session.flush()                       # gives the report its id
        report.reference = make_reference(report.id)
        db.session.add(StatusChange(report=report, old_status=None, new_status=Status.SUBMITTED,
                                    note="Report submitted.", changed_by=resident, changed_at=now))
        if before_commit is not None:
            before_commit(report)
        db.session.commit()
    except IntegrityError:
        # Two identical submissions arrived at the same moment; keep the first one.
        db.session.rollback()
        return find_by_token(resident, token), False
    return report, True


def reports_for_resident(resident: User):
    """Reports the resident submitted or joined (REQ-21), newest first."""
    joined = db.session.query(ReportFollower.report_id).filter(ReportFollower.resident_id == resident.id)
    return (Report.query.filter(or_(Report.resident_id == resident.id, Report.id.in_(joined)))
            .order_by(Report.created_at.desc()))


def resident_can_view(resident: User, report: Report) -> bool:
    return report.resident_id == resident.id or any(f.resident_id == resident.id for f in report.followers)
