"""Authority Administrator pages: dashboard, review, priority, assignment and closure.

Requirements: REQ-15 to REQ-17, REQ-19, REQ-25, REQ-27, REQ-28, REQ-30.
"""
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..audit import log_event
from ..extensions import db
from ..forms import AssignForm, CloseForm, PriorityForm, ReopenForm, StatusForm
from ..models import Category, Priority, Report, Role, Status, User
from ..security import role_required
from ..services import triage, workflow
from .helpers import run_action

bp = Blueprint("authority", __name__)


@bp.before_request
@role_required(Role.AUTHORITY_ADMIN)
def _only_authority_admin():
    """Every page in this blueprint is for the Authority Administrator only."""


@bp.route("/")
def dashboard():
    """REQ-15: all reports, filterable by status, category and priority, newest first."""
    status = request.args.get("status", "")
    category_id = request.args.get("category", type=int)
    priority = request.args.get("priority", "")

    query = Report.query
    if status in Status.LABELS:
        query = query.filter(Report.status == status)
    elif status == "open":
        query = query.filter(Report.status.in_(Status.OPEN))
    if category_id:
        query = query.filter(Report.category_id == category_id)
    if priority in Priority.LABELS:
        query = query.filter(Report.priority == priority)
    elif priority == "none":
        query = query.filter(Report.priority.is_(None))

    page = query.order_by(Report.created_at.desc()).paginate(
        per_page=current_app.config["REPORTS_PER_PAGE"], error_out=False)
    days = current_app.config["STALE_AFTER_DAYS"]
    stale_ids = {r.id for r in page.items if workflow.is_stale(r, days)}
    counts = dict(db.session.query(Report.status, db.func.count()).group_by(Report.status).all())
    return render_template("authority/dashboard.html", page=page, stale_ids=stale_ids, stale_days=days,
                           counts=counts, categories=Category.query.order_by(Category.name).all(),
                           filters={"status": status, "category": category_id, "priority": priority})


@bp.route("/reports/<int:report_id>")
def review(report_id: int):
    report = _get_report(report_id)
    log_event("report_viewed", current_user, report=report.reference)
    officers = workflow.active_officers()
    officer_choices = [(o.id, o.full_name) for o in officers if o.id != report.assigned_officer_id]

    # Each form has a prefix so its field names and ids stay unique on the shared page.
    priority_form = PriorityForm(prefix="priority", priority=report.priority or Priority.MEDIUM)
    assign_form = AssignForm(prefix="assign")
    assign_form.officer.choices = officer_choices
    status_form = StatusForm(prefix="status")
    status_form.status.choices = [(s, Status.LABELS[s]) for s in workflow.allowed_next_statuses(report, current_user)]
    suggestion = triage.priority_suggestion(report)
    if suggestion is not None and not report.priority:
        priority_form.priority.data = suggestion.suggested_value      # start from the AI suggestion
    days = current_app.config["STALE_AFTER_DAYS"]
    return render_template("authority/review.html", report=report, priority_form=priority_form,
                           priority_suggestion=suggestion,
                           assign_form=assign_form, status_form=status_form, close_form=CloseForm(prefix="close"),
                           reopen_form=ReopenForm(prefix="reopen"), is_stale=workflow.is_stale(report, days), stale_days=days)


@bp.route("/reports/<int:report_id>/priority", methods=["POST"])
def set_priority(report_id: int):
    """REQ-16: the administrator sets (or later, accepts or changes the suggested) priority."""
    report = _get_report(report_id)
    form = PriorityForm(prefix="priority")
    if form.validate_on_submit():
        report.priority = form.priority.data
        triage.confirm_priority(report, report.priority)   # REQ-14: kept or changed the suggestion
        db.session.commit()
        flash(f"Priority set to {report.priority_label}.", "success")
    return _back(report)


@bp.route("/reports/<int:report_id>/assign", methods=["POST"])
def assign(report_id: int):
    report = _get_report(report_id)
    form = AssignForm(prefix="assign")
    form.officer.choices = [(o.id, o.full_name) for o in workflow.active_officers()]
    if form.validate_on_submit():
        officer = db.session.get(User, form.officer.data)
        if report.status == Status.SUBMITTED:
            run_action(lambda: workflow.assign(report, officer, current_user, form.note.data),
                       f"{report.reference} assigned to {officer.full_name}.")
        else:
            run_action(lambda: workflow.reassign(report, officer, current_user, form.note.data),
                       f"{report.reference} reassigned to {officer.full_name}.")
    else:
        flash("Choose an active Field Officer.", "danger")
    return _back(report)


@bp.route("/reports/<int:report_id>/status", methods=["POST"])
def update_status(report_id: int):
    report = _get_report(report_id)
    form = StatusForm(prefix="status")
    form.status.choices = [(s, Status.LABELS[s]) for s in Status.LABELS]
    if form.validate_on_submit():
        run_action(lambda: workflow.update_status(report, form.status.data, current_user, form.note.data),
                   f"Status updated to {Status.LABELS[form.status.data]}.")
    return _back(report)


@bp.route("/reports/<int:report_id>/close", methods=["POST"])
def close(report_id: int):
    report = _get_report(report_id)
    form = CloseForm(prefix="close")
    if form.validate_on_submit():
        run_action(lambda: workflow.close(report, form.reason.data, current_user, form.note.data,
                                          form.original_reference.data),
                   f"{report.reference} closed.")
    return _back(report)


@bp.route("/reports/<int:report_id>/reopen", methods=["POST"])
def reopen(report_id: int):
    report = _get_report(report_id)
    form = ReopenForm(prefix="reopen")
    if form.validate_on_submit():
        run_action(lambda: workflow.reopen(report, current_user, form.note.data),
                   f"{report.reference} reopened.")
    else:
        flash("Give a reason for reopening the report.", "danger")
    return _back(report)


def _get_report(report_id: int) -> Report:
    return db.session.get(Report, report_id) or abort(404)


def _back(report: Report):
    return redirect(url_for(".review", report_id=report.id))
