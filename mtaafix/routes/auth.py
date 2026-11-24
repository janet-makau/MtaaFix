"""Registration, login and logout (REQ-1, REQ-2, REQ-4)."""
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..audit import log_event
from ..extensions import db
from ..forms import LoginForm, RegistrationForm
from ..models import Role, User
from ..security import dashboard_url_for

bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(full_name=form.full_name.data, email=form.email.data, role=Role.RESIDENT)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Welcome to MtaaFix. Your account has been created.", "success")
        return redirect(dashboard_url_for(user))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user is None or not user.check_password(form.password.data):
            # One general message, so the page never reveals which part was wrong (REQ-2).
            flash("Incorrect email or password.", "danger")
            log_event("login_failed")
        elif not user.active:
            flash("This account is deactivated. Please contact the authority office.", "danger")
            log_event("login_refused_deactivated", user)
        else:
            login_user(user, remember=form.remember.data)
            log_event("login", user)
            return redirect(_safe_next() or dashboard_url_for(user))
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_event("logout", current_user)
    logout_user()
    flash("You have logged out.", "info")
    return redirect(url_for("main.home"))


def _safe_next() -> str | None:
    """Only follow ?next= links that stay on this site."""
    target = request.args.get("next")
    if target and not urlsplit(target).netloc and target.startswith("/"):
        return target
    return None
