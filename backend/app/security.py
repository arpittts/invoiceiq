"""Password hashing (bcrypt) and access tokens (JWT)."""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    # bcrypt salts automatically; the salt is stored inside the hash string.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """Return the user id inside a valid token, or None if it is invalid or expired."""
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
