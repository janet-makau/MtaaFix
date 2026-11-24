"""Registration, login, sessions and role checks (REQ-1 to REQ-3, NFR-10 to NFR-12, SDS 7.1)."""
from datetime import timedelta

from mtaafix.extensions import db
from mtaafix.models import Role, User

from conftest import PASSWORD, login


def test_register_creates_resident_with_hashed_password(client, app):
    response = client.post("/register", data={
        "full_name": "Mary Atieno", "email": "Mary@Test.ke",
        "password": "Secret@123", "confirm": "Secret@123"})
    assert response.status_code == 302
    user = User.query.filter_by(email="mary@test.ke").one()      # stored in lower case
    assert user.role == Role.RESIDENT
    assert user.password_hash != "Secret@123"
    assert user.check_password("Secret@123")


def test_register_rejects_existing_email(client, users):
    response = client.post("/register", data={
        "full_name": "Copy", "email": "resident@test.ke",
        "password": "Secret@123", "confirm": "Secret@123"})
    assert response.status_code == 200
    assert b"An account with this email already exists." in response.data
    assert User.query.filter_by(email="resident@test.ke").count() == 1


def test_wrong_password_and_unknown_email_show_the_same_message(client, users):
    wrong_password = login(client, "resident@test.ke", "not-the-password")
    unknown_email = login(client, "nobody@test.ke")
    for response in (wrong_password, unknown_email):
        assert response.status_code == 200
        assert b"Incorrect email or password." in response.data


def test_each_role_lands_on_its_own_dashboard(client, users):
    expected = {"resident": "/resident/reports", "officer": "/officer/tasks", "admin": "/authority/",
                "superadmin": "/admin/users"}
    for key, path in expected.items():
        response = login(client, users[key].email)
        assert response.status_code == 302
        assert response.headers["Location"].startswith(path)
        client.post("/logout")


def test_deactivated_account_cannot_log_in(client, users):
    users["resident"].active = False
    db.session.commit()
    response = login(client, "resident@test.ke")
    assert b"This account is deactivated" in response.data


def test_deactivated_user_is_logged_out_on_next_request(client, users):
    login(client, "resident@test.ke", remember=True)
    assert client.get("/resident/reports").status_code == 200

    users["resident"].active = False          # Super Administrator deactivates the account
    db.session.commit()

    response = client.get("/resident/reports")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_session_has_idle_timeout_and_remember_cookie_lasts_14_days(client, app, users):
    assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(minutes=30)
    assert app.config["REMEMBER_COOKIE_DURATION"] == timedelta(days=14)

    response = login(client, "resident@test.ke", remember=True)
    cookies = response.headers.getlist("Set-Cookie")
    session_cookie = next(c for c in cookies if c.startswith("session="))
    assert "Expires=" in session_cookie and "HttpOnly" in session_cookie   # permanent, idle-limited session
    assert any(c.startswith("remember_token=") for c in cookies)


def test_logout_requires_post_and_clears_the_session(client, users):
    login(client, "resident@test.ke")
    assert client.get("/logout").status_code == 405       # no logout by a simple link
    client.post("/logout")
    response = client.get("/resident/reports")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_roles_cannot_open_other_roles_pages(client, users):
    login(client, "resident@test.ke")
    for path in ("/authority/", "/officer/tasks", "/admin/users"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/resident/reports")


def test_logged_out_user_is_sent_to_login(client, users):
    response = client.get("/authority/")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_login_next_parameter_cannot_redirect_to_another_site(client, users):
    response = client.post("/login?next=https://evil.example.com/",
                           data={"email": "resident@test.ke", "password": PASSWORD})
    assert response.status_code == 302
    assert "evil.example.com" not in response.headers["Location"]
