from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    DATABASE_URL: str = Field(default="postgresql://postgres.iheptwqcrfeeluxmpizx:chicago@loopback1@aws-1-us-east-1.pooler.supabase.com:6543/postgres")
    MAPBOX_TOKEN: str = Field(default="")
    MAX_MAPBOX_ROUTES: int = Field(default=3)

    GEMINI_API_KEY: str = Field(default="")
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")
    CORS_ORIGINS: str = Field(default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000")

    GEOHASH_PRECISION: int = Field(default=7)
    ISSUE_NEAR_ROUTE_METERS: int = Field(default=80)
    MAX_LLM_SEVERITY_ADJUST: int = Field(default=1)

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()