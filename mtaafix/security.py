"""Role checks (REQ-3, NFR-12).

The check runs on the server for every request, so hiding a link is never the only protection.
"""
from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user

from .models import Role

# Where each role lands after logging in.
DASHBOARDS = {
    Role.RESIDENT: "resident.my_reports",
    Role.FIELD_OFFICER: "officer.my_tasks",
    Role.AUTHORITY_ADMIN: "authority.dashboard",
    Role.SUPER_ADMIN: "superadmin.users",
}


def dashboard_url_for(user) -> str:
    return url_for(DASHBOARDS.get(user.role, "main.home"))


def role_required(*roles: str):
    """Allow the view only for users with one of the given roles.

    A logged-out user is sent to the login page; a user with another role is sent back
    to their own dashboard with a message.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                from .extensions import login_manager
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("You do not have access to that page.", "danger")
                return redirect(dashboard_url_for(current_user))
            return view(*args, **kwargs)
        return wrapped
    return decorator
