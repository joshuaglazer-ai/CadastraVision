"""Compatibility entry points for the original AI service.

``load_ai_model`` and ``process_geotiff`` keep their names and return
shapes; the work is done by ``backend.ai.model`` and ``backend.ai.pipeline``.
"""

from __future__ import annotations

from pathlib import Path

from backend.ai.model import get_model
from backend.ai.pipeline import run_pipeline
from backend.config import settings

MODEL_PATH = settings.model_path


def load_ai_model():
    """Load the trained six-class U-Net / ResNet34 model.

    Raises ``ModelLoadError`` with a diagnosis if the checkpoint is missing
    or does not match the architecture. There is no fallback model.
    """

    return get_model(settings.model_path)


def process_geotiff(input_path, output_dir, tile_size=None, min_polygon_area=0.0):
    """Run the complete GeoAI pipeline on an RGB GeoTIFF.

    ``min_polygon_area`` (square metres) is used as the sliver threshold.
    """

    output_dir = Path(output_dir)
    summary = run_pipeline(
        input_path,
        output_dir,
        model_path=settings.model_path,
        job_id=output_dir.name or "LOCAL",
        tile_size=tile_size or settings.tile_size,
        overlap=settings.tile_overlap,
        normalization=settings.normalization,
        chunk=settings.polygonize_chunk,
        sieve_min_pixels=settings.sieve_min_pixels,
        export_background=settings.export_background,
        sliver_area_m2=min_polygon_area if min_polygon_area > 0 else settings.sliver_area_m2,
        min_parcel_area_m2=settings.min_candidate_parcel_m2,
        road_access_distance_m=settings.road_access_distance_m,
    )
    return {
        "input": str(input_path),
        "prediction_raster": str(output_dir / "prediction.tif"),
        "confidence_raster": str(output_dir / "confidence.tif"),
        "entropy_raster": str(output_dir / "entropy.tif"),
        "geojson": str(output_dir / "ai_features.geojson"),
        "validated_geojson": str(output_dir / "ai_features.geojson"),
        "candidate_parcels": str(output_dir / "candidate_parcels.geojson"),
        "topology_report": str(output_dir / "topology_report.json"),
        "feature_count": summary["feature_count"],
        "topology": summary["topology"],
        "device": summary["device"],
        "summary": summary,
    }
