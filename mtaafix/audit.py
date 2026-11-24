"""Audit log (SDS section 7): who did what, when, and from which address.

Status changes are already stored in the status_history table. This module adds log entries
for logins and for staff viewing a report, written to instance/logs/mtaafix.log.
Email addresses and passwords are never written to the log.
"""
from flask import current_app, request


def _client() -> str:
    # PythonAnywhere passes the visitor's address in X-Real-IP; fall back to the direct address.
    return request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


def log_event(event: str, user=None, **details) -> None:
    who = f"user={user.id} role={user.role}" if user is not None else "user=anonymous"
    extra = " ".join(f"{k}={v}" for k, v in details.items())
    current_app.logger.info("AUDIT %s %s ip=%s %s", event, who, _client(), extra)
