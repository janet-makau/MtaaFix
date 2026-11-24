"""AI connected to the pages (REQ-9 to REQ-14): JSON endpoints, report submission and admin review.

The OpenAI API is always faked (see the fake_ai fixture in conftest.py).
"""
import pytest

from mtaafix.extensions import db
from mtaafix.models import AISuggestion, Category, Report, ReportFollower

from conftest import login

CATEGORY_URL = "/api/reports/suggest-category"
DUPLICATE_URL = "/api/reports/check-duplicate"
DESCRIPTION = "Deep pothole outside Kenyatta Market near the matatu stage."


@pytest.fixture
def resident_client(client, users):
    login(client, "resident@test.ke")
    return client


def _duplicate_request(category, token="form-1", lat=-1.30821, lng=36.79891):
    return {"description": DESCRIPTION, "category_id": category.id, "latitude": lat,
            "longitude": lng, "token": token}


def _submit(client, category, token="form-1"):
    return client.post("/resident/reports/new", data={
        "description": DESCRIPTION, "category": category.id, "latitude": "-1.308210",
        "longitude": "36.798910", "submission_token": token})


# ---------------------------------------------------------------- access
def test_api_is_for_logged_in_residents_only(client, users, category):
    assert client.post(CATEGORY_URL, json={}).status_code == 401
    login(client, "admin@test.ke")
    response = client.post(CATEGORY_URL, json={"description": DESCRIPTION, "token": "t"})
    assert response.status_code == 403 and response.is_json


def test_non_json_body_is_rejected(resident_client):
    assert resident_client.post(CATEGORY_URL, data="not json").status_code == 400


# ---------------------------------------------------------------- category
def test_category_suggestion_returned(resident_client, fake_ai):
    fake_ai.reply = {"category": "Roads"}
    data = resident_client.post(CATEGORY_URL, json={"description": DESCRIPTION, "token": "form-1"}).get_json()
    assert data["name"] == "Roads"
    assert data["category_id"] == Category.query.filter_by(name="Roads").one().id


def test_no_suggestion_when_ai_off_or_text_too_short(resident_client, app):
    assert resident_client.post(CATEGORY_URL, json={"description": DESCRIPTION, "token": "t"}).get_json() \
        == {"category_id": None, "name": None}
    assert resident_client.post(CATEGORY_URL, json={"description": "Hole", "token": "t"}).get_json() \
        == {"category_id": None}


def test_category_suggestion_saved_with_report_accepted_or_changed(resident_client, fake_ai, category):
    fake_ai.reply = {"category": "Drainage"}                 # AI suggests Drainage...
    resident_client.post(CATEGORY_URL, json={"description": DESCRIPTION, "token": "form-1"})
    fake_ai.reply = {"priority": "High", "reasons": ["busy road"]}
    _submit(resident_client, category)                        # ...resident sends it as Roads

    report = Report.query.one()
    row = AISuggestion.query.filter_by(report_id=report.id, kind="category").one()
    assert (row.suggested_value, row.final_value, row.accepted) == ("Drainage", "Roads", False)


# ---------------------------------------------------------------- duplicates
def test_duplicate_found_and_shown_without_personal_details(resident_client, make_report, users,
                                                             category, fake_ai):
    original = make_report(resident=users["resident2"])
    fake_ai.reply = {"duplicate_of": 1}
    data = resident_client.post(DUPLICATE_URL, json=_duplicate_request(category)).get_json()
    dup = data["duplicate"]
    assert dup["reference"] == original.reference and dup["distance_m"] < 5
    assert "Brian" not in str(dup) and "brian@test.ke" not in str(dup)


def test_no_duplicate_check_without_nearby_reports(resident_client, category, fake_ai):
    assert resident_client.post(DUPLICATE_URL, json=_duplicate_request(category)).get_json() == {"duplicate": None}
    assert fake_ai.requests == []          # nothing nearby, so the AI service is not called


def test_joining_shown_duplicate_follows_it_and_records_acceptance(resident_client, make_report, users,
                                                                  category, fake_ai):
    original = make_report(resident=users["resident2"])
    fake_ai.reply = {"duplicate_of": 1}
    resident_client.post(DUPLICATE_URL, json=_duplicate_request(category))

    response = resident_client.post(f"/api/reports/{original.id}/join", json={"token": "form-1"})
    assert response.get_json()["redirect"] == f"/resident/reports/{original.id}"
    assert ReportFollower.query.filter_by(report_id=original.id, resident_id=users["resident"].id).count() == 1
    row = AISuggestion.query.filter_by(report_id=original.id, kind="duplicate").one()
    assert row.accepted is True
    assert Report.query.count() == 1       # no new report, so no photo was uploaded either


def test_cannot_join_a_report_that_was_not_shown(resident_client, make_report, users):
    other = make_report(resident=users["resident2"])
    response = resident_client.post(f"/api/reports/{other.id}/join", json={"token": "form-1"})
    assert response.status_code == 404
    assert ReportFollower.query.count() == 0


def test_declined_duplicate_recorded_on_the_new_report(resident_client, make_report, users, category, fake_ai):
    original = make_report(resident=users["resident2"])
    fake_ai.reply = {"duplicate_of": 1}
    resident_client.post(DUPLICATE_URL, json=_duplicate_request(category))
    fake_ai.reply = {"priority": "Medium", "reasons": []}
    _submit(resident_client, category)

    new = Report.query.filter(Report.id != original.id).one()
    row = AISuggestion.query.filter_by(report_id=new.id, kind="duplicate").one()
    assert (row.suggested_value, row.final_value, row.accepted) == (original.reference, "new report", False)


# ---------------------------------------------------------------- priority for the administrator
def test_priority_suggested_after_submit_and_shown_to_admin(client, users, category, fake_ai):
    login(client, "resident@test.ke")
    fake_ai.reply = {"priority": "High", "reasons": ["near a school", "flooding risk"]}
    _submit(client, category)
    report = Report.query.one()
    client.post("/logout")

    login(client, "admin@test.ke")
    page = client.get(f"/authority/reports/{report.id}").get_data(as_text=True)
    assert "AI suggestion" in page and "near a school" in page and "flooding risk" in page

    client.post(f"/authority/reports/{report.id}/priority", data={"priority-priority": "medium"})
    db.session.expire_all()
    row = AISuggestion.query.filter_by(report_id=report.id, kind="priority").one()
    assert (row.suggested_value, row.final_value, row.accepted) == ("high", "medium", False)
    assert db.session.get(Report, report.id).priority == "medium"


def test_report_still_saved_when_ai_fails(client, users, category, fake_ai):
    fake_ai.error = TimeoutError()
    login(client, "resident@test.ke")
    assert _submit(client, category).status_code == 302
    report = Report.query.one()
    assert AISuggestion.query.filter_by(report_id=report.id).count() == 0
    client.post("/logout")
    login(client, "admin@test.ke")
    assert "No AI suggestion for this report" in client.get(f"/authority/reports/{report.id}").get_data(as_text=True)
