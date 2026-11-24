"""All web forms. Flask-WTF checks every input on the server and adds a CSRF token (NFR-13)."""
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (BooleanField, EmailField, HiddenField, PasswordField, SelectField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

from .models import CloseReason, Priority, Role, User


def _strip_lower(value):
    return value.strip().lower() if isinstance(value, str) else value


def _strip(value):
    return value.strip() if isinstance(value, str) else value


# ---------------------------------------------------------------- accounts (Phase 1)
class RegistrationForm(FlaskForm):
    """Public registration always creates a Resident (REQ-1, REQ-4)."""
    full_name = StringField("Full name", filters=[_strip],
                            validators=[DataRequired("Enter your full name."), Length(max=120)])
    email = EmailField("Email address", filters=[_strip_lower],
                       validators=[DataRequired("Enter your email address."),
                                   Email("Enter a valid email address.", check_deliverability=False),
                                   Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired("Enter a password."),
                                                     Length(min=8, message="Password must be at least 8 characters.")])
    confirm = PasswordField("Confirm password", validators=[EqualTo("password", "Passwords do not match.")])
    submit = SubmitField("Create account")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError("An account with this email already exists.")


class LoginForm(FlaskForm):
    email = EmailField("Email address", filters=[_strip_lower], validators=[DataRequired("Enter your email address.")])
    password = PasswordField("Password", validators=[DataRequired("Enter your password.")])
    remember = BooleanField("Keep me logged in")
    submit = SubmitField("Log in")


class StaffAccountForm(FlaskForm):
    """Staff accounts are created only by the Super Administrator (REQ-4, REQ-23)."""
    full_name = StringField("Full name", filters=[_strip], validators=[DataRequired(), Length(max=120)])
    email = EmailField("Email address", filters=[_strip_lower],
                       validators=[DataRequired(), Email("Enter a valid email address.", check_deliverability=False)])
    role = SelectField("Role", choices=[(r, Role.LABELS[r]) for r in Role.STAFF])
    password = PasswordField("Temporary password",
                             validators=[DataRequired(), Length(min=8, message="Password must be at least 8 characters.")])
    submit = SubmitField("Create account")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError("An account with this email already exists.")


class ChangeRoleForm(FlaskForm):
    role = SelectField("Role", choices=list(Role.LABELS.items()))
    submit = SubmitField("Change role")


class CategoryForm(FlaskForm):
    name = StringField("Category name", filters=[_strip], validators=[DataRequired(), Length(max=60)])
    submit = SubmitField("Save")


class ConfirmForm(FlaskForm):
    """An empty form used for one-click actions, so they still carry a CSRF token."""
    submit = SubmitField("Confirm")


# ---------------------------------------------------------------- reports (Phase 2)
class ReportForm(FlaskForm):
    description = TextAreaField(
        "Describe the problem", filters=[_strip],
        validators=[DataRequired("Describe the problem."),
                    Length(min=10, max=2000, message="Use between 10 and 2,000 characters.")])
    category = SelectField("Category", coerce=int, validators=[DataRequired("Choose a category.")])
    latitude = HiddenField()
    longitude = HiddenField()
    photo = FileField("Photo (optional)", validators=[FileAllowed(["jpg", "jpeg", "png"], "Only JPEG or PNG photos are allowed.")])
    submission_token = HiddenField()
    submit = SubmitField("Submit report")

    def validate_latitude(self, field):
        """REQ-6: a pin on the map is required, and must be a real coordinate."""
        try:
            lat, lng = float(field.data), float(self.longitude.data)
        except (TypeError, ValueError):
            raise ValidationError("Place a pin on the map to show where the problem is.")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValidationError("The map location is not valid. Please place the pin again.")


# ---------------------------------------------------------------- workflow (Phase 3)
class PriorityForm(FlaskForm):
    priority = SelectField("Priority", choices=list(Priority.LABELS.items()))
    submit = SubmitField("Save priority")


class AssignForm(FlaskForm):
    officer = SelectField("Field Officer", coerce=int)
    note = TextAreaField("Note", filters=[_strip], validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Assign")


class StatusForm(FlaskForm):
    status = SelectField("New status")
    note = TextAreaField("Note", filters=[_strip], validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Update status")


class CloseForm(FlaskForm):
    reason = SelectField("Reason", choices=list(CloseReason.LABELS.items()))
    original_reference = StringField("Original report reference (for duplicates)", filters=[_strip],
                                     validators=[Optional(), Length(max=20)])
    note = TextAreaField("Note", filters=[_strip], validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Close report")


class ReopenForm(FlaskForm):
    note = TextAreaField("Reason for reopening", filters=[_strip],
                         validators=[DataRequired("Give a reason for reopening."), Length(max=1000)])
    submit = SubmitField("Reopen report")
