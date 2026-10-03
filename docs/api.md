# API

Base URL: `http://localhost:8000`. Interactive documentation: `/docs`.

Every endpoint except `/health` and `/` requires `Authorization: Bearer <Supabase access
token>`. The surveyor and assignment are derived from the token. No endpoint accepts a
surveyor id from the client.

Errors are JSON: `{"detail": "message a surveyor can act on"}` with a 4xx or 5xx status.
`401` means sign in again; `503` on authentication means the server cannot reach or is
not configured for the identity provider.

Most endpoints take an optional `source`: `existing` (default) or `job:<JOB-ID>`. An
unknown source is a `404`.

## System

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/health` | `{status, service, version}`. Public. |
| GET | `/` | Name, tagline and principle. Public. |
| GET | `/api/system/status` | Model status (checkpoint present, runtime available, normalisation), pipeline settings, indexed layers, indexing progress and errors, available sources |

## Surveyor and assignment

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/surveyors/me` | `{surveyor, assignment, notes}`; `assignment` is null when none is registered |
| GET | `/api/assignments/current` | The assignment, or `404` with the reason |
| GET | `/api/assignments/current/boundary` | Assigned boundary as a GeoJSON FeatureCollection |

`assignment` carries `assignment_id`, `assignment_status`, `district`, `taluk`,
`village`, `area_ha` (measured), `declared_area_ha`, `bbox`, `is_demo` and `label`.

## Datasets

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/datasets` | Ten source categories, each with `status`, `datasets` and an `empty_message` when nothing is present |
| POST | `/api/datasets/upload` | Multipart: `source_type`, `file`. Extension must suit the category. |
| GET | `/api/datasets/{dataset_id}` | One dataset |
| GET | `/api/datasets/{dataset_id}/overlay` | A reference GeoJSON layer in longitude/latitude; each feature's `id` is its position in the file |
| GET | `/api/datasets/{dataset_id}/features/{index}` | One record of an existing GIS layer with the AI features of `source` overlaid: `properties`, `metrics`, `reasoning` |

Categories: `drone`, `satellite`, `dsm`, `dtm`, `ai_layers`, `land_records`, `gis`,
`survey_of_india`, `documents`, `gnss`. Dataset status is one of `AVAILABLE`,
`PROCESSING`, `PROCESSED`, `NOT AVAILABLE`, `REQUIRES REVIEW`. Each dataset reports its
name, type, source, CRS, resolution, extent, date and size where they can be read from
the file.

## Map

| Method | Path | Parameters |
| --- | --- | --- |
| GET | `/api/map/layers` | Layer catalogue: availability, counts, and the reason a layer is unavailable (for example "DSM dataset unavailable") |
| GET | `/api/map/assigned-area` | Assigned boundary plus `available` and `notes` |
| GET | `/api/map/parcels` | `bbox`, `zoom`, `limit` |
| GET | `/api/map/features` | `classes` (comma separated), `bbox`, `zoom`, `limit` |
| GET | `/api/map/gnss` | GNSS points and recorded ground-truth positions |
| GET | `/api/map/features/{uid}` | `geometry` = `overview` or `detail`, `reasoning` = true or false |
| GET | `/api/parcels/{parcel_id}` | A candidate parcel with reasoning |

`bbox` is `min_lon,min_lat,max_lon,max_lat`. The feature endpoints return a GeoJSON
FeatureCollection with `total_matching`, `returned`, `truncated` and the level of detail
used. Geometry is generalised for the zoom; the largest features are returned first.

A feature's `properties` include `uid`, `layer`, `class_name`, `area_m2`, `perimeter_m`,
`confidence`, `entropy` (null when not recorded), `review_priority`, `review_reasons`,
`qa_flags`, `geometry_status` and `verification_status`. The detail endpoint adds
`reviews`, `audit`, `uncertainty`, and `ai_geometry` when a surveyor has edited the
outline.

## Processing

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/processing` | Jobs of the surveyor and their assignment |
| GET | `/api/processing/stages` | The twelve stages, in order |
| POST | `/api/processing/upload` | Multipart `file` (`.tif` or `.tiff`). Validates the raster and registers a job. `400` wrong type or unreadable, `413` too large, `422` not usable for inference. |
| POST | `/api/processing/from-dataset/{dataset_id}` | Registers a job for imagery already in the library |
| POST | `/api/processing/{job_id}/start` | Queues the job. Idempotent for a running or finished job. |
| GET | `/api/processing/{job_id}` | Job state |
| GET | `/api/processing/{job_id}/result` | Summary and layer statistics. `409` until the job has completed. |

