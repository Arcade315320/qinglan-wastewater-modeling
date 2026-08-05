from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Wastewater Process Modeling API"
    app_version: str = "0.1.0"
    modal_simulation_url: str | None = None
    modal_auth_token: str | None = None
    simulation_timeout_seconds: float = 880.0
    simulation_worker_count: int = Field(default=3, ge=1, le=8)
    database_path: str | None = None
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://arcade315320.github.io",
        ]
    )


settings = Settings()
