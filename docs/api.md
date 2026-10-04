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
| GET | `/api/system/status` | Model status (checkpoint present, runtime available, normalisation), pipeline settings, indexed layers, indexing progress and errors, `data_files`, available sources |

`data_files` says, for each required file (`parcels`, `landcover`, `model`), how it was
found. The same entry for the checkpoint is repeated as `model.checkpoint_file_status`.

```json
{
  "status": "ambiguous",
  "expected": "backend/data/parcels/candidate_parcels.geojson",
  "used": null,
  "candidates": ["candidate_parcels (1).geojson", "candidate_parcels (2).geojson"],
  "message": "Several .geojson files in backend/data/parcels/ (...) and none is named candidate_parcels.geojson. Keep one, or rename the right one to candidate_parcels.geojson."
}
```

| `status` | Meaning |
| --- | --- |
| `found` | The file exists under its expected name (`message` is null) |
| `fallback` | The expected name is absent and the folder holds exactly one file of that type, which is used (`used` names it) |
| `ambiguous` | Several files of that type and none under the expected name; none is used, the layer or model is unavailable |
| `missing` | No file of that type in the folder |

Paths are given relative to the repository, never as server paths.

## Surveyor and assignment

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/surveyors/me` | `{surveyor, assignment, notes}`; `assignment` is null when none is registered |
| GET | `/api/assignments/current` | The assignment, or `404` with the reason |
| GET | `/api/assignments/current/boundary` | Assigned boundary as a GeoJSON FeatureCollection |

`assignment` is the account's **current area**. It carries `assignment_id`, `name`,
`assignment_status`, `state`, `district`, `taluk`, `village`, `area_m2` and `area_ha`
(measured), `declared_area_ha`, `bbox`, `is_demo`, `kind` (`registry`, `work_area` or
`demo`), `is_official`, `editable` and `label`:

| `label` | Meaning |
| --- | --- |
| `ASSIGNED` | From the assignment registry, for this e-mail |
| `SELF-DECLARED WORK AREA` | Drawn or uploaded by the surveyor; not an official assignment |
| `DEMO ASSIGNMENT` | The registry's demo entry, shown only when `CADASTRA_ALLOW_DEMO_ASSIGNMENT=true` |

`surveyor` carries `govt_surveyor_id` (from the account's Supabase profile, or null),
`govt_surveyor_id_status` (`SELF-DECLARED` when present; it is not checked against any
register) and `profile_complete`.

### Work areas

The owner of a work area is always the signed-in account. Nothing in a request names
it, and an owner field in a request body is ignored.

| Method | Path | Body / returns |
| --- | --- | --- |
| GET | `/api/assignments` | `{items, current, current_id, notes, registry_error, limits}`. `items` lists the registry assignments for this e-mail, then the account's own work areas, each with `geometry`, `notes` and `is_current`. |
| POST | `/api/assignments/measure` | `{boundary}` → `{geometry, area_m2, area_ha, perimeter_m, metric_crs, vertices, notes}`. Checks and measures without saving. |
| POST | `/api/assignments` | `{name, state?, district?, taluk?, village?, origin, boundary, activate?}` → the new work area (`201`). `activate` defaults to true. |
| PATCH | `/api/assignments/{id}` | Any of `name`, `state`, `district`, `taluk`, `village`, `boundary`, `origin`, `reason` |
| DELETE | `/api/assignments/{id}` | `{deleted, was_active}` |
| POST | `/api/assignments/{id}/activate` | Makes one of the account's work areas, one of its registry assignments, or the demo assignment (see below) the current area |

- `boundary` is a GeoJSON Polygon or MultiPolygon, a Feature, or a FeatureCollection
  holding exactly one polygon feature. A `crs` member is honoured and the geometry is
  reprojected to longitude/latitude; without one, longitude/latitude is assumed.
- The area is measured by the server in the local UTM zone. An area sent by the client
  is ignored.
- Rejected with `400` and the reason: anything that is not one polygon, malformed or
  unclosed-and-unfixable rings, self-intersecting outlines, outlines whose points lie
  on a line, areas under 1 m² or over 1,000 km², more than 20,000 vertices, a CRS that
  cannot be used, text fields over 120 characters, an empty name, an `origin` other than
  `drawn` or `uploaded`.
- When demo fallback is allowed and no registry assignment lists the account, the demo
  assignment is listed too (`kind: "demo"`) and can be activated, so an account can
  return to it after using a work area; activating it keeps the work areas and makes
  none current.
- Registry assignments are read-only: PATCH and DELETE on them return `404`. Another
  account's work area returns `404` for every method, the same answer as for an id that
  does not exist.
- Every create, update, delete and activate is written to the audit log
  (`entity_type` `work_area`, actions `work_area.create`, `work_area.update`,
  `work_area.delete`, `work_area.activate`; choosing a registry assignment is
  `assignment.activate`), with the before and after values.

The current area decides the boundary on the map, the bounding box every map and
analytics query is filtered to, and the `assignment_id` recorded on new processing jobs,
reviews and export metadata.

## Datasets

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/datasets` | Ten source categories, each with `status`, `datasets`, `notes` and an `empty_message` when nothing is present. A missing or ambiguous existing layer is explained in the `ai_layers` category's `notes`. |
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
| GET | `/api/map/layers` | Layer catalogue: availability, counts, the reason a layer is unavailable, and `data_files` (as in the system status; null for a job source) |
| GET | `/api/map/assigned-area` | Assigned boundary plus `available` and `notes` |
| GET | `/api/map/parcels` | `bbox`, `zoom`, `limit` |
| GET | `/api/map/features` | `classes` (comma separated), `bbox`, `zoom`, `limit` |
| GET | `/api/map/gnss` | GNSS points and recorded ground-truth positions |
| GET | `/api/map/plots` | `bbox`, `zoom`, `limit`: candidate plots (a job source's `plots` layer) |
| GET | `/api/map/features/{uid}` | `geometry` = `overview` or `detail`, `reasoning` = true or false |
| GET | `/api/parcels/{parcel_id}` | A candidate parcel with reasoning |

An unavailable layer's `message` names the file or folder it needs, for example
"Expected file backend/data/parcels/candidate_parcels.geojson is missing. Place the file
there under that name." or "DSM dataset unavailable. Add a GeoTIFF to backend/data/dsm/."
When a feature endpoint is asked for a layer that does not exist, it returns an empty
collection with `available: false` and the same message.

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
| POST | `/api/processing/{job_id}/plots` | Builds (or rebuilds) candidate plots for a completed job; `409` otherwise. Status in the job's `summary.plots.status` (`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`). The job's other layers are not changed. |
| GET | `/api/processing/models` | `{models, default, note}`. Each model: `id`, `name`, `file`, `trained_on`, `suits`, `licence`, `available`, `reason` (why not, when unavailable), `hash` (first 12 hex digits of SHA-256), `size_bytes`. No server paths. |
| GET | `/api/processing/{job_id}/area-check` | Whether the image overlaps the current area: `{intersects, distance_km, message, area_id, area_name, image_bbox, matching_areas}` |
| POST | `/api/processing/{job_id}/start` | Queues the job. Idempotent for a running or finished job. If the image does not overlap the current area, answers `409` with the distance unless `confirm_outside_area=true` is passed. `model_id` chooses the model (default when omitted); an unknown id is `400`, an unavailable one `409`. |
| GET | `/api/processing/{job_id}` | Job state |
| GET | `/api/processing/{job_id}/result` | Summary and layer statistics. `409` until the job has completed. |

A job has `job_id`, `status` (`UPLOADED`, `QUEUED`, `PROCESSING`, `COMPLETED`,
`FAILED`), `stage`, `progress` (0 to 100), `stages` (each with `status`, `fraction`,
`detail`), `input_dataset`, `output_dataset`, `raster_meta`, `summary`, `error`,
`surveyor_id`, `assignment_id`, `area_check`, `model`, `created_at`, `updated_at`, and
`source` once completed.

`model` is `{id, name, file, hash, trained_on, suits, licence}`, recorded when the job
starts; the file is checked again when the run begins and a changed checkpoint fails the
job rather than run under the wrong record. Jobs from before models were selectable carry
the village model with a `note` saying it was recorded afterwards. Their features,
exports and the source label (`/api/system/status` and `/api/map/layers` → `sources`)
name the model.

A job belongs to the area that is current when it is **started**: `assignment_id` is
set then, and `area_check` records whether the image overlapped that area
(`intersects`), how far away it was if not (`distance_km`, geodesic, between the image
extent and the area boundary) and whether the surveyor confirmed processing it anyway
(`confirmed_outside_area`). `matching_areas` lists the account's areas the image does
overlap, so the client can offer to switch. Jobs started before this check existed
have `area_check: null`.

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

`candidate_plots` is counted separately from `candidate_parcels`. `plots` (null when the
source has none) gives `count`, `area_m2`, `p10_area_m2`, `median_area_m2`,
`p90_area_m2`, the plots holding exactly one building counted two ways
(`one_building_seed_rule` and `one_building_share_seed_rule` for buildings of at least
`PLOT_MIN_BUILDING_M2`, which is 100 % by construction; `one_building_all` and
`one_building_share_all` for every detected building feature), `low_coverage` (plots
under 5 % built, flagged), `road_access`, and
`reference_check`: when an existing GIS layer covers the area, `reference_features`,
`plots_with_one`, `plots_with_several`, `plots_with_none`, `reference_with_own_plot` and
`reference_outside_plots` (each reference feature placed by its representative point).

## Export

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/export/summary` | The metadata an export will carry |
| GET | `/api/export/geojson` | Streamed GeoJSON |
| GET | `/api/export/csv` | Attribute table |
| GET | `/api/export/gpkg` | GeoPackage; `501` if GeoPandas is not installed |

Parameters: `layer` = `parcels`, `landcover` or `plots`; `status` = `all`, `verified`,
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
