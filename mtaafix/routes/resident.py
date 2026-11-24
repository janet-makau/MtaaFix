"""Resident pages: report a problem and follow my reports (REQ-5 to REQ-8, REQ-21, REQ-29)."""
import uuid

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from ..extensions import db
from ..forms import ReportForm
from ..models import Category, Report, Role
from ..security import role_required
from ..services import triage
from ..services.reports import create_report, find_by_token, reports_for_resident, resident_can_view
from ..services.storage import PhotoError, store_photo

bp = Blueprint("resident", __name__)


@bp.before_request
@role_required(Role.RESIDENT)
def _only_residents():
    """Every page in this blueprint is for residents only."""


@bp.route("/reports")
def my_reports():
    reports = reports_for_resident(current_user).all()
    return render_template("resident/my_reports.html", reports=reports)


@bp.route("/reports/new", methods=["GET", "POST"])
def new_report():
    form = ReportForm()
    categories = Category.query.filter_by(active=True).order_by(Category.name).all()   # REQ-24
    form.category.choices = [(0, "Choose a category")] + [(c.id, c.name) for c in categories]
    if not form.submission_token.data:
        form.submission_token.data = uuid.uuid4().hex

    if form.validate_on_submit():
        # REQ-29: if this exact form was already saved, show that report instead of saving again.
        already = find_by_token(current_user, form.submission_token.data)
        if already:
            flash(f"This report was already submitted. Your reference number is {already.reference}.", "info")
            return redirect(url_for(".report_detail", report_id=already.id))

        category = db.session.get(Category, form.category.data)
        if category is None or not category.active:
            form.category.errors.append("Choose a category.")
            return render_template("resident/report_form.html", form=form)

        photo_url = None
        if form.photo.data:
            try:
                photo_url = store_photo(form.photo.data)
            except PhotoError as error:
                # REQ-7: reject the photo but keep everything else the resident typed.
                form.photo.errors.append(str(error))
                return render_template("resident/report_form.html", form=form)

        token = form.submission_token.data
        report, created = create_report(
            current_user, description=form.description.data, category=category,
            latitude=float(form.latitude.data), longitude=float(form.longitude.data),
            photo_url=photo_url, token=token,
            # REQ-14: the suggestions shown on this form are saved with the report, in one transaction.
            before_commit=lambda new_report: triage.record_form_suggestions(new_report, token))
        if created:
            triage.forget_form(token)
            triage.suggest_priority(report)          # REQ-12: ready for the administrator (None if AI is off)
            flash(f"Thank you. Your report has been submitted. Your reference number is {report.reference}.",
                  "success")
        return redirect(url_for(".report_detail", report_id=report.id))

    return render_template("resident/report_form.html", form=form)


@bp.route("/reports/<int:report_id>")
def report_detail(report_id: int):
    report = db.session.get(Report, report_id)
    if report is None or not resident_can_view(current_user, report):
        abort(404)   # do not reveal that other residents' reports exist
    return render_template("resident/report_detail.html", report=report)