A job has `job_id`, `status` (`UPLOADED`, `QUEUED`, `PROCESSING`, `COMPLETED`,
`FAILED`), `stage`, `progress` (0 to 100), `stages` (each with `status`, `fraction`,
`detail`), `input_dataset`, `output_dataset`, `raster_meta`, `summary`, `error`,
`surveyor_id`, `assignment_id`, `created_at`, `updated_at`, and `source` once completed.

Stages: `UPLOAD`, `LOAD_MODEL`, `READ_RASTER`, `TILE_IMAGE`, `RUN_SEGMENTATION`,
`CALCULATE_CONFIDENCE`, `CALCULATE_ENTROPY`, `POLYGONIZE`, `REPAIR_GEOMETRY`,
`GENERATE_GIS`, `RUN_QA`, `READY_FOR_REVIEW`.

## Reviews and audit

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/reviews/queue` | `group` = `high`, `medium`, `low`, `verified`, `flagged`; `include_fragments`, `limit`, `offset`. Returns the page and the counts of every group. |
| GET | `/api/reviews` | Review history: `status`, `feature_id`, `mine`, `limit`, `offset` |
| GET | `/api/reviews/feature/{feature_id}` | Reviews and state of one feature |
| POST | `/api/reviews` | Record a decision (below) |
| GET | `/api/reviews/{review_id}` | One review |
| PATCH | `/api/reviews/{review_id}` | Change `comment` or `action`; the change is audited |
| GET | `/api/audit` | Audit events: `entity_id`, `mine`, `limit`, `offset` |

`POST /api/reviews` body:

```json
{
  "feature_id": "CAND-000105",
  "action": "approve | edit | flag | reject | add_ground_truth",
  "comment": "required for flag and reject",
  "source": "existing",
  "edited_geometry": { "type": "Polygon", "coordinates": [] },
  "ground_truth": {
    "observed_class": "Building",
    "latitude": 28.5592, "longitude": 77.6254,
    "accuracy_m": 0.03, "device": "GNSS rover",
    "observation": "Tin roof, single storey", "photo_reference": "IMG_0114",
    "observed_at": "2026-10-03T10:15:00+05:30"
  }
}
```

`edited_geometry` is required for `edit` and must be a valid longitude/latitude polygon.
`ground_truth` is required for `add_ground_truth` and needs at least an observed class, a
position (latitude and longitude together) or an observation. The stored review records the surveyor, the time, the model's confidence and
entropy for the feature, the original geometry and the edited one.

An audit event has `at`, `actor_id`, `actor_email`, `action`, `entity_type`, `entity_id`,
`before`, `after` and `reason`.

## Analytics

`GET /api/analytics` returns counts and areas computed from the layers of `source`:
candidate parcels, AI features and fragments, buildings, roads (with share of mapped
area), water, fields, other, road access, mapped area and extent, review counts by
group and by status, model uncertainty (or the statement that none was recorded), and
processing status.

## Export

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/export/summary` | The metadata an export will carry |
| GET | `/api/export/geojson` | Streamed GeoJSON |
| GET | `/api/export/csv` | Attribute table |
| GET | `/api/export/gpkg` | GeoPackage; `501` if GeoPandas is not installed |

Parameters: `layer` = `parcels` or `landcover`; `status` = `all`, `verified`,
`unverified`; `classes` (comma separated, land cover only); `source`.

The file's `metadata` holds the project, assignment, surveyor, processing job, model,
export time, record status, verification counts, the disclaimer and a legal notice. Each
feature has `verification_status`, `status_label` ("AI GENERATED / PRELIMINARY" or
"SURVEYOR VERIFIED" and so on) and `geometry_source` (`AI` or `SURVEYOR_EDIT`). A
surveyor's edited outline replaces the AI outline in the export. Exports are audited.

## Terrain

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/terrain/status` | Which of DSM and DTM exist, whether heights can be measured, and why not |
| GET | `/api/terrain/grid` | `bbox` (required), `surface` = `dtm` or `dsm`, `size`. `404` when the raster is missing. |
| GET | `/api/terrain/buildings` | Measured height per building footprint, or `available: false` with the reason |

## Original endpoints

Kept so that earlier clients keep working. They serve the `existing` source.

`GET /stats`, `/parcels`, `/parcels/{parcel_id}`, `/parcels-geojson`, `/landcover`,
`/landcover-geojson`, `/landcover-summary`, `/analytics`, `/export/geojson`.
