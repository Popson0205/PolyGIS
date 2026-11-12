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
    tool_timeout_s: int = 120                  # the longest a geoprocessing tool may run

    # Rasters (elevation models, imagery, results of raster tools) are kept as Cloud-Optimized GeoTIFFs: in an
    # S3-compatible bucket when S3_ENDPOINT is set (e.g. the Cloudflare R2 bucket Geohub uses, under S3_PREFIX),
    # with RASTER_DIR as the local copy the map tiles and tools read; without a bucket, RASTER_DIR alone (give it a
    # persistent volume then).
    raster_dir: str = "./data/rasters"
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = ""
    s3_region: str = ""
    s3_prefix: str = "polygis/rasters/"
    max_raster_mb: int = 1000                  # the largest raster file accepted
    max_raster_cells: int = 60_000_000         # the most cells a raster tool works on (per band)
    max_hydro_cells: int = 16_000_000          # … and a hydrology tool, which needs more memory per cell

    # Satellite imagery from open sources (Add data > Satellite imagery). Planetary Computer and Earth Search need nothing.
    # Google Earth Engine is offered when GEE_PROJECT is set: with a service account (GEE_SERVICE_ACCOUNT and GEE_PRIVATE_KEY,
    # the key file's JSON or a path to it), or with the credentials the server's environment already holds.
    gee_project: str = ""
    gee_service_account: str = ""
    gee_private_key: str = ""
    imagery_max_mb: int = 128                  # the most memory one imagery request may use (cells x bands x bytes); larger areas get larger cells
    imagery_gee_max_mb: int = 40               # … for Earth Engine, whose direct download is limited to about 48 MB
    imagery_timeout_s: int = 240               # the longest an imagery request may take
    # Poly Earth (Earth Engine-style analyses on open data), run in the background with progress shown
    earth_max_mb: int = 384                    # the most memory one run's scenes may take; larger areas get larger cells
    earth_timeout_s: int = 900                 # the longest a run may take
    earth_workers: int = 12                    # files read at once (the work waits on the network, not the processor)
    earth_jobs: int = 2                        # runs at once on the server (others wait their turn)

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _checks(self):
        self.app_url = self.app_url.strip().rstrip("/")
        self.geohub_url = self.geohub_url.strip().rstrip("/")
        self.geohub_api = self.geohub_api.strip().rstrip("/")
        # pasted values often carry spaces or quotes; Geohub trims its copy of the secret, so trim this one too
        self.id_secret = self.id_secret.strip().strip('"').strip("'").strip()
        self.id_client = self.id_client.strip() or "gis"
        if not self.db_schema.isidentifier() or self.db_schema.lower() == "public":
            raise ValueError("DB_SCHEMA must be a plain name other than public, e.g. gis")
        if self.cookie_secure is None:
            self.cookie_secure = self.app_url.startswith("https://")
        return self


settings = Settings()
