"""The web panel asks for a password only when PANEL_PASSWORD is set."""
import base64

import pytest

from webapp.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _basic(password, user="anyone"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_open_when_no_password_is_set(client, monkeypatch):
    monkeypatch.delenv("PANEL_PASSWORD", raising=False)
    assert client.get("/jobs.json").status_code == 200


def test_password_is_required_once_set(client, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secret-123")
    r = client.get("/jobs.json")
    assert r.status_code == 401
    assert "Basic" in r.headers["WWW-Authenticate"]


def test_wrong_password_is_rejected(client, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secret-123")
    assert client.get("/jobs.json", headers=_basic("nope")).status_code == 401


def test_right_password_works_with_any_login(client, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secret-123")
    assert client.get("/jobs.json", headers=_basic("secret-123")).status_code == 200
    assert client.get("/jobs.json", headers=_basic("secret-123", user="yurii")).status_code == 200
