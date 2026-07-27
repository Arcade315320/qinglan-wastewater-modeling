from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Wastewater Process Modeling API"
    app_version: str = "0.1.0"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


settings = Settings()
