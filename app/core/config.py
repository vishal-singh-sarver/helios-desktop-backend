from pathlib import Path
from pydantic import AliasChoices, Field
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

    # Storage — accepts HELIOS_DATA_DIR (set by the Electron main process in
    # packaged mode via backend-manager.ts) or DATA_DIR (for standalone/dev
    # runs). Without this alias, pydantic would only read DATA_DIR and the
    # packaged app would silently fall back to Path("data") relative to cwd.
    data_dir: Path = Field(
        default=Path("data"),
        validation_alias=AliasChoices("HELIOS_DATA_DIR", "DATA_DIR"),
    )

    # Database — derived from data_dir unless explicitly set
    db_path: Path | None = None
    db_echo: bool = False

    # Projects — derived from data_dir unless explicitly set
    projects_dir: Path | None = None

    # PyHelios
    pyhelios_use_pip: bool = False
    # Override source path; if blank, auto-detected as <project_root>/pyhelios
    pyhelios_source_path: str | None = None

    @property
    def pyhelios_auto_source_path(self) -> Path:
        """Absolute path to the PyHelios submodule directory."""
        if self.pyhelios_source_path:
            return Path(self.pyhelios_source_path)
        # app/core/config.py → app/core → app → project_root
        return Path(__file__).resolve().parent.parent.parent / "pyhelios"

    # Security
    secret_key: str = "change-me"

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or self.data_dir / "heliosgui.db"

    @property
    def resolved_projects_dir(self) -> Path:
        return self.projects_dir or self.data_dir / "projects"

    def scenario_dir(self, project_id: str, scenario_id: str) -> Path:
        """Canonical per-scenario folder nested under its parent project.

        Layout under this folder:
            context_file/
                context.xml         PyHelios state
                archives/           rotated autosaves (.gz)
            weather/                uploaded weather CSVs
            metadata/               reserved for future use
            export_files/           reserved for future use
        """
        return self.resolved_projects_dir / project_id / "scenarios" / scenario_id

    def scenario_context_file_dir(self, project_id: str, scenario_id: str) -> Path:
        """`context_file/` subfolder where context.xml + archives live."""
        return self.scenario_dir(project_id, scenario_id) / "context_file"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
