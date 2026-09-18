import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    redis_host: str = os.getenv("REDIS_HOST", "localhost")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://taskforge:taskforge_dev_password"
        "@localhost:5432/taskforge",
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-insecure-secret-change-me")


settings = Settings()