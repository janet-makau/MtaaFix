"""Application settings.

Every setting is read from an environment variable (or the .env file), so the same code
runs on a laptop and on PythonAnywhere. Secrets are never written in the code (NFR-14).
"""
import os
from datetime import timedelta


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-secret")

    # Database (SRS 6.1): SQLite by default; SQLAlchemy lets us switch to MySQL by URL only.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")  # filled in by create_app if empty
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessions and cookies (NFR-11, SDS 7.1)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_IDLE_MINUTES", "30")))
    SESSION_REFRESH_EACH_REQUEST = True          # the 30 minutes restart on every request (inactivity timeout)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=int(os.environ.get("REMEMBER_DAYS", "14")))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Photos (REQ-7): JPEG or PNG up to 5 MB. Requests above 6 MB are refused by Flask itself.
    PHOTO_MAX_BYTES = 5 * 1024 * 1024
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")

    # Email (REQ-22): Gmail SMTP. If MAIL_USERNAME is empty, emails are written to the log instead.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", True)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER") or os.environ.get("MAIL_USERNAME", "")

    # Map (Leaflet + OpenStreetMap)
    MAP_CENTER_LAT = float(os.environ.get("MAP_CENTER_LAT", "-1.286389"))
    MAP_CENTER_LNG = float(os.environ.get("MAP_CENTER_LNG", "36.817223"))
    MAP_ZOOM = int(os.environ.get("MAP_ZOOM", "13"))

    # AI triage (SDS 4.6). Off unless both AI_ENABLED and OPENAI_API_KEY are set (REQ-13).
    AI_ENABLED = _bool("AI_ENABLED", False)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
    AI_API_URL = os.environ.get("AI_API_URL", "https://api.openai.com/v1/chat/completions")
    AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "10"))
    DUPLICATE_RADIUS_M = 200       # look for duplicates within 200 metres...
    DUPLICATE_MAX_CANDIDATES = 5   # ...and send at most 5 nearby reports to the AI service

    # Workflow
    STALE_AFTER_DAYS = 14          # REQ-30: highlight open reports with no status change for 14 days
    REPORTS_PER_PAGE = 20


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True   # cookies only over HTTPS
    REMEMBER_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"   # in-memory database
    MAIL_USERNAME = ""                       # never send real email in tests
    CLOUDINARY_URL = ""
    AI_ENABLED = False                       # tests switch AI on explicitly and fake the API
    OPENAI_API_KEY = ""


CONFIGS = {"development": DevelopmentConfig, "production": ProductionConfig, "testing": TestingConfig}
