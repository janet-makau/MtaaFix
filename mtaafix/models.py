"""Database tables (SRS section 6.1).

Each class is one table. SQLAlchemy turns these classes into SQL, so the same code works
with SQLite now and MySQL later.
"""
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> datetime:
    """Current time in UTC, stored without a time zone (SQLite has no time zone type)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fixed values used across the system
# ---------------------------------------------------------------------------
class Role:
    RESIDENT = "resident"
    FIELD_OFFICER = "field_officer"
    AUTHORITY_ADMIN = "authority_admin"
    SUPER_ADMIN = "super_admin"

    LABELS = {
        RESIDENT: "Resident",
        FIELD_OFFICER: "Field Officer",
        AUTHORITY_ADMIN: "Authority Administrator",
        SUPER_ADMIN: "Super Administrator",
    }
    STAFF = (FIELD_OFFICER, AUTHORITY_ADMIN, SUPER_ADMIN)


class Status:
    SUBMITTED = "submitted"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"

    LABELS = {
        SUBMITTED: "Submitted",
        ASSIGNED: "Assigned",
        IN_PROGRESS: "In Progress",
        RESOLVED: "Resolved",
        CLOSED: "Closed",
    }
    OPEN = (SUBMITTED, ASSIGNED, IN_PROGRESS)   # "open report" in the SRS glossary


class Priority:
    HIGH, MEDIUM, LOW = "high", "medium", "low"
    LABELS = {HIGH: "High", MEDIUM: "Medium", LOW: "Low"}


class CloseReason:
    """Reasons for closing a report that will not be fixed (REQ-27)."""
    DUPLICATE = "duplicate"
    LABELS = {
        DUPLICATE: "Duplicate",
        "not_valid": "Not a valid report",
        "not_responsibility": "Not the authority's responsibility",
        "cannot_fix_now": "Cannot be fixed at this time",
    }


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=Role.RESIDENT)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # Passwords are stored only as salted hashes (NFR-10).
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:
        """Flask-Login refuses to log in users whose is_active is False (REQ-23)."""
        return self.active

    @property
    def role_label(self) -> str:
        return Role.LABELS.get(self.role, self.role)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<Category {self.name}>"


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(20), unique=True, index=True)   # e.g. MF-000123 (REQ-8)
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    description = db.Column(db.Text, nullable=False)
    photo_url = db.Column(db.String(500))
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=Status.SUBMITTED, index=True)
    priority = db.Column(db.String(10))                                      # set by the administrator
    assigned_officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    duplicate_of_id = db.Column(db.Integer, db.ForeignKey("reports.id"))     # set when closed as Duplicate
    # One-time token from the form, so a double click or refresh cannot save twice (REQ-29).
    submission_token = db.Column(db.String(64), unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    status_changed_at = db.Column(db.DateTime, nullable=False, default=utcnow)  # used by REQ-30

    resident = db.relationship("User", foreign_keys=[resident_id])
    assigned_officer = db.relationship("User", foreign_keys=[assigned_officer_id])
    category = db.relationship("Category")
    duplicate_of = db.relationship("Report", remote_side=[id])
    history = db.relationship("StatusChange", back_populates="report",
                              order_by="StatusChange.changed_at", cascade="all, delete-orphan")
    assignments = db.relationship("Assignment", back_populates="report",
                                  order_by="Assignment.assigned_at", cascade="all, delete-orphan")
    followers = db.relationship("ReportFollower", back_populates="report", cascade="all, delete-orphan")

    @property
    def status_label(self) -> str:
        return Status.LABELS.get(self.status, self.status)

    @property
    def priority_label(self) -> str:
        return Priority.LABELS.get(self.priority, "Not set")

    @property
    def is_open(self) -> bool:
        return self.status in Status.OPEN

    def __repr__(self) -> str:
        return f"<Report {self.reference} {self.status}>"


class ReportFollower(db.Model):
    """Residents linked to a report they did not submit, so they also receive its updates."""
    __tablename__ = "report_followers"

    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    joined_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    report = db.relationship("Report", back_populates="followers")
    resident = db.relationship("User")


class Assignment(db.Model):
    """One row for each assignment or reassignment of a report (REQ-17, REQ-25)."""
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), nullable=False, index=True)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    report = db.relationship("Report", back_populates="assignments")
    officer = db.relationship("User", foreign_keys=[officer_id])
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])


class StatusChange(db.Model):
    """Every status change with its note, who made it and when (REQ-20)."""
    __tablename__ = "status_history"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), nullable=False, index=True)
    old_status = db.Column(db.String(20))
    new_status = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    report = db.relationship("Report", back_populates="history")
    changed_by = db.relationship("User")

    @property
    def new_status_label(self) -> str:
        return Status.LABELS.get(self.new_status, self.new_status)


class AISuggestion(db.Model):
    """AI suggestions and whether people accepted them (REQ-14).

    report_id is filled when the report is saved, or points to the existing report a resident joined.
    """
    __tablename__ = "ai_suggestions"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), index=True)
    kind = db.Column(db.String(20), nullable=False)          # category, duplicate or priority
    suggested_value = db.Column(db.String(120))
    reasons = db.Column(db.Text)
    final_value = db.Column(db.String(120))
    accepted = db.Column(db.Boolean)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    report = db.relationship("Report")
