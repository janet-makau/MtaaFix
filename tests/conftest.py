"""Shared test fixtures.

Every test gets a fresh in-memory database with the default categories and one active
account per role. Emails are never sent (MAIL_USERNAME is empty in TestingConfig) and the
AI service is never called for real (see `fake_ai`).

Run all tests from the project folder with:   python -m pytest
"""
import io

import pytest
from PIL import Image

from mtaafix import create_app
from mtaafix.cli import init_database
from mtaafix.extensions import db as _db
from mtaafix.models import Category, Role, User
from mtaafix.services import ai
from mtaafix.services.reports import create_report

PASSWORD = "Test@2026"


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        init_database()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    """A request context, needed by code that builds links (emails) or reads the client address."""
    with app.test_request_context():
        yield


def _user(name: str, email: str, role: str, active: bool = True) -> User:
    user = User(full_name=name, email=email, role=role, active=active)
    user.set_password(PASSWORD)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def users(app):
    return {
        "resident": _user("Wanjiru Kamau", "resident@test.ke", Role.RESIDENT),
        "resident2": _user("Brian Otieno", "brian@test.ke", Role.RESIDENT),
        "officer": _user("Peter Mwangi", "officer@test.ke", Role.FIELD_OFFICER),
        "officer2": _user("Aisha Hassan", "aisha@test.ke", Role.FIELD_OFFICER),
        "admin": _user("Grace Njeri", "admin@test.ke", Role.AUTHORITY_ADMIN),
        "superadmin": _user("Daniel Kiprop", "super@test.ke", Role.SUPER_ADMIN),
    }


@pytest.fixture
def category(app):
    return Category.query.filter_by(name="Roads").one()


@pytest.fixture
def make_report(users, category):
    """Create a Submitted report. Each call uses a new form token."""
    counter = {"n": 0}

    def _make(resident=None, description="Large pothole outside the market near the matatu stage.",
              cat=None, latitude=-1.3082, longitude=36.7989):
        counter["n"] += 1
        report, _ = create_report(resident or users["resident"], description=description,
                                  category=cat or category, latitude=latitude, longitude=longitude,
                                  photo_url=None, token=f"token-{counter['n']}")
        return report
    return _make


def login(client, email: str, password: str = PASSWORD, remember: bool = False):
    data = {"email": email, "password": password}
    if remember:
        data["remember"] = "y"
    return client.post("/login", data=data, follow_redirects=False)


def image_bytes(fmt: str = "JPEG", size=(40, 30)) -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 110, 100)).save(buffer, fmt)
    buffer.seek(0)
    return buffer


@pytest.fixture
def fake_ai(app, monkeypatch):
    """Switch AI on and replace the HTTP call with a fake.

    Set `fake_ai.reply` to the dict the model should return, or `fake_ai.error` to an exception
    to raise. `fake_ai.requests` keeps every request body, so tests can check what was sent.
    """
    class FakeAI:
        reply: dict | None = None
        error: Exception | None = None
        requests: list = []

        def __call__(self, url, body, api_key, timeout):
            self.requests.append(body)
            if self.error:
                raise self.error
            import json
            return {"choices": [{"message": {"content": json.dumps(self.reply)}}]}

    fake = FakeAI()
    fake.requests = []
    app.config.update(AI_ENABLED=True, OPENAI_API_KEY="test-key")
    monkeypatch.setattr(ai, "_post_json", fake)
    return fake
