import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _normalize_database_url(raw_url: str) -> str:
    # Railway's Postgres plugin (and most managed Postgres providers) hand
    # you a plain "postgresql://..." URL. SQLAlchemy needs to know which
    # driver to use, so it must say "postgresql+psycopg://..." instead.
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw_url


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.getenv(
        "REDIS_URL",
        f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}",
    )
    database_url: str = _normalize_database_url(
        os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://taskforge:taskforge_dev_password"
            "@localhost:5432/taskforge",
        )
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-insecure-secret-change-me")


settings = Settings()