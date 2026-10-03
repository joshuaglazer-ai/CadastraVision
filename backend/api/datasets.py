"""Dataset discovery, upload and reference overlays."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.config import settings
from backend.core import runtime
from backend.core.deps import current_context, service_error, source_param, store_dep
from backend.core.store import Store
from backend.gis.geometry import POLYGON_TYPES, analyse_geometry
from backend.services import dataset_service, parcel_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(prefix="/api/datasets", tags=["Datasets"])


@router.get("")
def list_datasets(
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    result = dataset_service.discover(settings, store, context.surveyor_id)
    result["assignment_id"] = context.assignment_id
    return result


@router.post("/upload")
def upload_dataset(
    source_type: str = Form(...),
    file: UploadFile = File(...),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Add a file to one of the source categories (DSM, DTM, GNSS, ...)."""

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing.")
    try:
        path = dataset_service.save_upload(source_type, file.filename, file.file, settings)
    except dataset_service.DatasetError as exc:
        raise service_error(exc)

    store.add_audit(
        actor_id=context.surveyor_id,
        actor_email=context.email,
        action="dataset.upload",
        entity_type="dataset",
        entity_id=path.name,
        after={"source_type": source_type, "size_bytes": path.stat().st_size},
    )
    for category in dataset_service.discover(settings, store)["categories"]:
        if category["key"] != source_type:
            continue
        for dataset in category["datasets"]:
            if dataset["name"] == path.name:
                return dataset
    return {"name": path.name, "source_type": source_type, "status": "AVAILABLE"}


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    dataset = dataset_service.find_dataset(dataset_id, settings, store)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return dataset


@router.get("/{dataset_id}/overlay")
def dataset_overlay(
    dataset_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """A reference vector dataset as GeoJSON for the map."""

    dataset = dataset_service.find_dataset(dataset_id, settings, store)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    try:
        return dataset_service.reference_overlay(dataset, settings)
    except dataset_service.DatasetError as exc:
        raise service_error(exc)


@router.get("/{dataset_id}/features/{index}")
def dataset_feature(
    dataset_id: str,
    index: int,
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """One record of an existing GIS layer, with the AI features overlaid on it.

    This is parcel reasoning when existing parcel GIS is available: the parcel
    geometry and attributes come from the dataset as supplied, and the AI
    land cover of ``source`` is intersected with it.
    """

    dataset = dataset_service.find_dataset(dataset_id, settings, store)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    try:
        feature = dataset_service.reference_feature(dataset, settings, index)
    except dataset_service.DatasetError as exc:
        raise service_error(exc)

    geometry = feature["geometry"]
    result = {
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "name": dataset["name"],
            "source_type": dataset["source_type"],
            "source_label": dataset["source_label"],
        },
        "index": index,
        "record_label": "EXISTING GIS RECORD",
        "note": (
            "Geometry and attributes are shown as supplied in the dataset. "
            "Cadastra Vision has not verified them."
        ),
        "properties": feature["properties"],
        "geometry": geometry,
        "bbox": None,
        "metrics": None,
        "reasoning": {
            "scenario": "existing_gis",
            "available": False,
            "message": "Parcel reasoning needs a polygon record.",
        },
        "source": source,
    }
    if geometry.get("type") not in POLYGON_TYPES:
        return result

    analysis = analyse_geometry(geometry)
    if analysis["fatal"] or analysis["metrics"] is None:
        result["reasoning"]["message"] = (
            "This record's geometry is not usable: "
            + ("; ".join(analysis["problems"]) or "invalid polygon")
            + "."
        )
        return result

    metrics = analysis["metrics"]
    result["bbox"] = list(analysis["bbox"])
    result["metrics"] = {
        "area_m2": round(metrics["area_m2"], 3),
        "perimeter_m": round(metrics["perimeter_m"], 3),
        "metric_crs": metrics["metric_crs"],
        "geometry_problems": list(analysis["problems"]),
    }
    runtime.ensure_source(source)
    result["reasoning"] = parcel_service.reason(
        {"id": "", "properties": {}, "geometry": geometry, "bbox": result["bbox"]},
        source=source,
        settings=settings,
        scenario="existing_gis",
    )
    return result
