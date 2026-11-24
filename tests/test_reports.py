"""Report submission and access (REQ-5 to REQ-8, REQ-21, REQ-29; NFR-12)."""
import re

import pytest
from werkzeug.datastructures import FileStorage

from mtaafix.models import Report
from mtaafix.services.reports import create_report, reports_for_resident, resident_can_view
from mtaafix.services.storage import PhotoError, validate_photo

from conftest import image_bytes, login


def test_reference_number_format(make_report, ctx):
    report = make_report()
    assert re.fullmatch(r"MF-\d{6}", report.reference)
    assert report.reference == f"MF-{report.id:06d}"


def test_same_form_token_never_saves_twice(users, category, ctx):
    kwargs = dict(description="Streetlight off for two weeks near the bus stop.", category=category,
                  latitude=-1.30, longitude=36.78, photo_url=None, token="same-token")
    first, created_first = create_report(users["resident"], **kwargs)
    second, created_second = create_report(users["resident"], **kwargs)
    assert created_first and not created_second
    assert first.id == second.id
    assert Report.query.count() == 1


def test_resident_sees_only_own_and_joined_reports(make_report, users, ctx):
    from mtaafix.extensions import db
    from mtaafix.models import ReportFollower

    own = make_report()
    other = make_report(resident=users["resident2"])
    joined = make_report(resident=users["resident2"], description="Blocked drain by the school gate.")
    db.session.add(ReportFollower(report=joined, resident_id=users["resident"].id))
    db.session.commit()

    visible = {r.id for r in reports_for_resident(users["resident"])}
    assert visible == {own.id, joined.id}
    assert resident_can_view(users["resident"], joined)
    assert not resident_can_view(users["resident"], other)


def test_other_residents_report_returns_not_found(client, make_report, users, ctx):
    other = make_report(resident=users["resident2"])
    login(client, "resident@test.ke")
    assert client.get(f"/resident/reports/{other.id}").status_code == 404


def test_submit_report_through_the_form(client, users, category):
    login(client, "resident@test.ke")
    response = client.post("/resident/reports/new", data={
        "description": "Large pothole outside Kenyatta Market.", "category": category.id,
        "latitude": "-1.308200", "longitude": "36.798900", "submission_token": "form-1",
        "photo": (image_bytes("JPEG"), "pothole.jpg")}, content_type="multipart/form-data")
    assert response.status_code == 302
    report = Report.query.one()
    assert report.resident_id == users["resident"].id
    assert report.photo_url                       # stored locally in tests (no Cloudinary URL)


def test_form_rejects_short_description_and_missing_location(client, users, category):
    login(client, "resident@test.ke")
    response = client.post("/resident/reports/new", data={
        "description": "Hole", "category": category.id, "latitude": "", "longitude": "",
        "submission_token": "form-2"})
    assert response.status_code == 200
    assert Report.query.count() == 0


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_jpeg_and_png_photos_are_accepted(app, fmt, ctx):
    file = FileStorage(stream=image_bytes(fmt), filename=f"photo.{fmt.lower()}")
    assert validate_photo(file) in {"jpg", "png"}


def test_photo_type_is_checked_from_content_not_name(app, ctx):
    gif = FileStorage(stream=image_bytes("GIF"), filename="looks-like.jpg")
    with pytest.raises(PhotoError, match="JPEG or PNG"):
        validate_photo(gif)


def test_photo_over_5_mb_is_rejected(app, ctx):
    import io
    big = FileStorage(stream=io.BytesIO(b"\xff" * (5 * 1024 * 1024 + 1)), filename="big.jpg")
    with pytest.raises(PhotoError, match="under 5 MB"):
        validate_photo(big)
