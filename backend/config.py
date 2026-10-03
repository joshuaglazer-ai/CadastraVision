"""Central configuration for the Cadastra Vision backend.

Every path and tunable lives here so that services never hard-code a
location or a dataset name. Values come from environment variables (see
``backend/.env.example``); nothing secret is given a default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is in requirements.txt; tolerate its absence in tooling
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

APP_NAME = "Cadastra Vision"
APP_TAGLINE = "AI + GIS Surveyor Assistance Platform"
APP_PRINCIPLE = "AI proposes. GIS validates. Surveyors verify."
API_VERSION = "3.0.0"

DISCLAIMER = (
    "AI-assisted spatial features require surveyor verification and are "
    "not legal cadastral ownership records."
)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    """Runtime settings. Construct with ``load_settings()``."""

    # --- locations -------------------------------------------------------
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    model_path: Path = BASE_DIR / "models" / "best_weighted_multiclass_unet.pth"
    processing_dir: Path = BASE_DIR / "processing"

    # --- authentication --------------------------------------------------
    supabase_url: str = ""
    supabase_key: str = ""  # anon key only; never a service-role key
    # "supabase" verifies every request; "off" is an explicit local-dev mode.
    auth_mode: str = "supabase"
    dev_user_email: str = "local.dev@cadastra.invalid"
    allow_demo_assignment: bool = True

    # --- web -------------------------------------------------------------
    cors_origins: list[str] = field(default_factory=list)

    # --- AI pipeline -----------------------------------------------------
    tile_size: int = 512
    tile_overlap: int = 64
    # Must match the preprocessing used when the checkpoint was trained.
    # "scale_255": x / 255.   "imagenet": (x / 255 - mean) / std.
    normalization: str = "scale_255"
    polygonize_chunk: int = 4096
    sieve_min_pixels: int = 8
    sliver_area_m2: float = 1.0
    export_background: bool = False
    max_upload_mb: int = 4096

    # --- GIS -------------------------------------------------------------
    road_access_distance_m: float = 5.0
    map_feature_limit: int = 4000
    min_candidate_parcel_m2: float = 25.0
    built_cluster_buffer_m: float = 3.0

    # --- derived paths ---------------------------------------------------
    @property
    def parcels_file(self) -> Path:
        return self.data_dir / "parcels" / "candidate_parcels.geojson"

    @property
    def landcover_file(self) -> Path:
        return self.data_dir / "landcover" / "uplarshi_landcover_with_attributes.geojson"

    @property
    def assignments_file(self) -> Path:
        return self.data_dir / "surveyors" / "surveyor_assignments.geojson"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state" / "cadastra.db"

    @property
    def upload_dir(self) -> Path:
        return self.processing_dir / "uploads"

    @property
    def output_dir(self) -> Path:
        return self.processing_dir / "outputs"

    @property
    def dataset_upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    def ensure_dirs(self) -> None:
        for path in (
            self.cache_dir,
            self.state_db.parent,
            self.upload_dir,
            self.output_dir,
            self.dataset_upload_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    data_dir = Path(os.getenv("CADASTRA_DATA_DIR") or (BASE_DIR / "data"))
    processing_dir = Path(os.getenv("CADASTRA_PROCESSING_DIR") or (BASE_DIR / "processing"))
    model_path = Path(
        os.getenv("CADASTRA_MODEL_PATH")
        or (BASE_DIR / "models" / "best_weighted_multiclass_unet.pth")
    )

    frontend_url = os.getenv("FRONTEND_URL", "").strip()
    default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://localhost:3000",
    ]
    if frontend_url and frontend_url not in default_origins:
        default_origins.append(frontend_url)

    auth_mode = os.getenv("CADASTRA_AUTH", "supabase").strip().lower()
    if auth_mode not in {"supabase", "off"}:
        auth_mode = "supabase"

    normalization = os.getenv("MODEL_NORMALIZATION", "scale_255").strip().lower()
    if normalization not in {"scale_255", "imagenet"}:
        normalization = "scale_255"

    return Settings(
        data_dir=data_dir,
        model_path=model_path,
        processing_dir=processing_dir,
        supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
        auth_mode=auth_mode,
        dev_user_email=os.getenv("CADASTRA_DEV_USER_EMAIL", "local.dev@cadastra.invalid"),
        allow_demo_assignment=_bool("CADASTRA_ALLOW_DEMO_ASSIGNMENT", True),
        cors_origins=_list("CORS_ORIGINS", default_origins),
        tile_size=_int("TILE_SIZE", 512),
        tile_overlap=_int("TILE_OVERLAP", 64),
        normalization=normalization,
        polygonize_chunk=_int("POLYGONIZE_CHUNK", 4096),
        sieve_min_pixels=_int("SIEVE_MIN_PIXELS", 8),
        sliver_area_m2=_float("SLIVER_AREA_M2", 1.0),
        export_background=_bool("EXPORT_BACKGROUND", False),
        max_upload_mb=_int("MAX_UPLOAD_MB", 4096),
        road_access_distance_m=_float("ROAD_ACCESS_DISTANCE_M", 5.0),
        map_feature_limit=_int("MAP_FEATURE_LIMIT", 4000),
        min_candidate_parcel_m2=_float("MIN_CANDIDATE_PARCEL_M2", 25.0),
        built_cluster_buffer_m=_float("BUILT_CLUSTER_BUFFER_M", 3.0),
    )


settings = load_settings()
