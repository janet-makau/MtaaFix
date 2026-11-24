"""JSON endpoints used by the report form while the resident is typing (SDS 4.3 process 2.0).

    POST /api/reports/suggest-category   -> {"category_id": 3, "name": "Drainage"} or {"category_id": null}
    POST /api/reports/check-duplicate    -> {"duplicate": {...}} or {"duplicate": null}
    POST /api/reports/<id>/join          -> {"redirect": "/resident/reports/<id>"}

Residents only. Requests are JSON and carry the CSRF token in the X-CSRFToken header, which
CSRFProtect checks like any form (NFR-13). If the AI is off or fails, the endpoints still answer
with null, so the form keeps working (REQ-13).
"""
from flask import Blueprint, abort, jsonify, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Category, Role, Status
from ..services import ai, triage

bp = Blueprint("api", __name__)

MIN_DESCRIPTION = 10


@bp.before_request
def _residents_only():
    # JSON callers get a status code, not a redirect to an HTML page.
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role != Role.RESIDENT:
        abort(403)


@bp.errorhandler(400)
@bp.errorhandler(401)
@bp.errorhandler(403)
@bp.errorhandler(404)
def _json_error(error):
    return jsonify(error=error.name), error.code


@bp.post("/reports/suggest-category")
def suggest_category():
    data = _json()
    description, token = _text(data, "description"), _text(data, "token")
    if len(description) < MIN_DESCRIPTION or not token:
        return jsonify(category_id=None)
    category = triage.suggest_category(token, description)
    return jsonify(category_id=category.id if category else None, name=category.name if category else None)


@bp.post("/reports/check-duplicate")
def check_duplicate():
    data = _json()
    description, token = _text(data, "description"), _text(data, "token")
    try:
        category_id = int(data.get("category_id"))
        latitude, longitude = float(data.get("latitude")), float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify(duplicate=None)       # incomplete form: the server-side validation will explain
    if len(description) < MIN_DESCRIPTION or not token or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return jsonify(duplicate=None)
    if db.session.get(Category, category_id) is None:
        return jsonify(duplicate=None)

    match = triage.check_duplicate(token, description, category_id, latitude, longitude)
    if match is None:
        return jsonify(duplicate=None)
    # Only what the resident needs to recognise the problem: no names, emails or staff details (NFR-16).
    return jsonify(duplicate={
        "id": match.id,
        "reference": match.reference,
        "category": match.category.name,
        "status": Status.LABELS[match.status],
        "description": match.description[:300],
        "distance_m": round(ai.distance_m(latitude, longitude, match.latitude, match.longitude)),
    })


@bp.post("/reports/<int:report_id>/join")
def join(report_id: int):
    token = _text(_json(), "token")
    report = triage.shown_duplicate(token)
    # A resident may only join the report that was actually shown to them for this form.
    if report is None or report.id != report_id or not report.is_open:
        abort(404)
    triage.join_report(current_user, report, token)
    return jsonify(redirect=url_for("resident.report_detail", report_id=report.id))


def _json() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""
