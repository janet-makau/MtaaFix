"""Field Officer pages: my tasks and progress updates (REQ-18, REQ-19)."""
from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user

from ..audit import log_event
from ..extensions import db
from ..forms import StatusForm
from ..models import Report, Role, Status
from ..security import role_required
from ..services import workflow
from .helpers import run_action

bp = Blueprint("officer", __name__)


@bp.before_request
@role_required(Role.FIELD_OFFICER)
def _only_field_officers():
    """Every page in this blueprint is for Field Officers only."""


@bp.route("/tasks")
def my_tasks():
    """REQ-18: only the open reports assigned to this officer."""
    return render_template("officer/my_tasks.html", reports=workflow.open_reports_for_officer(current_user))


@bp.route("/tasks/<int:report_id>")
def task_detail(report_id: int):
    report = _get_own_report(report_id)
    log_event("report_viewed", current_user, report=report.reference)
    form = StatusForm()
    form.status.choices = [(s, Status.LABELS[s]) for s in workflow.allowed_next_statuses(report, current_user)]
    return render_template("officer/task_detail.html", report=report, form=form)


@bp.route("/tasks/<int:report_id>/status", methods=["POST"])
def update_status(report_id: int):
    report = _get_own_report(report_id)
    form = StatusForm()
    form.status.choices = [(s, Status.LABELS[s]) for s in Status.LABELS]
    if form.validate_on_submit():
        run_action(lambda: workflow.update_status(report, form.status.data, current_user, form.note.data),
                   f"Status updated to {Status.LABELS[form.status.data]}.")
    if report.status == Status.RESOLVED:
        return redirect(url_for(".my_tasks"))
    return redirect(url_for(".task_detail", report_id=report.id))


def _get_own_report(report_id: int) -> Report:
    """A Field Officer only sees reports currently assigned to them (REQ-19, REQ-25)."""
    report = db.session.get(Report, report_id)
    if report is None or report.assigned_officer_id != current_user.id:
        abort(404)
    return report
