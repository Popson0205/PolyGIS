from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str
    # PolyGIS keeps its tables in this schema of Geohub's database; PostGIS stays in public.
    db_schema: str = "gis"
    db_pool_max: int = 5
    app_url: str = "http://localhost:5176"     # where people open PolyGIS
    api_prefix: str = ""                       # "/api" when one service serves the API and the built web app
    web_dir: str = ""

    # Signing in: GeoEstate ID, through Geohub (Geohub's ID_CLIENTS must list this app with the same secret)
    geohub_url: str = "http://localhost:5173"  # Geohub's address (the issuer)
    geohub_api: str = "http://localhost:8000"  # Geohub's API (…/api in production)
    id_client: str = "gis"
    id_secret: str = ""
    cookie_secure: bool | None = None          # default: secure when APP_URL is https

    # Uploads: the largest file accepted, and the most features one layer may hold.
    max_upload_mb: int = 200
    max_features: int = 500_000

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _checks(self):
        self.app_url = self.app_url.strip().rstrip("/")
        self.geohub_url = self.geohub_url.strip().rstrip("/")
        self.geohub_api = self.geohub_api.strip().rstrip("/")
        if not self.db_schema.isidentifier() or self.db_schema.lower() == "public":
            raise ValueError("DB_SCHEMA must be a plain name other than public, e.g. gis")
        if self.cookie_secure is None:
            self.cookie_secure = self.app_url.startswith("https://")
        return self


settings = Settings()
