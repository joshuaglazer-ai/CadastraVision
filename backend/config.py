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


PARCELS_FILE_NAME = "candidate_parcels.geojson"
LANDCOVER_FILE_NAME = "uplarshi_landcover_with_attributes.geojson"
MODEL_FILE_NAME = "best_weighted_multiclass_unet.pth"


@dataclass(frozen=True)
class FileResolution:
    """Where a required file was looked for and what was found.

    ``status`` is one of:

    * ``found``     - the expected file exists under its exact name
    * ``fallback``  - it does not, but its folder holds exactly one file of
                      the same type (for example a browser download named
                      ``candidate_parcels (2).geojson``), which is used
    * ``ambiguous`` - several files of that type and none with the exact
                      name; nothing is used, because guessing could load the
                      wrong data
    * ``missing``   - no file of that type in the folder
    """

    expected: Path
    path: Path | None
    status: str
    candidates: tuple[Path, ...] = ()

    @property
    def usable(self) -> bool:
        return self.path is not None

    def describe(self) -> dict:
        """JSON form for the API. Paths are shown relative to the project."""

        folder = display_path(self.expected.parent)
        expected = display_path(self.expected)
        if self.status == "found":
            message = None
        elif self.status == "fallback":
            message = (
                f"Using {self.path.name} because {self.expected.name} is not present in {folder}/. "
                f"Rename it to {self.expected.name} to make this explicit."
            )
        elif self.status == "ambiguous":
            names = ", ".join(path.name for path in self.candidates)
            message = (
                f"Several {self.expected.suffix} files in {folder}/ ({names}) and none is named "
                f"{self.expected.name}. Keep one, or rename the right one to {self.expected.name}."
            )
        else:
            message = f"Expected file {expected} is missing. Place the file there under that name."
        return {
            "status": self.status,
            "expected": expected,
            "used": self.path.name if self.path else None,
            "candidates": [path.name for path in self.candidates],
            "message": message,
        }


def display_path(path: Path) -> str:
    """A path as the operator would type it from the repository root.

    Paths outside the repository (for example a ``CADASTRA_DATA_DIR`` on
    another disk) are shown from their last two components so that server
    paths are not exposed in API responses.
    """

    path = Path(path)
    try:
        return path.resolve().relative_to(BASE_DIR.parent.resolve()).as_posix()
    except ValueError:
        return Path(*path.parts[-3:]).as_posix() if len(path.parts) >= 3 else path.name


def resolve_file(expected: Path) -> FileResolution:
    """Find ``expected``, or the only file of its type in the same folder."""

    expected = Path(expected)
    if expected.is_file():
        return FileResolution(expected, expected, "found")
    folder = expected.parent
    suffix = expected.suffix.lower()
    candidates: tuple[Path, ...] = ()
    if folder.is_dir():
        candidates = tuple(
            sorted(
                (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == suffix),
                key=lambda p: p.name.lower(),
            )
        )
    if len(candidates) == 1:
        return FileResolution(expected, candidates[0], "fallback", candidates)
    if candidates:
        return FileResolution(expected, None, "ambiguous", candidates)
    return FileResolution(expected, None, "missing")


@dataclass
class Settings:
    """Runtime settings. Construct with ``load_settings()``."""

    # --- locations -------------------------------------------------------
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    # Where the checkpoint is expected; ``model_path`` applies the fallback.
    configured_model_path: Path = BASE_DIR / "models" / MODEL_FILE_NAME
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
    # None: derived per raster from the minimum mapping unit
    # (sliver_area_m2 / true ground area of one pixel). An integer overrides.
    sieve_min_pixels: int | None = None
    sliver_area_m2: float = 1.0
    export_background: bool = False
    max_upload_mb: int = 4096

    # --- GIS -------------------------------------------------------------
    road_access_distance_m: float = 5.0
    map_feature_limit: int = 4000
    min_candidate_parcel_m2: float = 25.0
    built_cluster_buffer_m: float = 3.0

    # --- candidate plots (morphological tessellation) ---------------------
    plots_enabled: bool = True
    plot_limit_m: float = 25.0      # land farther than this from a building is not assigned
    plot_grid_m: float = 0.10       # working grid for dividing the land
    plot_min_building_m2: float = 5.0

    # --- derived paths ---------------------------------------------------
    # The three required files are resolved on every access (one directory
    # listing), so a file copied in while the server runs is picked up. When
    # nothing usable is found the expected path is returned; it does not
    # exist, so callers report the file as unavailable.
    @property
    def parcels_resolution(self) -> FileResolution:
        return resolve_file(self.data_dir / "parcels" / PARCELS_FILE_NAME)

    @property
    def landcover_resolution(self) -> FileResolution:
        return resolve_file(self.data_dir / "landcover" / LANDCOVER_FILE_NAME)

    @property
    def model_resolution(self) -> FileResolution:
        return resolve_file(self.configured_model_path)

    @property
    def parcels_file(self) -> Path:
        resolution = self.parcels_resolution
        return resolution.path or resolution.expected

    @property
    def landcover_file(self) -> Path:
        resolution = self.landcover_resolution
        return resolution.path or resolution.expected

    @property
    def model_path(self) -> Path:
        resolution = self.model_resolution
        return resolution.path or resolution.expected

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

    def ensure_dirs(self) -> None:
        for path in (
            self.cache_dir,
            self.state_db.parent,
            self.upload_dir,
            self.output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    data_dir = Path(os.getenv("CADASTRA_DATA_DIR") or (BASE_DIR / "data"))
    processing_dir = Path(os.getenv("CADASTRA_PROCESSING_DIR") or (BASE_DIR / "processing"))
    model_path = Path(os.getenv("CADASTRA_MODEL_PATH") or (BASE_DIR / "models" / MODEL_FILE_NAME))

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
        configured_model_path=model_path,
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
        sieve_min_pixels=(_int("SIEVE_MIN_PIXELS", 0) or None) if os.getenv("SIEVE_MIN_PIXELS") else None,
        sliver_area_m2=_float("SLIVER_AREA_M2", 1.0),
        export_background=_bool("EXPORT_BACKGROUND", False),
        max_upload_mb=_int("MAX_UPLOAD_MB", 4096),
        road_access_distance_m=_float("ROAD_ACCESS_DISTANCE_M", 5.0),
        map_feature_limit=_int("MAP_FEATURE_LIMIT", 4000),
        min_candidate_parcel_m2=_float("MIN_CANDIDATE_PARCEL_M2", 25.0),
        built_cluster_buffer_m=_float("BUILT_CLUSTER_BUFFER_M", 3.0),
        plots_enabled=_bool("PLOTS_ENABLED", True),
        plot_limit_m=_float("PLOT_LIMIT_M", 25.0),
        plot_grid_m=_float("PLOT_GRID_M", 0.10),
        plot_min_building_m2=_float("PLOT_MIN_BUILDING_M2", 5.0),
    )


settings = load_settings()
