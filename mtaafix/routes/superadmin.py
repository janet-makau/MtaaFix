"""Super Administrator pages: user accounts and report categories (REQ-4, REQ-23, REQ-24, REQ-26)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..forms import CategoryForm, ChangeRoleForm, ConfirmForm, StaffAccountForm
from ..models import Category, Role, User
from ..security import role_required
from ..services.workflow import open_reports_for_officer

bp = Blueprint("superadmin", __name__)


@bp.before_request
@role_required(Role.SUPER_ADMIN)
def _only_super_admin():
    """Every page in this blueprint is for the Super Administrator only."""


# ------------------------------------------------------------------ users
@bp.route("/users")
def users():
    role_filter = request.args.get("role", "")
    query = User.query
    if role_filter in Role.LABELS:
        query = query.filter_by(role=role_filter)
    return render_template("superadmin/users.html", users=query.order_by(User.full_name).all(),
                           role_filter=role_filter)


@bp.route("/users/new", methods=["GET", "POST"])
def create_staff():
    form = StaffAccountForm()
    if form.validate_on_submit():
        user = User(full_name=form.full_name.data, email=form.email.data, role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f"{user.role_label} account created for {user.full_name}.", "success")
        return redirect(url_for(".users"))
    return render_template("superadmin/staff_form.html", form=form)


@bp.route("/users/<int:user_id>/role", methods=["POST"])
def change_role(user_id: int):
    user = _get_user(user_id)
    form = ChangeRoleForm()
    if not form.validate_on_submit():
        abort(400)
    new_role = form.role.data
    if user.id == current_user.id and new_role != Role.SUPER_ADMIN:
        flash("You cannot remove your own Super Administrator role.", "danger")
        return redirect(url_for(".users"))
    if user.role == Role.FIELD_OFFICER and new_role != Role.FIELD_OFFICER and _blocked_by_open_reports(user):
        return redirect(url_for(".users"))
    user.role = new_role
    db.session.commit()
    flash(f"{user.full_name} is now a {user.role_label}.", "success")
    return redirect(url_for(".users"))


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
def toggle_active(user_id: int):
    user = _get_user(user_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    if user.active:
        if user.id == current_user.id:
            flash("You cannot deactivate your own account.", "danger")
            return redirect(url_for(".users"))
        if user.role == Role.FIELD_OFFICER and _blocked_by_open_reports(user):
            return redirect(url_for(".users"))
    user.active = not user.active
    db.session.commit()
    flash(f"{user.full_name} has been {'reactivated' if user.active else 'deactivated'}.", "success")
    return redirect(url_for(".users"))


def _blocked_by_open_reports(officer: User) -> bool:
    """REQ-26: a Field Officer with open reports must have them reassigned first."""
    reports = open_reports_for_officer(officer)
    if not reports:
        return False
    refs = ", ".join(r.reference for r in reports)
    flash(f"{officer.full_name} still has open reports ({refs}). Ask the Authority Administrator "
          "to reassign them first.", "danger")
    return True


def _get_user(user_id: int) -> User:
    return db.session.get(User, user_id) or abort(404)


# ------------------------------------------------------------------ categories
@bp.route("/categories", methods=["GET", "POST"])
def categories():
    form = CategoryForm()
    if form.validate_on_submit():
        db.session.add(Category(name=form.name.data))
        if _commit_unique(f"Category \"{form.name.data}\" added."):
            return redirect(url_for(".categories"))
    return render_template("superadmin/categories.html", form=form,
                           categories=Category.query.order_by(Category.name).all())


@bp.route("/categories/<int:category_id>/rename", methods=["POST"])
def rename_category(category_id: int):
    category = db.session.get(Category, category_id) or abort(404)
    form = CategoryForm()
    if form.validate_on_submit():
        category.name = form.name.data
        _commit_unique("Category renamed.")
    return redirect(url_for(".categories"))


@bp.route("/categories/<int:category_id>/toggle", methods=["POST"])
def toggle_category(category_id: int):
    """REQ-24: a deactivated category is hidden from new reports but kept on existing ones."""
    category = db.session.get(Category, category_id) or abort(404)
    if ConfirmForm().validate_on_submit():
        category.active = not category.active
        db.session.commit()
        flash(f"\"{category.name}\" {'reactivated' if category.active else 'deactivated'}.", "success")
    return redirect(url_for(".categories"))


def _commit_unique(success_message: str) -> bool:
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("A category with that name already exists.", "danger")
        return False
    flash(success_message, "success")
    return True
