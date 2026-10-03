"""Processing jobs: upload, validate, queue, run, report.

Job state lives in SQLite (``core.store``), so it survives a restart. The
pipeline runs on one worker thread (a second model run would only compete
for the same CPU / GPU) and writes its real stage and progress to the job
record as it goes. A job that was running when the server stopped is marked
FAILED on the next start, with an explanation, instead of staying
"processing" forever.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO

from backend.ai import pipeline
from backend.ai.model import ModelLoadError
from backend.config import Settings
from backend.core import runtime
from backend.core.store import Store
from backend.services import dataset_service
from backend.services.assignment_service import SurveyorContext

log = logging.getLogger("cadastra.processing")

ALLOWED_EXTENSIONS = {".tif", ".tiff"}

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cadastra-pipeline")
_running: set[str] = set()
_running_lock = threading.Lock()


class ProcessingError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """A job as the API returns it: no server paths."""

    view = {key: value for key, value in job.items() if key != "input_path"}
    view["source"] = runtime.job_source(job["job_id"]) if job["status"] == "COMPLETED" else None
    view["status_label"] = {
        "UPLOADED": "Uploaded · ready to start",
        "QUEUED": "Queued",
        "PROCESSING": "Processing",
        "COMPLETED": "Ready for review",
        "FAILED": "Failed",
    }.get(job["status"], job["status"])
    return view


def _inspect(path: Path) -> dict[str, Any]:
    from backend.ai.raster import RasterError, inspect_raster, validate_for_inference

    try:
        meta = inspect_raster(path)
    except RasterError as exc:
        raise ProcessingError(str(exc)) from exc
    checks = validate_for_inference(meta)
    meta.pop("crs_wkt", None)
    meta["warnings"] = checks["warnings"]
    meta["errors"] = checks["errors"]
    return meta


def _new_job(
    context: SurveyorContext,
    store: Store,
    *,
    input_dataset: str,
    input_path: Path,
    meta: dict[str, Any],
    origin: str,
) -> dict[str, Any]:
    stages = pipeline.initial_stages()
    megabytes = input_path.stat().st_size / (1024 * 1024)
    stages[0].update(status="done", fraction=1.0, detail=f"{input_dataset} · {megabytes:,.1f} MB · {origin}")

    job = store.create_job(
        surveyor_id=context.surveyor_id,
        surveyor_email=context.email,
        assignment_id=context.assignment_id,
        input_dataset=input_dataset,
        input_path=str(input_path),
        stages=stages,
        raster_meta=meta,
        status="UPLOADED",
        stage="UPLOAD",
    )
    store.add_audit(
        actor_id=context.surveyor_id,
        actor_email=context.email,
        action="job.create",
        entity_type="job",
        entity_id=job["job_id"],
        after={"input_dataset": input_dataset, "origin": origin, "size_bytes": input_path.stat().st_size},
    )
    return job


def create_from_upload(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    filename: str,
    stream: BinaryIO,
) -> dict[str, Any]:
    """Save an uploaded GeoTIFF, validate it, and register a job."""

    try:
        name = dataset_service.safe_filename(filename)
    except dataset_service.DatasetError as exc:
        raise ProcessingError(str(exc)) from exc
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ProcessingError("Unsupported file type. Upload a GeoTIFF (.tif or .tiff).")

    folder = settings.upload_dir / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / name
    try:
        written = dataset_service.copy_stream(
            stream, destination, settings.max_upload_mb * 1024 * 1024
        )
    except dataset_service.DatasetError as exc:
        raise ProcessingError(str(exc), exc.status_code) from exc
    if written == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise ProcessingError("The uploaded file is empty.")

    try:
        meta = _inspect(destination)
    except ProcessingError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if meta["errors"]:
        shutil.rmtree(folder, ignore_errors=True)
        raise ProcessingError("This raster cannot be processed. " + " ".join(meta["errors"]), 422)

    return _new_job(
        context, store, input_dataset=name, input_path=destination, meta=meta, origin="uploaded"
    )


def create_from_dataset(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    dataset_id: str,
) -> dict[str, Any]:
    """Register a job for imagery that is already in the dataset library."""

    dataset = dataset_service.find_dataset(dataset_id, settings, store)
    if dataset is None:
        raise ProcessingError("Dataset not found.", 404)
    if dataset["source_type"] not in ("drone", "satellite") or dataset["kind"] != "raster":
        raise ProcessingError("Only drone / ORI or satellite imagery can be processed.")
    path = dataset_service.resolve_path(dataset, settings)
    if path is None:
        raise ProcessingError("The dataset file is no longer available.", 404)

    meta = _inspect(path)
    if meta["errors"]:
        raise ProcessingError("This raster cannot be processed. " + " ".join(meta["errors"]), 422)
    return _new_job(
        context,
        store,
        input_dataset=dataset["name"],
        input_path=path,
        meta=meta,
        origin="from dataset library",
    )


def authorise(job: dict[str, Any] | None, context: SurveyorContext) -> dict[str, Any]:
    """A job is visible to its surveyor and to others on the same assignment."""

    if job is None:
        raise ProcessingError("Job not found.", 404)
    same_surveyor = job.get("surveyor_id") == context.surveyor_id
    same_assignment = bool(context.assignment_id) and job.get("assignment_id") == context.assignment_id
    if not (same_surveyor or same_assignment):
        raise ProcessingError("Job not found.", 404)
    return job


def start(
    context: SurveyorContext, settings: Settings, store: Store, job_id: str
) -> dict[str, Any]:
    job = authorise(store.get_job(job_id), context)
    if job["status"] in ("QUEUED", "PROCESSING", "COMPLETED"):
        return job
    if not Path(job["input_path"]).exists():
        raise ProcessingError("The input raster is no longer on the server. Upload it again.", 409)

    stages = pipeline.initial_stages()
    stages[0] = (job.get("stages") or stages)[0]
    job = store.update_job(
        job_id,
        status="QUEUED",
        stage="LOAD_MODEL",
        progress=0.0,
        stages=stages,
        error=None,
        summary=None,
    )
    store.add_audit(
        actor_id=context.surveyor_id,
        actor_email=context.email,
        action="job.start",
        entity_type="job",
        entity_id=job_id,
    )
    with _running_lock:
        _running.add(job_id)
    _executor.submit(_run, job_id, settings, store)
    return job


class _Reporter:
    """Writes pipeline stage updates to the job record, without flooding
    the database when a stage reports progress many times a second."""

    def __init__(self, job_id: str, store: Store, stages: list[dict[str, Any]]):
        self.job_id = job_id
        self.store = store
        self.stages = stages
        self._index = {stage["key"]: stage for stage in stages}
        self._last_write = 0.0

    def __call__(self, key: str, status: str, fraction: float | None = None, detail: str | None = None) -> None:
        stage = self._index.get(key)
        if stage is None:
            return
        changed = stage["status"] != status
        stage["status"] = status
        if status == "done":
            stage["fraction"] = 1.0
        elif fraction is not None:
            stage["fraction"] = round(max(0.0, min(float(fraction), 1.0)), 4)
        if detail is not None:
            stage["detail"] = detail
        if status == "running" and changed:
            stage["started_at"] = time.time()
        if status in ("done", "failed"):
            stage["finished_at"] = time.time()

        now = time.monotonic()
        if changed or now - self._last_write >= 0.5:
            self._last_write = now
            self.flush(key if status != "done" else None)

    def flush(self, running_key: str | None = None) -> None:
        current = running_key or next(
            (s["key"] for s in self.stages if s["status"] == "running"), None
        )
        fields: dict[str, Any] = {
            "stages": self.stages,
            "progress": pipeline.overall_progress(self.stages),
            "status": "PROCESSING",
        }
        if current:
            fields["stage"] = current
        self.store.update_job(self.job_id, **fields)

    def fail_running(self, message: str) -> None:
        failed = next((s for s in self.stages if s["status"] == "running"), None)
        if failed is None:
            failed = next((s for s in self.stages if s["status"] == "pending"), None)
        if failed is not None:
            failed["status"] = "failed"
            failed["detail"] = message


def _run(job_id: str, settings: Settings, store: Store) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    reporter = _Reporter(job_id, store, job["stages"] or pipeline.initial_stages())
    output_dir = runtime.job_output_dir(job_id)

    try:
        store.update_job(job_id, status="PROCESSING")
        summary = pipeline.run_pipeline(
            job["input_path"],
            output_dir,
            model_path=settings.model_path,
            job_id=job_id,
            source_dataset=job["input_dataset"],
            tile_size=settings.tile_size,
            overlap=settings.tile_overlap,
            normalization=settings.normalization,
            chunk=settings.polygonize_chunk,
            sieve_min_pixels=settings.sieve_min_pixels,
            export_background=settings.export_background,
            sliver_area_m2=settings.sliver_area_m2,
            min_parcel_area_m2=settings.min_candidate_parcel_m2,
            road_access_distance_m=settings.road_access_distance_m,
            report=reporter,
        )
        store.update_job(
            job_id,
            status="COMPLETED",
            stage="READY_FOR_REVIEW",
            progress=100.0,
            stages=reporter.stages,
            summary=summary,
            output_dataset=runtime.job_source(job_id),
            error=None,
        )
        # Index the output so it is immediately available on the map.
        runtime.ensure_source(runtime.job_source(job_id))
        store.add_audit(
            actor_id="system",
            actor_email=None,
            action="job.complete",
            entity_type="job",
            entity_id=job_id,
            after={
                "feature_count": summary.get("feature_count"),
                "candidate_parcel_count": summary.get("candidate_parcel_count"),
            },
        )
    except (pipeline.PipelineError, ModelLoadError) as exc:
        _fail(job_id, store, reporter, str(exc))
    except Exception as exc:  # unexpected: keep the trace in the server log
        log.error("Job %s failed:\n%s", job_id, traceback.format_exc())
        _fail(job_id, store, reporter, f"{type(exc).__name__}: {exc}")
    finally:
        with _running_lock:
            _running.discard(job_id)


def _fail(job_id: str, store: Store, reporter: _Reporter, message: str) -> None:
    reporter.fail_running(message)
    store.update_job(
        job_id,
        status="FAILED",
        stages=reporter.stages,
        progress=pipeline.overall_progress(reporter.stages),
        error=message,
    )
    store.add_audit(
        actor_id="system",
        actor_email=None,
        action="job.fail",
        entity_type="job",
        entity_id=job_id,
        after={"error": message},
    )


def result(context: SurveyorContext, store: Store, job_id: str) -> dict[str, Any]:
    job = authorise(store.get_job(job_id), context)
    if job["status"] != "COMPLETED":
        raise ProcessingError(
            f"The job has no result yet (status: {job['status']}).", 409
        )
    source = runtime.job_source(job_id)
    indexed = runtime.ensure_source(source)
    layers = runtime.get_layers()
    return {
        "job": public_job(job),
        "source": source,
        "summary": job.get("summary"),
        "layers": {
            kind: layers.stats(source, kind) if record else None
            for kind, record in indexed.items()
        },
        "label": "AI GENERATED / PRELIMINARY",
    }


def recover_interrupted(store: Store) -> int:
    """Called at start-up: jobs that were mid-run cannot be resumed."""

    return store.fail_interrupted_jobs()
