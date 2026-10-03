"""End-to-end GeoAI pipeline: GeoTIFF in, reviewed-ready GIS features out.

    GeoTIFF -> validate -> 512 px windowed inference (U-Net / ResNet34)
            -> class + confidence + entropy rasters
            -> chunked polygonisation -> geometry repair
            -> metric attributes -> QA / review priority -> GeoJSON

Memory use is bounded by one tile during inference and one chunk during
polygonisation, whatever the size of the input mosaic. Every stage reports
what it actually did through the ``report`` callback; nothing here sleeps or
invents progress.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from backend.ai import qa
from backend.ai.classes import CLASS_NAMES, CLASS_PREFIX, NODATA_CLASS, NUM_CLASSES
from backend.ai.inference import predict_probabilities, summarise_probabilities
from backend.ai.model import MODEL_LABEL, get_model
from backend.ai.raster import RasterError, inspect_raster, validate_for_inference

# (key, label) in execution order. UPLOAD is completed by the API before the
# pipeline starts; it is listed so the job shows the whole chain.
STAGES: list[tuple[str, str]] = [
    ("UPLOAD", "Upload"),
    ("LOAD_MODEL", "Load model"),
    ("READ_RASTER", "Read raster"),
    ("TILE_IMAGE", "Tile image"),
    ("RUN_SEGMENTATION", "Run segmentation"),
    ("CALCULATE_CONFIDENCE", "Calculate confidence"),
    ("CALCULATE_ENTROPY", "Calculate entropy"),
    ("POLYGONIZE", "Polygonize"),
    ("REPAIR_GEOMETRY", "Repair geometry"),
    ("GENERATE_GIS", "Generate GIS"),
    ("RUN_QA", "Run QA"),
    ("READY_FOR_REVIEW", "Ready for review"),
]

# Share of the overall progress bar given to each stage. Segmentation
# dominates wall-clock time, so it gets most of the bar.
STAGE_WEIGHT = {
    "UPLOAD": 0.0,
    "LOAD_MODEL": 0.04,
    "READ_RASTER": 0.02,
    "TILE_IMAGE": 0.01,
    "RUN_SEGMENTATION": 0.62,
    "CALCULATE_CONFIDENCE": 0.0,
    "CALCULATE_ENTROPY": 0.0,
    "POLYGONIZE": 0.14,
    "REPAIR_GEOMETRY": 0.05,
    "GENERATE_GIS": 0.08,
    "RUN_QA": 0.04,
    "READY_FOR_REVIEW": 0.0,
}

CONFIDENCE_NODATA = -1.0
ENTROPY_NODATA = -1.0

Report = Callable[..., None]


class PipelineError(RuntimeError):
    """Processing cannot continue; the message is shown to the surveyor."""


def initial_stages() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "status": "pending", "fraction": 0.0, "detail": None}
        for key, label in STAGES
    ]


def overall_progress(stages: list[dict[str, Any]]) -> float:
    total = 0.0
    for stage in stages:
        weight = STAGE_WEIGHT.get(stage["key"], 0.0)
        if stage["status"] == "done":
            total += weight
        elif stage["status"] == "running":
            total += weight * float(stage.get("fraction") or 0.0)
    return round(min(total, 1.0) * 100.0, 1)


def plan_tiles(width: int, height: int, tile: int, overlap: int) -> list[tuple[tuple, tuple]]:
    """Tiles as ``(core, read)`` windows, each ``(col, row, width, height)``.

    The model sees ``read`` (the core plus a margin of ``overlap // 2``
    pixels of context, clipped to the raster); only ``core`` is written, so
    tile borders do not show in the output.
    """

    if tile < 32:
        raise ValueError("tile size must be at least 32 pixels")
    margin = max(int(overlap) // 2, 0)
    tiles = []
    for row in range(0, height, tile):
        for col in range(0, width, tile):
            core_w = min(tile, width - col)
            core_h = min(tile, height - row)
            read_col = max(col - margin, 0)
            read_row = max(row - margin, 0)
            read_w = min(col + core_w + margin, width) - read_col
            read_h = min(row + core_h + margin, height) - read_row
            tiles.append(((col, row, core_w, core_h), (read_col, read_row, read_w, read_h)))
    return tiles


def _noop_report(*_args, **_kwargs) -> None:
    return None


def geometry_to_geojson(geometry, decimals: int = 8) -> dict[str, Any]:
    """Shapely polygon -> GeoJSON dict with rounded coordinates."""

    def ring(coords) -> list:
        return np.round(np.asarray(coords, dtype=np.float64)[:, :2], decimals).tolist()

    def polygon(poly) -> list:
        return [ring(poly.exterior.coords), *[ring(interior.coords) for interior in poly.interiors]]

    if geometry.geom_type == "Polygon":
        return {"type": "Polygon", "coordinates": polygon(geometry)}
    return {"type": "MultiPolygon", "coordinates": [polygon(part) for part in geometry.geoms]}


def write_feature_collection(path: Path, name: str, metadata: dict[str, Any], features) -> int:
    """Stream features to a GeoJSON file. Returns the number written."""

    count = 0
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"type":"FeatureCollection","name":')
        handle.write(json.dumps(name))
        handle.write(',"crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}}')
        handle.write(',"metadata":')
        handle.write(json.dumps(metadata, ensure_ascii=False, default=str))
        handle.write(',"features":[\n')
        for feature in features:
            if count:
                handle.write(",\n")
            handle.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":"), default=str))
            count += 1
        handle.write("\n]}\n")
    return count


# --------------------------------------------------------------------- stage 1
def segment_raster(
    input_path: Path,
    output_dir: Path,
    model,
    device,
    meta: dict[str, Any],
    *,
    tile_size: int,
    overlap: int,
    normalization: str,
    report: Report,
) -> dict[str, Any]:
    """Windowed inference. Writes the class, confidence and entropy rasters."""

    import rasterio
    from rasterio.windows import Window

    tiles = plan_tiles(meta["width"], meta["height"], tile_size, overlap)
    report(
        "TILE_IMAGE",
        "done",
        detail=f"{len(tiles):,} tiles of {tile_size} px with {overlap // 2} px context margin",
    )

    prediction_path = output_dir / "prediction.tif"
    confidence_path = output_dir / "confidence.tif"
    entropy_path = output_dir / "entropy.tif"

    black_is_nodata = meta.get("nodata") is None and not meta.get("alpha_band")
    rgb_bands = meta["rgb_bands"]

    class_pixels = np.zeros(NUM_CLASSES, dtype=np.int64)
    confidence_histogram = np.zeros(10, dtype=np.int64)
    confidence_sum = 0.0
    entropy_sum = 0.0
    valid_pixels = 0
    skipped_tiles = 0

    report("RUN_SEGMENTATION", "running", fraction=0.0, detail=f"0 / {len(tiles):,} tiles")

    with rasterio.open(input_path) as src:
        profile = {
            "driver": "GTiff",
            "width": src.width,
            "height": src.height,
            "count": 1,
            "crs": src.crs,
            "transform": src.transform,
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
            "compress": "deflate",
            "BIGTIFF": "IF_SAFER",
        }
        with rasterio.open(
            prediction_path, "w", dtype="uint8", nodata=NODATA_CLASS, **profile
        ) as pred_dst, rasterio.open(
            confidence_path, "w", dtype="float32", nodata=CONFIDENCE_NODATA, **profile
        ) as conf_dst, rasterio.open(
            entropy_path, "w", dtype="float32", nodata=ENTROPY_NODATA, **profile
        ) as ent_dst:
            for index, (core, read) in enumerate(tiles, start=1):
                col, row, core_w, core_h = core
                read_col, read_row, read_w, read_h = read
                read_window = Window(read_col, read_row, read_w, read_h)
                core_window = Window(col, row, core_w, core_h)
                off_x, off_y = col - read_col, row - read_row

                rgb = src.read(rgb_bands, window=read_window)  # [3, h, w]
                valid = src.dataset_mask(window=read_window) > 0
                if black_is_nodata:
                    valid &= rgb.any(axis=0)
                valid_core = valid[off_y : off_y + core_h, off_x : off_x + core_w]

                if not valid_core.any():
                    skipped_tiles += 1
                    pred_dst.write(
                        np.full((core_h, core_w), NODATA_CLASS, dtype=np.uint8), 1, window=core_window
                    )
                    empty = np.full((core_h, core_w), CONFIDENCE_NODATA, dtype=np.float32)
                    conf_dst.write(empty, 1, window=core_window)
                    ent_dst.write(empty, 1, window=core_window)
                else:
                    image = np.transpose(rgb, (1, 2, 0))
                    probabilities = predict_probabilities(model, image, device, normalization)
                    probabilities = probabilities[
                        :, off_y : off_y + core_h, off_x : off_x + core_w
                    ]
                    prediction, confidence, entropy = summarise_probabilities(probabilities)

                    invalid = ~valid_core
                    if invalid.any():
                        prediction[invalid] = NODATA_CLASS
                        confidence[invalid] = CONFIDENCE_NODATA
                        entropy[invalid] = ENTROPY_NODATA

                    pred_dst.write(prediction, 1, window=core_window)
                    conf_dst.write(confidence, 1, window=core_window)
                    ent_dst.write(entropy, 1, window=core_window)

                    valid_prediction = prediction[valid_core]
                    valid_confidence = confidence[valid_core]
                    class_pixels += np.bincount(valid_prediction, minlength=NUM_CLASSES)[:NUM_CLASSES]
                    confidence_histogram += np.histogram(
                        valid_confidence, bins=10, range=(0.0, 1.0)
                    )[0]
                    confidence_sum += float(valid_confidence.sum(dtype=np.float64))
                    entropy_sum += float(entropy[valid_core].sum(dtype=np.float64))
                    valid_pixels += int(valid_core.sum())

                if index == len(tiles) or index % max(1, len(tiles) // 200) == 0:
                    report(
                        "RUN_SEGMENTATION",
                        "running",
                        fraction=index / len(tiles),
                        detail=f"{index:,} / {len(tiles):,} tiles",
                    )

    if valid_pixels == 0:
        raise PipelineError(
            "The raster contains no valid pixels (everything is NoData), so there is nothing to segment."
        )

    mean_confidence = confidence_sum / valid_pixels
    mean_entropy = entropy_sum / valid_pixels
    pixel_area = None
    if meta.get("resolution_m"):
        pixel_area = meta["resolution_m"][0] * meta["resolution_m"][1]

    report(
        "RUN_SEGMENTATION",
        "done",
        detail=f"{len(tiles):,} tiles, {valid_pixels:,} valid pixels, {skipped_tiles:,} empty tiles skipped",
    )
    report(
        "CALCULATE_CONFIDENCE",
        "done",
        detail=f"Mean confidence {mean_confidence:.3f} (max softmax probability per pixel)",
    )
    report(
        "CALCULATE_ENTROPY",
        "done",
        detail=f"Mean entropy {mean_entropy:.3f} nats of a possible {math.log(NUM_CLASSES):.3f}",
    )

    return {
        "prediction_raster": prediction_path,
        "confidence_raster": confidence_path,
        "entropy_raster": entropy_path,
        "tiles": len(tiles),
        "skipped_tiles": skipped_tiles,
        "valid_pixels": valid_pixels,
        "total_pixels": int(meta["width"]) * int(meta["height"]),
        "mean_confidence": round(mean_confidence, 6),
        "mean_entropy": round(mean_entropy, 6),
        "confidence_histogram": {
            "bin_edges": [round(edge, 1) for edge in np.linspace(0.0, 1.0, 11).tolist()],
            "pixels": [int(v) for v in confidence_histogram],
        },
        "class_pixels": {
            CLASS_NAMES[c]: {
                "pixels": int(class_pixels[c]),
                "share": round(float(class_pixels[c]) / valid_pixels, 6),
                "approx_area_m2": (
                    round(float(class_pixels[c]) * pixel_area, 3) if pixel_area else None
                ),
            }
            for c in range(NUM_CLASSES)
        },
        "nodata_rule": (
            "pure black RGB treated as NoData" if black_is_nodata else "dataset mask (NoData value / alpha band)"
        ),
    }


# ------------------------------------------------------------------ stage 2-4
def build_features(
    segmentation: dict[str, Any],
    *,
    job_id: str,
    source_dataset: str,
    generated_at: str,
    chunk: int,
    sieve_min_pixels: int,
    export_background: bool,
    sliver_area_m2: float,
    min_parcel_area_m2: float,
    road_access_distance_m: float,
    report: Report,
) -> dict[str, Any]:
    """Polygonise, repair, measure and assess. Returns frames and reports."""

    import geopandas as gpd

    from backend.ai.parcels import derive_candidate_parcels
    from backend.ai.polygonize import polygonize_rasters
    from backend.ai.topology import run_topology_validation
    from backend.gis.metric import choose_metric_crs, crs_label, metric_properties

    # ---- polygonise ----------------------------------------------------
    report("POLYGONIZE", "running", fraction=0.0, detail="Vectorising class regions")
    regions, info = polygonize_rasters(
        segmentation["prediction_raster"],
        segmentation["confidence_raster"],
        segmentation["entropy_raster"],
        chunk=chunk,
        sieve_min_pixels=sieve_min_pixels,
        export_background=export_background,
        progress=lambda done, total: report(
            "POLYGONIZE", "running", fraction=done / total, detail=f"{done} / {total} chunks"
        ),
    )
    source_crs = info["crs"]
    report(
        "POLYGONIZE",
        "done",
        detail=f"{len(regions):,} regions from {info['chunks']} chunk(s); {info['seam_merges']} stitched across chunk borders",
    )
    if not regions:
        raise PipelineError(
            "Segmentation produced only background, so no features were generated. "
            "Check that the imagery is comparable to drone orthoimagery of a settlement."
        )

    frame = gpd.GeoDataFrame(
        {
            "class_id": [region.class_id for region in regions],
            "class_name": [CLASS_NAMES.get(region.class_id, "Unknown") for region in regions],
            "pixel_count": [region.pixels for region in regions],
            "confidence": [region.mean_confidence for region in regions],
            "entropy": [region.mean_entropy for region in regions],
        },
        geometry=[region.geometry for region in regions],
        crs=source_crs,
    )
    del regions

    # ---- measure in a metric CRS ---------------------------------------
    report("REPAIR_GEOMETRY", "running", fraction=0.0, detail="Validating geometry")
    metric_crs = choose_metric_crs(source_crs, tuple(frame.total_bounds))
    metric = frame.to_crs(metric_crs) if metric_crs != frame.crs else frame

    # Repair first so that measurements describe the geometry that is kept.
    metric["area_m2"] = metric.geometry.area
    metric, topology = run_topology_validation(
        metric, sliver_area_threshold=sliver_area_m2, area_column="area_m2"
    )
    report(
        "REPAIR_GEOMETRY",
        "done",
        detail=(
            f"{topology['repaired']} repaired, {topology['remaining_invalid']} still invalid, "
            f"{topology['empty_geometries']} empty (reported, removed), "
            f"{topology['overlap_pairs']} overlapping pairs"
        ),
    )

    # ---- attributes -----------------------------------------------------
    report("GENERATE_GIS", "running", fraction=0.0, detail="Measuring features")
    measures = [metric_properties(geometry) for geometry in metric.geometry]
    for column in (
        "area_m2",
        "perimeter_m",
        "length_m",
        "width_m",
        "compactness",
        "ring_count",
        "hole_count",
        "vertex_count",
    ):
        metric[column] = [measure[column] for measure in measures]

    metric = metric.sort_values(["class_id", "area_m2"], ascending=[True, False]).reset_index(drop=True)
    counters: dict[int, int] = {}
    feature_ids = []
    for class_id in metric["class_id"]:
        counters[class_id] = counters.get(class_id, 0) + 1
        feature_ids.append(f"{CLASS_PREFIX.get(int(class_id), 'FTR')}-{counters[class_id]:06d}")
    metric["feature_id"] = feature_ids
    metric["source_dataset"] = source_dataset
    metric["processing_job_id"] = job_id
    metric["generated_at"] = generated_at
    metric["metric_crs"] = crs_label(metric_crs)

    report("GENERATE_GIS", "running", fraction=0.5, detail="Deriving candidate parcels")
    parcels = derive_candidate_parcels(
        metric, road_access_distance_m=road_access_distance_m
    )
    report(
        "GENERATE_GIS",
        "done",
        detail=f"{len(metric):,} features and {len(parcels):,} candidate parcels, measured in {crs_label(metric_crs)}",
    )

    # ---- QA -------------------------------------------------------------
    report("RUN_QA", "running", fraction=0.0, detail="Assessing review priority")

    def road_access_of(row) -> bool | None:
        value = row.get("road_access_candidate")
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return bool(value)

    def assess(row, layer: str) -> dict[str, Any]:
        return qa.assess(
            layer=layer,
            area_m2=row["area_m2"],
            compactness=row["compactness"],
            rings=int(row["ring_count"]),
            confidence=row["confidence"],
            entropy=row["entropy"],
            geometry_status=row["geometry_status"],
            has_overlap=bool(row["has_overlap"]),
            road_access=road_access_of(row) if layer == "parcels" else None,
            sliver_area_m2=sliver_area_m2,
            min_parcel_area_m2=min_parcel_area_m2,
            road_access_distance_m=road_access_distance_m,
        )

    for target, layer in ((metric, "landcover"), (parcels, "parcels")):
        assessments = [assess(row, layer) for _, row in target.iterrows()]
        target["review_priority"] = [a["priority"] for a in assessments]
        target["review_reasons"] = [a["reasons"] for a in assessments]
        target["qa_flags"] = [a["flags"] for a in assessments]
        target["verification_status"] = [qa.default_status(a["priority"]) for a in assessments]

    priority_counts = {
        level: int((metric["review_priority"] == level).sum()) for level in qa.PRIORITIES
    }
    report(
        "RUN_QA",
        "done",
        detail=(
            f"High {priority_counts['High']:,} · Medium {priority_counts['Medium']:,} · "
            f"Low {priority_counts['Low']:,}"
        ),
    )

    return {
        "features_metric": metric,
        "parcels_metric": parcels,
        "metric_crs": metric_crs,
        "metric_crs_label": crs_label(metric_crs),
        "source_crs_label": crs_label(source_crs),
        "topology": topology,
        "polygonize": {k: v for k, v in info.items() if k not in ("crs", "transform")},
        "priority_counts": priority_counts,
    }


FEATURE_COLUMNS = [
    "feature_id",
    "class_id",
    "class_name",
    "area_m2",
    "perimeter_m",
    "length_m",
    "width_m",
    "compactness",
    "ring_count",
    "hole_count",
    "vertex_count",
    "pixel_count",
    "confidence",
    "entropy",
    "review_priority",
    "review_reasons",
    "qa_flags",
    "geometry_status",
    "is_sliver",
    "has_overlap",
    "topology_status",
    "verification_status",
    "source_dataset",
    "processing_job_id",
    "generated_at",
    "metric_crs",
]

PARCEL_COLUMNS = [
    "parcel_id",
    "source_feature_id",
    *[c for c in FEATURE_COLUMNS if c != "feature_id"],
    "nearest_road_distance_m",
    "road_access_candidate",
    "boundary_status",
]


def _clean(value: Any) -> Any:
    """Make a pandas / numpy scalar JSON-friendly."""

    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return round(number, 6) if math.isfinite(number) else None
    return value


def _features_for_export(frame, columns: list[str], extra: dict[str, Any]):
    """Yield GeoJSON features (lon/lat) from a metric-CRS frame."""

    wgs84 = frame.to_crs("EPSG:4326")
    present = [column for column in columns if column in wgs84.columns]
    for _, row in wgs84.iterrows():
        properties = {column: _clean(row[column]) for column in present}
        properties.update(extra)
        yield {
            "type": "Feature",
            "properties": properties,
            "geometry": geometry_to_geojson(row.geometry),
        }


# ------------------------------------------------------------------ entry point
def run_pipeline(
    input_path: Path | str,
    output_dir: Path | str,
    *,
    model_path: Path | str,
    job_id: str = "LOCAL",
    source_dataset: str | None = None,
    tile_size: int = 512,
    overlap: int = 64,
    normalization: str = "scale_255",
    chunk: int = 4096,
    sieve_min_pixels: int = 8,
    export_background: bool = False,
    sliver_area_m2: float = 1.0,
    min_parcel_area_m2: float = 25.0,
    road_access_distance_m: float = 5.0,
    report: Report | None = None,
    model_loader: Callable[[Path], tuple[Any, Any]] | None = None,
) -> dict[str, Any]:
    """Run the whole pipeline. Returns a JSON-serialisable summary."""

    report = report or _noop_report
    started = time.time()
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_dataset = source_dataset or input_path.name
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not input_path.exists():
        raise PipelineError(f"Input raster not found: {input_path.name}")

    # ---- model ----------------------------------------------------------
    report("LOAD_MODEL", "running", fraction=0.0, detail="Loading U-Net / ResNet34 checkpoint")
    loader = model_loader or get_model
    model, device = loader(Path(model_path))
    report("LOAD_MODEL", "done", detail=f"{MODEL_LABEL} on {device}")

    # ---- raster ---------------------------------------------------------
    report("READ_RASTER", "running", fraction=0.0, detail="Reading georeferencing and bands")
    try:
        meta = inspect_raster(input_path)
    except RasterError as exc:
        raise PipelineError(str(exc)) from exc
    checks = validate_for_inference(meta)
    if checks["errors"]:
        raise PipelineError(" ".join(checks["errors"]))
    report(
        "READ_RASTER",
        "done",
        detail=(
            f"{meta['width']:,} x {meta['height']:,} px, {meta['band_count']} bands, {meta['crs']}"
            + (f", {max(meta['resolution_m']) * 100:.1f} cm/px" if meta.get("resolution_m") else "")
        ),
    )

    # ---- segmentation ---------------------------------------------------
    report("TILE_IMAGE", "running", fraction=0.0, detail="Planning tiles")
    segmentation = segment_raster(
        input_path,
        output_dir,
        model,
        device,
        meta,
        tile_size=tile_size,
        overlap=overlap,
        normalization=normalization,
        report=report,
    )

    # ---- vector products ------------------------------------------------
    built = build_features(
        segmentation,
        job_id=job_id,
        source_dataset=source_dataset,
        generated_at=generated_at,
        chunk=chunk,
        sieve_min_pixels=sieve_min_pixels,
        export_background=export_background,
        sliver_area_m2=sliver_area_m2,
        min_parcel_area_m2=min_parcel_area_m2,
        road_access_distance_m=road_access_distance_m,
        report=report,
    )

    disclaimer = (
        "AI-assisted spatial features require surveyor verification and are "
        "not legal cadastral ownership records."
    )
    metadata = {
        "project": "Cadastra Vision",
        "status": "AI GENERATED / PRELIMINARY",
        "disclaimer": disclaimer,
        "processing_job_id": job_id,
        "source_dataset": source_dataset,
        "generated_at": generated_at,
        "model": MODEL_LABEL,
        "normalization": normalization,
        "tile_size": tile_size,
        "tile_overlap": overlap,
        "source_crs": built["source_crs_label"],
        "metric_crs": built["metric_crs_label"],
    }
    label = {"label": "AI GENERATED / PRELIMINARY"}

    features_path = output_dir / "ai_features.geojson"
    parcels_path = output_dir / "candidate_parcels.geojson"
    feature_count = write_feature_collection(
        features_path,
        f"ai_features_{job_id}",
        metadata,
        _features_for_export(built["features_metric"], FEATURE_COLUMNS, label),
    )
    parcel_count = write_feature_collection(
        parcels_path,
        f"candidate_parcels_{job_id}",
        {**metadata, "status": "CANDIDATE PARCEL / AI GENERATED / PRELIMINARY"},
        _features_for_export(
            built["parcels_metric"], PARCEL_COLUMNS, {"label": "CANDIDATE PARCEL / AI GENERATED / PRELIMINARY"}
        ),
    )

    frame = built["features_metric"]
    class_summary = {}
    for class_id, name in CLASS_NAMES.items():
        subset = frame[frame["class_id"] == class_id]
        if len(subset) == 0:
            continue
        class_summary[name] = {
            "features": int(len(subset)),
            "area_m2": round(float(subset["area_m2"].sum()), 3),
            "mean_confidence": round(float(subset["confidence"].mean()), 4),
            "mean_entropy": round(float(subset["entropy"].mean()), 4),
        }

    qa_report = {
        "topology": built["topology"],
        "polygonize": built["polygonize"],
        "priority_counts": built["priority_counts"],
        "note": "Model-derived uncertainty ranks features for review; it is not proof that a feature is wrong.",
    }
    qa_path = output_dir / "qa_report.json"
    qa_path.write_text(json.dumps(qa_report, indent=2, default=str), encoding="utf-8")
    # Legacy file name, kept for callers of the original pipeline.
    (output_dir / "topology_report.json").write_text(
        json.dumps(built["topology"], indent=2, default=str), encoding="utf-8"
    )

    summary: dict[str, Any] = {
        "job_id": job_id,
        "input": input_path.name,
        "source_dataset": source_dataset,
        "generated_at": generated_at,
        "device": str(device),
        "model": MODEL_LABEL,
        "normalization": normalization,
        "tile_size": tile_size,
        "tile_overlap": overlap,
        "raster": {k: v for k, v in meta.items() if k != "crs_wkt"},
        "warnings": checks["warnings"],
        "segmentation": {
            k: v
            for k, v in segmentation.items()
            if k not in ("prediction_raster", "confidence_raster", "entropy_raster")
        },
        "feature_count": feature_count,
        "candidate_parcel_count": parcel_count,
        "classes": class_summary,
        "priority_counts": built["priority_counts"],
        "topology": built["topology"],
        "metric_crs": built["metric_crs_label"],
        "source_crs": built["source_crs_label"],
        "outputs": {
            "prediction_raster": "prediction.tif",
            "confidence_raster": "confidence.tif",
            "entropy_raster": "entropy.tif",
            "features": features_path.name,
            "candidate_parcels": parcels_path.name,
            "qa_report": qa_path.name,
        },
        "elapsed_seconds": round(time.time() - started, 1),
        "status": "AI GENERATED / PRELIMINARY",
        "disclaimer": disclaimer,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    report(
        "READY_FOR_REVIEW",
        "done",
        detail=f"{feature_count:,} AI-generated features await surveyor verification",
    )
    return summary


def process_image(input_path, output_dir, model_path=None, **kwargs):
    """Alias kept from the original module."""

    if model_path is None:
        from backend.config import settings

        model_path = settings.model_path
    return run_pipeline(input_path, output_dir, model_path=model_path, **kwargs)
