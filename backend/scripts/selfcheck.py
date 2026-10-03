"""One-command check of the whole backend on this machine.

    python -m backend.scripts.selfcheck

It verifies, in order: the Python packages, the real checkpoint (strict
load into U-Net / ResNet34 and a forward pass), the complete pipeline on a
small synthetic GeoTIFF using the real weights, and the indexing of the
project's GIS layers. Each step prints PASS or FAIL with the reason.

The synthetic image only exercises the machinery (tiling, NoData, rasters,
polygonisation, repair, measurement, QA). It says nothing about model
accuracy; for that, process a real orthoimage.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import traceback
from pathlib import Path

RESULTS: list[tuple[str, bool, str]] = []


def step(name: str):
    def decorator(func):
        def run() -> bool:
            try:
                detail = func() or ""
                RESULTS.append((name, True, str(detail)))
                print(f"PASS  {name}  {detail}")
                return True
            except Exception as exc:
                RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
                print(f"FAIL  {name}\n      {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=4)
                return False

        return run

    return decorator


@step("Python packages")
def check_packages():
    versions = []
    for module in (
        "fastapi", "numpy", "torch", "segmentation_models_pytorch",
        "rasterio", "geopandas", "shapely", "pyproj",
    ):
        loaded = importlib.import_module(module)
        versions.append(f"{module} {getattr(loaded, '__version__', '?')}")
    import shapely

    if int(shapely.__version__.split(".")[0]) < 2:
        raise RuntimeError(f"Shapely 2.x is required, found {shapely.__version__}")
    return ", ".join(versions)


@step("Checkpoint loads strictly into U-Net / ResNet34 (6 classes)")
def check_model():
    import torch

    from backend.ai.model import get_model
    from backend.config import settings

    model, device = get_model(settings.model_path)
    with torch.no_grad():
        output = model(torch.zeros(1, 3, 512, 512, device=device))
    if tuple(output.shape) != (1, 6, 512, 512):
        raise RuntimeError(f"unexpected output shape {tuple(output.shape)}")
    return f"{settings.model_path.name} on {device}, output {tuple(output.shape)}"


def _synthetic_geotiff(path: Path) -> None:
    """A 1200 x 900 px RGB GeoTIFF in UTM 43N at 5 cm/px with a NoData corner."""

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(7)
    height, width = 900, 1200
    image = np.zeros((3, height, width), dtype=np.uint8)
    image[0] = 96 + rng.integers(-12, 12, (height, width))
    image[1] = 122 + rng.integers(-12, 12, (height, width))
    image[2] = 70 + rng.integers(-12, 12, (height, width))
    image[:, 200:420, 150:520] = np.array([186, 178, 170], dtype=np.uint8)[:, None, None]
    image[:, 500:560, :] = np.array([118, 116, 114], dtype=np.uint8)[:, None, None]
    image[:, 640:820, 760:1040] = np.array([52, 84, 120], dtype=np.uint8)[:, None, None]
    image[:, :160, 1040:] = 0  # NoData corner
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=3, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(757000.0, 3162000.0, 0.05, 0.05), nodata=0,
    ) as dst:
        dst.write(image)


@step("Pipeline runs end to end with the real model (synthetic GeoTIFF)")
def check_pipeline():
    import rasterio

    from backend.ai.pipeline import run_pipeline
    from backend.config import settings

    stages: list[str] = []

    def report(key, status, fraction=None, detail=None):
        if status in ("done", "failed"):
            stages.append(f"{key}:{status}")

    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        tif = folder / "synthetic.tif"
        _synthetic_geotiff(tif)
        summary = run_pipeline(
            tif, folder / "out", model_path=settings.model_path, job_id="SELFCHECK",
            tile_size=settings.tile_size, overlap=settings.tile_overlap,
            normalization=settings.normalization, chunk=512,  # small chunk: exercises seam stitching
            sieve_min_pixels=settings.sieve_min_pixels, report=report,
        )
        with rasterio.open(folder / "out" / "prediction.tif") as src:
            corner = src.read(1)[:160, 1040:]
            if not (corner == 255).all():
                raise RuntimeError("NoData corner was classified instead of being kept as NoData")
        collection = json.loads((folder / "out" / "ai_features.geojson").read_text())
        if len(collection["features"]) != summary["feature_count"]:
            raise RuntimeError("feature count does not match the written file")
        for feature in collection["features"][:50]:
            properties = feature["properties"]
            for key in ("feature_id", "class_name", "area_m2", "perimeter_m", "confidence",
                        "entropy", "review_priority", "geometry_status", "verification_status"):
                if key not in properties:
                    raise RuntimeError(f"feature is missing '{key}'")
        expected = {"LOAD_MODEL", "READ_RASTER", "TILE_IMAGE", "RUN_SEGMENTATION",
                    "CALCULATE_CONFIDENCE", "CALCULATE_ENTROPY", "POLYGONIZE",
                    "REPAIR_GEOMETRY", "GENERATE_GIS", "RUN_QA", "READY_FOR_REVIEW"}
        done = {s.split(":")[0] for s in stages if s.endswith(":done")}
        if expected - done:
            raise RuntimeError(f"stages not completed: {sorted(expected - done)}")
        seg = summary["segmentation"]
    return (
        f"{seg['tiles']} tiles, {summary['feature_count']} features, "
        f"{summary['candidate_parcel_count']} candidate parcels, "
        f"mean confidence {seg['mean_confidence']:.3f}, mean entropy {seg['mean_entropy']:.3f}, "
        f"metric CRS {summary['metric_crs']}, {summary['elapsed_seconds']} s"
    )


@step("Project GIS layers index")
def check_layers():
    from backend.core import runtime

    indexed = runtime.ensure_source(runtime.EXISTING_SOURCE)
    parts = []
    for kind, record in indexed.items():
        if record is None:
            parts.append(f"{kind}: file not present")
        else:
            parts.append(f"{kind}: {record['feature_count']:,} features ({record['crs']})")
    errors = runtime.bootstrap_errors()
    if errors:
        raise RuntimeError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    return "; ".join(parts)


@step("API application imports")
def check_api():
    from backend.main import app

    return f"{len(app.routes)} routes"


def main() -> int:
    print("Cadastra Vision self-check\n")
    packages_ok = check_packages()
    if packages_ok:
        if check_model():
            check_pipeline()
    check_layers()
    check_api()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed else f"{len(failed)} CHECK(S) FAILED: " + "; ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
