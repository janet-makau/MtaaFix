"""AI triage module (SDS 4.6; REQ-9 to REQ-14; NFR-15). The OpenAI API is always faked."""
import json
import urllib.error

from mtaafix.extensions import db
from mtaafix.models import AISuggestion, Category, Status
from mtaafix.services import ai


# ---------------------------------------------------------------- privacy and distance (no AI)
def test_mask_removes_emails_and_kenyan_phone_numbers():
    text = "Call me on 0712 345 678 or +254 712 345678, or email wanjiru.k@gmail.com. Pothole is 2m wide."
    masked = ai.mask_personal_data(text)
    assert "0712" not in masked and "254" not in masked and "gmail" not in masked
    assert masked.count(ai.MASK) == 3
    assert "Pothole is 2m wide." in masked


def test_distance_is_about_right():
    # 0.001 degrees of latitude is about 111 metres
    assert 105 < ai.distance_m(-1.3000, 36.8000, -1.3010, 36.8000) < 117


def test_nearby_reports_same_category_open_within_200m(make_report, ctx):
    near = make_report(latitude=-1.30000, longitude=36.80000)
    make_report(latitude=-1.30500, longitude=36.80000)                       # about 550 m away
    drainage = Category.query.filter_by(name="Drainage").one()
    make_report(cat=drainage, latitude=-1.30001, longitude=36.80001)       # other category
    closed = make_report(latitude=-1.30002, longitude=36.80000)
    closed.status = Status.CLOSED
    db.session.commit()

    found = ai.nearby_open_reports(near.category_id, -1.30010, 36.80010)
    assert [r.id for r in found] == [near.id]


def test_nearby_reports_limited_to_5_nearest_first(make_report, ctx):
    reports = [make_report(latitude=-1.30000 - i * 0.0001, longitude=36.80000) for i in range(7)]
    found = ai.nearby_open_reports(reports[0].category_id, -1.30000, 36.80000)
    assert [r.id for r in found] == [r.id for r in reports[:5]]


# ---------------------------------------------------------------- suggestions
def test_suggest_category_returns_matching_active_category(app, fake_ai):
    categories = Category.query.order_by(Category.name).all()
    fake_ai.reply = {"category": "drainage"}
    assert ai.suggest_category("Water floods the road when it rains.", categories).name == "Drainage"


def test_suggest_category_ignores_names_not_in_the_list(app, fake_ai):
    fake_ai.reply = {"category": "Electricity"}
    assert ai.suggest_category("Power line down.", Category.query.all()) is None


def test_check_duplicate_maps_number_back_to_report(make_report, fake_ai, ctx):
    a, b = make_report(), make_report(description="Pothole near the matatu stage, very deep.")
    fake_ai.reply = {"duplicate_of": 2}
    assert ai.check_duplicate("Deep pothole at the stage", [a, b]) is b
    sent = json.loads(fake_ai.requests[-1]["messages"][1]["content"])
    assert [c["number"] for c in sent["existing_reports"]] == [1, 2]
    assert "MF-" not in fake_ai.requests[-1]["messages"][1]["content"]     # no references sent


def test_check_duplicate_rejects_out_of_range_or_null(make_report, fake_ai, ctx):
    a = make_report()
    for reply in ({"duplicate_of": None}, {"duplicate_of": 5}, {"duplicate_of": True}, {}):
        fake_ai.reply = reply
        assert ai.check_duplicate("Pothole", [a]) is None


def test_suggest_priority_validates_value_and_trims_reasons(app, fake_ai):
    fake_ai.reply = {"priority": "High", "reasons": ["near a school", "flooding risk", "x" * 200, "extra"]}
    result = ai.suggest_priority("Blocked drain by the school gate floods the road.", "Drainage")
    assert result.priority == "high"
    assert len(result.reasons) == 3 and len(result.reasons[2]) == ai.MAX_REASON_CHARS

    fake_ai.reply = {"priority": "Urgent!!"}
    assert ai.suggest_priority("Something", "Roads") is None


def test_request_masks_data_uses_temperature_0_and_never_sends_coordinates(make_report, fake_ai, ctx):
    fake_ai.reply = {"priority": "Low", "reasons": []}
    ai.suggest_priority("Bin full. Call 0712345678.", "Waste Collection")
    body = fake_ai.requests[-1]
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    content = body["messages"][1]["content"]
    assert "0712345678" not in content and ai.MASK in content
    assert "-1.3" not in content and "36.7" not in content


# ---------------------------------------------------------------- fallback (REQ-13)
def test_switched_off_means_no_call_and_no_suggestion(app, fake_ai):
    app.config["AI_ENABLED"] = False
    assert ai.suggest_priority("Pothole", "Roads") is None
    assert fake_ai.requests == []


def test_timeouts_errors_and_bad_replies_return_none(app, fake_ai):
    for error in (TimeoutError(), urllib.error.URLError("offline"), OSError("reset")):
        fake_ai.error = error
        assert ai.suggest_category("Pothole", Category.query.all()) is None
    fake_ai.error = None
    fake_ai.reply = ["not", "an", "object"]
    assert ai.suggest_category("Pothole", Category.query.all()) is None


# ---------------------------------------------------------------- record keeping (REQ-14)
def test_record_suggestion_saved_with_report_in_same_transaction(make_report, ctx):
    report = make_report()
    ai.record_suggestion(ai.KIND_CATEGORY, "Roads", "Roads", report=report)
    ai.record_suggestion(ai.KIND_PRIORITY, "high", "medium", report=report, reasons=["busy road"])
    ai.record_suggestion(ai.KIND_DUPLICATE, None, None, report=report)
    db.session.commit()

    rows = {r.kind: r for r in AISuggestion.query.filter_by(report_id=report.id)}
    assert rows["category"].accepted is True
    assert rows["priority"].accepted is False and rows["priority"].reasons == "busy road"
    assert rows["duplicate"].accepted is None
