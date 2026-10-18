import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "given",
    [
        "postgresql://u:p@ep-cool-name.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
        "postgres://u:p@ep-cool-name.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
        "postgresql+psycopg://u:p@ep-cool-name.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
    ],
)
def test_database_url_always_uses_psycopg_driver(given):
    settings = Settings(database_url=given, jwt_secret="x" * 40)
    assert settings.database_url == (
        "postgresql+psycopg://u:p@ep-cool-name.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    )


def test_allowed_origins_are_split_and_trimmed():
    settings = Settings(
        allowed_origins="https://a.vercel.app, http://localhost:5173 ,", jwt_secret="x" * 40
    )
    assert settings.allowed_origins_list == ["https://a.vercel.app", "http://localhost:5173"]
