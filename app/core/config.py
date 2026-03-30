from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_env: str = "development"
    backend_version: str = "1.0.0"
    log_level: str = "INFO"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False

    # CORS — comma-separated origins string from .env, parsed to list below
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Storage
    data_dir: Path = Path("data")

    # Database — derived from data_dir unless explicitly set
    db_path: Path | None = None
    db_echo: bool = False

    # Projects — derived from data_dir unless explicitly set
    projects_dir: Path | None = None

    # PyHelios
    pyhelios_use_pip: bool = False
    pyhelios_source_path: str | None = None

    # Security
    secret_key: str = "change-me"

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or self.data_dir / "heliosgui.db"

    @property
    def resolved_projects_dir(self) -> Path:
        return self.projects_dir or self.data_dir / "projects"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
