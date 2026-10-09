from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Settings
    Équivalent conceptuel de config/*.php et du .env dans Laravel.
    Les variables d'environnement sont strictement typées et validées au démarrage.
    """

    APP_NAME: str = "Todo API"
    APP_ENV: str = "development"
    DEBUG: bool = False
    API_V1_STR: str = "/api/v1"

    # Configuration PostgreSQL
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "todo_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 5

    # Refresh Token
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_TOKEN_ABSOLUTE_MAX_DAYS: int = 90

    # Rate limiting
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW_MINUTES: int = 30
    SENSITIVE_RATE_LIMIT_MAX_ATTEMPTS: int = 5
    SENSITIVE_RATE_LIMIT_WINDOW_MINUTES: int = 30
    GENERAL_RATE_LIMIT_MAX_REQUESTS: int = 100
    GENERAL_RATE_LIMIT_WINDOW_MINUTES: int = 1

    # Idempotency key
    IDEMPOTENCY_KEY_TTL_MINUTES: int = 60
    IDEMPOTENCY_LOCK_TTL_SECONDS: int = 30

    # Taskiq
    TASK_RESULT_TTL_HOURS: int = 2

    # Prunable
    SOFT_DELETE_RETENTION_DAYS: int = 30
    JOB_RETENTION_DAYS: int = 30
    API_TOKEN_EXPIRED_RETENTION_DAYS: int = 30

    # API token
    API_TOKEN_MAX_ACTIVE: int = 5
    API_TOKEN_PREFIX="tda_"

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret_key(cls, v: str) -> str:
        if v.startswith("change-me") or len(v.encode()) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 bytes and must not be the "
                "'change-me' placeholder"
            )
        return v

    # URL de connexion directe (optionnelle, surchargée par Docker Compose)
    DATABASE_URL: str | None = None

    @computed_field  # Champ calculé automatiquement si DATABASE_URL n'est pas explicite
    @property
    def async_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )


settings = Settings()
