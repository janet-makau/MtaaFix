"""Small helpers shared by the staff pages."""
from flask import flash

from ..extensions import db
from ..services.workflow import Outcome, WorkflowError


def run_action(action, success_message: str) -> bool:
    """Run a workflow action, show the right message, and undo partial changes if a rule is broken.

    Returns True if the action succeeded.
    """
    try:
        outcome: Outcome = action()
    except WorkflowError as error:
        db.session.rollback()
        flash(str(error), "danger")
        return False
    flash(success_message, "success")
    if not outcome.email_sent:
        # REQ-22 (SRS 3.1.7 E1): the change is saved, but tell staff the email failed.
        flash("The change was saved, but the email to the resident could not be sent.", "warning")
    return True
