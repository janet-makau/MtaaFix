"""MtaaFix application factory.

create_app() builds the Flask app: it loads the settings, connects the database and other
extensions, and registers the pages (blueprints) for each role.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session

from .config import CONFIGS
from .extensions import csrf, db, login_manager, mail

LOCAL_TZ = ZoneInfo("Africa/Nairobi")


def create_app(env: str | None = None, overrides: dict | None = None) -> Flask:
    load_dotenv()
    env = env or os.environ.get("MTAAFIX_ENV", "development")

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIGS[env])
    app.config["MTAAFIX_ENV"] = env
    if overrides:
        app.config.update(overrides)
    os.makedirs(app.instance_path, exist_ok=True)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, "mtaafix.db")
    if env == "production" and app.config["SECRET_KEY"] == "dev-only-not-secret":
        raise RuntimeError("Set SECRET_KEY in the environment before running in production.")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)

    _configure_logging(app)
    _configure_sessions(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_template_helpers(app)

    from .cli import register_cli
    register_cli(app)
    return app


@login_manager.user_loader
def load_user(user_id: str):
    """Reload the account on every request (SDS 7.1).

    Returning None for a deactivated account logs the user out at their next click,
    even if they ticked "Keep me logged in".
    """
    from .models import User
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    return user if user is not None and user.active else None


def _configure_sessions(app: Flask) -> None:
    @app.before_request
    def _apply_idle_timeout():
        # A permanent session expires after PERMANENT_SESSION_LIFETIME without a request (30 minutes).
        session.permanent = True


def _register_blueprints(app: Flask) -> None:
    from .routes.api import bp as api_bp
    from .routes.auth import bp as auth_bp
    from .routes.authority import bp as authority_bp
    from .routes.main import bp as main_bp
    from .routes.officer import bp as officer_bp
    from .routes.resident import bp as resident_bp
    from .routes.superadmin import bp as superadmin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(resident_bp, url_prefix="/resident")
    app.register_blueprint(authority_bp, url_prefix="/authority")
    app.register_blueprint(officer_bp, url_prefix="/officer")
    app.register_blueprint(superadmin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/error.html", code=404, message="That page could not be found."), 404

    @app.errorhandler(413)
    def too_large(_):
        # The upload is bigger than MAX_CONTENT_LENGTH, so Flask refuses it before our form sees it.
        flash("That photo is too large. Please choose a JPEG or PNG under 5 MB.", "danger")
        return redirect(request.url)

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        app.logger.exception("Unhandled error: %s", error)
        return render_template("errors/error.html", code=500,
                               message="Something went wrong. Nothing was saved. Please try again."), 500


def _register_template_helpers(app: Flask) -> None:
    from .models import Priority, Role, Status

    @app.template_filter("localtime")
    def localtime(value, fmt="%d/%m/%Y %H:%M"):
        """Show stored UTC times in Nairobi time."""
        if value is None:
            return ""
        from datetime import timezone
        return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).strftime(fmt)

    @app.context_processor
    def inject_constants():
        cfg = app.config
        return {"Role": Role, "Status": Status, "Priority": Priority,
                "map_cfg": {"lat": cfg["MAP_CENTER_LAT"], "lng": cfg["MAP_CENTER_LNG"], "zoom": cfg["MAP_ZOOM"]}}


def _configure_logging(app: Flask) -> None:
    """Write warnings and errors (such as failed emails, REQ-22) to instance/logs/mtaafix.log."""
    if app.testing:
        return
    log_dir = os.path.join(app.instance_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(log_dir, "mtaafix.log"), maxBytes=1_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
