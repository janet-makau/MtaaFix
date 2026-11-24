"""Status update emails to residents (REQ-22, SRS 4.4).

Emails are plain text. If email is not configured (development), the message is written to
the log instead. If sending fails, the failure is logged and False is returned; the status
change itself has already been saved.
"""
from flask import current_app, url_for
from flask_mail import Message

from ..extensions import mail
from ..models import Report, StatusChange


def recipients_for(report: Report) -> list[str]:
    """The resident who submitted the report plus residents who joined it, active accounts only."""
    people = [report.resident] + [f.resident for f in report.followers]
    seen, emails = set(), []
    for person in people:
        if person and person.active and person.email not in seen:
            seen.add(person.email)
            emails.append(person.email)
    return emails


def build_message(report: Report, change: StatusChange) -> tuple[str, str]:
    subject = f"MtaaFix: Report {report.reference} is now {change.new_status_label}"
    lines = [
        "Hello,",
        "",
        f"The status of your MtaaFix report {report.reference} ({report.category.name}) "
        f"has changed to: {change.new_status_label}.",
    ]
    if change.note:
        lines += ["", f"Note from the authority: {change.note}"]
    lines += ["", f"View the report: {url_for('resident.report_detail', report_id=report.id, _external=True)}",
              "", "This is an automatic message from MtaaFix. Please do not reply."]
    return subject, "\n".join(lines)


def notify_status_change(report: Report, change: StatusChange) -> bool:
    emails = recipients_for(report)
    if not emails:
        return True
    subject, body = build_message(report, change)

    if not current_app.config.get("MAIL_USERNAME"):
        current_app.logger.info("Email not configured; would send to %s: %s", ", ".join(emails), subject)
        return True

    try:
        with mail.connect() as conn:
            for email in emails:   # one message per resident, so addresses are never shared (NFR-16)
                conn.send(Message(subject=subject, recipients=[email], body=body))
        return True
    except Exception:  # noqa: BLE001 - any SMTP/network error must not undo the saved change
        current_app.logger.exception("Failed to send status email for %s to %d recipient(s)",
                                     report.reference, len(emails))
        return False
