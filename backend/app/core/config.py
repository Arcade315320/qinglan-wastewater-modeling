from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Wastewater Process Modeling API"
    app_version: str = "0.1.0"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://arcade315320.github.io",
        ]
    )


settings = Settings()
