"""AI processing jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from backend.ai import pipeline
from backend.config import settings
from backend.core.deps import current_context, service_error, store_dep
from backend.core.store import Store
from backend.services import processing_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(prefix="/api/processing", tags=["AI Processing"])


@router.get("")
def list_jobs(
    limit: int = Query(default=25, ge=1, le=200),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    jobs = store.list_jobs(surveyor_id=context.surveyor_id, limit=limit)
    return {"jobs": [processing_service.public_job(job) for job in jobs]}


@router.get("/stages")
def stages(context: SurveyorContext = Depends(current_context)):
    """The pipeline stages in execution order."""

    return {"stages": [{"key": key, "label": label} for key, label in pipeline.STAGES]}


@router.post("/upload")
def upload(
    file: UploadFile = File(...),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Upload a GeoTIFF. It is validated before a job is created."""

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing.")
    try:
        job = processing_service.create_from_upload(
            context, settings, store, file.filename, file.file
        )
    except processing_service.ProcessingError as exc:
        raise service_error(exc)
    return processing_service.public_job(job)


@router.post("/from-dataset/{dataset_id}")
def from_dataset(
    dataset_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Create a job for imagery that is already in the dataset library."""

    try:
        job = processing_service.create_from_dataset(context, settings, store, dataset_id)
    except processing_service.ProcessingError as exc:
        raise service_error(exc)
    return processing_service.public_job(job)


@router.get("/{job_id}/area-check")
def area_check(
    job_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Whether the job's image overlaps the current area, how far away it is
    if not, and which of the account's areas it does overlap."""

    try:
        job = processing_service.authorise(store.get_job(job_id), context)
        return processing_service.area_check(job, context, settings, store)
    except processing_service.ProcessingError as exc:
        raise service_error(exc)


@router.post("/{job_id}/start")
def start(
    job_id: str,
    confirm_outside_area: bool = Query(
        default=False, description="Process the image although it does not overlap the current area"
    ),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        job = processing_service.start(
            context, settings, store, job_id, confirm_outside_area=confirm_outside_area
        )
    except processing_service.ProcessingError as exc:
        raise service_error(exc)
    return processing_service.public_job(job)


@router.get("/{job_id}")
def job_status(
    job_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        job = processing_service.authorise(store.get_job(job_id), context)
    except processing_service.ProcessingError as exc:
        raise service_error(exc)
    return processing_service.public_job(job)


@router.get("/{job_id}/result")
def job_result(
    job_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return processing_service.result(context, store, job_id)
    except processing_service.ProcessingError as exc:
        raise service_error(exc)
