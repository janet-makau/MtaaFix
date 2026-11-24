"""Report lifecycle rules (REQ-17, REQ-19, REQ-20, REQ-25 to REQ-28; SDS 4.5)."""
import pytest

from mtaafix.extensions import db
from mtaafix.models import Assignment, CloseReason, ReportFollower, Status, StatusChange
from mtaafix.services import workflow
from mtaafix.services.workflow import WorkflowError


@pytest.fixture
def report(make_report, ctx):
    return make_report()


def test_new_report_starts_submitted_with_one_history_entry(report):
    assert report.status == Status.SUBMITTED
    assert [h.new_status for h in report.history] == [Status.SUBMITTED]


def test_admin_assigns_submitted_report(report, users):
    outcome = workflow.assign(report, users["officer"], users["admin"])
    assert report.status == Status.ASSIGNED
    assert report.assigned_officer_id == users["officer"].id
    assert Assignment.query.filter_by(report_id=report.id).count() == 1
    assert outcome.change.changed_by_id == users["admin"].id
    assert outcome.email_sent   # email is logged, not sent, in tests


def test_only_admin_can_assign(report, users):
    with pytest.raises(WorkflowError):
        workflow.assign(report, users["officer"], users["officer"])


def test_cannot_assign_to_inactive_or_non_officer(report, users):
    users["officer"].active = False
    db.session.commit()
    for target in (users["officer"], users["resident"]):
        with pytest.raises(WorkflowError, match="active Field Officer"):
            workflow.assign(report, target, users["admin"])


def test_officer_moves_own_report_forward_one_step_at_a_time(report, users):
    workflow.assign(report, users["officer"], users["admin"])
    with pytest.raises(WorkflowError, match="In Progress"):
        workflow.update_status(report, Status.RESOLVED, users["officer"])      # cannot skip a step
    workflow.update_status(report, Status.IN_PROGRESS, users["officer"], "Crew on site.")
    workflow.update_status(report, Status.RESOLVED, users["officer"], "Pothole patched.")
    assert report.status == Status.RESOLVED
    assert [h.new_status for h in report.history] == [
        Status.SUBMITTED, Status.ASSIGNED, Status.IN_PROGRESS, Status.RESOLVED]


def test_officer_cannot_update_another_officers_report(report, users):
    workflow.assign(report, users["officer"], users["admin"])
    with pytest.raises(WorkflowError, match="assigned to you"):
        workflow.update_status(report, Status.IN_PROGRESS, users["officer2"])


def test_reassign_needs_a_reason_and_keeps_history(report, users):
    workflow.assign(report, users["officer"], users["admin"])
    with pytest.raises(WorkflowError, match="reason"):
        workflow.reassign(report, users["officer2"], users["admin"], None)
    workflow.reassign(report, users["officer2"], users["admin"], "Peter is on leave.")
    assert report.assigned_officer_id == users["officer2"].id
    assert [a.officer_id for a in report.assignments] == [users["officer"].id, users["officer2"].id]


def test_close_as_duplicate_sets_original_and_moves_residents(make_report, users, ctx):
    original = make_report()
    duplicate = make_report(resident=users["resident2"])
    workflow.close(duplicate, CloseReason.DUPLICATE, users["admin"], original_reference=original.reference)

    assert duplicate.status == Status.CLOSED
    assert duplicate.duplicate_of_id == original.id
    followers = {f.resident_id for f in ReportFollower.query.filter_by(report_id=original.id)}
    assert followers == {users["resident2"].id}


def test_close_as_duplicate_needs_a_valid_other_reference(report, users):
    for reference in ("MF-999999", report.reference, ""):
        with pytest.raises(WorkflowError, match="valid reference"):
            workflow.close(report, CloseReason.DUPLICATE, users["admin"], original_reference=reference)
    assert report.status == Status.SUBMITTED


def test_close_needs_a_known_reason(report, users):
    with pytest.raises(WorkflowError):
        workflow.close(report, "because", users["admin"])


def test_reopen_clears_officer_but_keeps_assignment_history(report, users):
    workflow.assign(report, users["officer"], users["admin"])
    workflow.update_status(report, Status.IN_PROGRESS, users["officer"])
    workflow.update_status(report, Status.RESOLVED, users["officer"])

    with pytest.raises(WorkflowError, match="reason"):
        workflow.reopen(report, users["admin"], "")
    workflow.reopen(report, users["admin"], "Pothole has opened up again.")

    assert report.status == Status.SUBMITTED
    assert report.assigned_officer_id is None
    assert Assignment.query.filter_by(report_id=report.id).count() == 1     # history kept

    workflow.assign(report, users["officer2"], users["admin"])                # new row added
    assert Assignment.query.filter_by(report_id=report.id).count() == 2


def test_only_resolved_or_closed_reports_can_be_reopened(report, users):
    with pytest.raises(WorkflowError):
        workflow.reopen(report, users["admin"], "No reason to reopen.")


def test_every_change_records_who_when_and_note(report, users):
    workflow.assign(report, users["officer"], users["admin"], "Please inspect this week.")
    change = StatusChange.query.filter_by(report_id=report.id, new_status=Status.ASSIGNED).one()
    assert change.changed_by_id == users["admin"].id
    assert change.changed_at is not None
    assert change.note == "Please inspect this week."


def test_stale_reports_are_flagged_after_14_days(report):
    from datetime import timedelta
    from mtaafix.models import utcnow
    assert not workflow.is_stale(report, 14)
    report.status_changed_at = utcnow() - timedelta(days=15)
    assert workflow.is_stale(report, 14)
