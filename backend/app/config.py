from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_data_dir: Path = Path(".local-data")
    app_timezone: str = "Asia/Kolkata"
    mongodb_uri: str | None = None
    mongodb_database: str | None = None
    gemini_api_key: str | None = None
    github_token: str | None = None
    gemini_model: str | None = None
    telegram_bot_token: str | None = None
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str | None = None
    linkedin_api_version: str | None = None
    app_tls_cert: Path | None = None
    app_tls_key: Path | None = None
    publishing_enabled: bool = False

    def missing_configuration(self) -> list[str]:
        required = (
            "mongodb_uri", "mongodb_database", "gemini_api_key", "gemini_model",
            "telegram_bot_token", "linkedin_client_id", "linkedin_client_secret",
            "linkedin_redirect_uri", "linkedin_api_version",
        )
        return [
            name.upper()
            for name in required
            if not (value := getattr(self, name))
            or "<" in value
            or ">" in value
        ]


def get_settings() -> Settings:
    return Settings()
