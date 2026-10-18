from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.models import User

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
GOOD = {"email": "Owner@Example.com", "password": "correct-horse-battery"}


def test_register_creates_user_without_leaking_hash(client):
    response = client.post(REGISTER, json=GOOD)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "owner@example.com"  # stored lowercase
    assert "password" not in body and "password_hash" not in body


def test_password_is_stored_hashed(client, db):
    client.post(REGISTER, json=GOOD)
    user = db.scalar(select(User))
    assert user.password_hash != GOOD["password"]
    assert user.password_hash.startswith("$2b$")  # bcrypt format


def test_register_duplicate_email_is_rejected_case_insensitively(client):
    client.post(REGISTER, json=GOOD)
    response = client.post(REGISTER, json={**GOOD, "email": "OWNER@example.COM"})
    assert response.status_code == 409


def test_register_validates_input(client):
    assert client.post(REGISTER, json={**GOOD, "email": "not-an-email"}).status_code == 422
    assert client.post(REGISTER, json={**GOOD, "password": "short"}).status_code == 422
    assert client.post(REGISTER, json={**GOOD, "password": "x" * 73}).status_code == 422


def test_login_returns_token_that_works(client):
    client.post(REGISTER, json=GOOD)
    response = client.post(LOGIN, json={"email": "owner@example.com", "password": GOOD["password"]})
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"


def test_login_failures_share_one_message(client):
    client.post(REGISTER, json=GOOD)
    wrong_password = client.post(LOGIN, json={**GOOD, "password": "wrong-password-123"})
    unknown_email = client.post(LOGIN, json={**GOOD, "email": "nobody@example.com"})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_protected_route_rejects_missing_bad_and_expired_tokens(client):
    client.post(REGISTER, json=GOOD)
    assert client.get(ME).status_code == 401
    assert client.get(ME, headers={"Authorization": "Bearer garbage"}).status_code == 401

    expired = jwt.encode(
        {"sub": "1", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        "test-secret-that-is-long-enough-for-hs256-signing",
        algorithm="HS256",
    )
    assert client.get(ME, headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    forged = jwt.encode({"sub": "1"}, "some-other-secret-that-is-also-long-enough", "HS256")
    assert client.get(ME, headers={"Authorization": f"Bearer {forged}"}).status_code == 401
