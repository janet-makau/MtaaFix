"""Public pages, and photos stored locally during development."""
import os

from flask import Blueprint, current_app, redirect, render_template, send_from_directory, url_for
from flask_login import current_user, login_required

from ..security import dashboard_url_for

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    return render_template("home.html")


@bp.route("/dashboard")
def dashboard():
    """Send a logged-in user to the dashboard for their role."""
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))
    return redirect(url_for("main.home"))


@bp.route("/uploads/<path:filename>")
@login_required
def uploaded_photo(filename: str):
    """Serve photos saved in instance/uploads (only used when Cloudinary is not configured)."""
    return send_from_directory(os.path.join(current_app.instance_path, "uploads"), filename)
