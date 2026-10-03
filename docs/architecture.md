# Architecture

Cadastra Vision has two parts: a FastAPI backend that holds all data, computation and
state, and a React frontend that displays it. The frontend computes nothing a surveyor
relies on. Every count, area, confidence and status on screen comes from the API.

```
Browser (React, Leaflet, React Three Fiber)
   │  Supabase session token on every request
   ▼
FastAPI  ── api/        thin routers
         ── services/   assignment, datasets, map, parcels, reviews, analytics, export,
         │              processing, terrain
         ── ai/         model, inference, pipeline, polygonize, topology, qa, parcels
         ── gis/        geojson_io, geometry, metric, layers (indexed cache)
         ── core/       auth, store (SQLite), runtime, deps
   │
   ▼
backend/data/         datasets by category, layer cache, state database
backend/processing/   uploaded rasters and per-job outputs
backend/models/       trained checkpoint
```

## Principle and vocabulary

AI proposes, GIS validates, surveyors verify. The code keeps these three apart:

- The **model** produces a class, a confidence and an entropy per pixel.
- **GIS checks** measure geometry in a projected CRS, test validity and overlap, and rank
  features for review. They do not change what the model said.
- A **surveyor decision** is a separate record. It never overwrites the AI geometry; an
  edit is stored beside the original.

Verification status of a feature:

| Status | Meaning |
| --- | --- |
| `AI_GENERATED` | No surveyor has looked at it; low review priority |
| `REVIEW_REQUIRED` | No surveyor has looked at it; high or medium review priority |
| `SURVEYOR_REVIEWED` | Ground truth recorded, no decision yet |
| `SURVEYOR_VERIFIED` | Approved |
| `EDITED` | Outline corrected by a surveyor |
| `FLAGGED` | Marked for follow-up, with a reason |
| `REJECTED` | Rejected, with a reason |

## Authentication and identity

The browser signs in with Supabase (e-mail and password) and sends the session's access
token as a bearer token. The backend asks Supabase who the token belongs to
(`GET {SUPABASE_URL}/auth/v1/user` with the anon key) and caches the answer for 60
seconds. No service-role key is used anywhere.

Surveyors create an account on `/signup` (`supabase.auth.signUp`) or sign in with
Google (`supabase.auth.signInWithOAuth`, returning to `/login`). The full name is
stored as `user_metadata.full_name` and becomes the display name.

Every account must also enter a **government surveyor ID**, kept as
`user_metadata.govt_surveyor_id`: on the sign-up form, or on `/complete-profile`, where
`ProtectedLayout` sends any signed-in account that has none (typically after a first
Google sign-in). The backend reads it from the verified `/auth/v1/user` answer like the
name. It is **self-declared**: the user can set it, nothing checks it against a
government register, and the API and screens label it `SELF-DECLARED`. After saving
it the browser refreshes its session, so the server's 60-second identity cache (keyed
by token) does not keep serving the profile without it. The requirement is enforced by
the frontend; the API reports `profile_complete` but does not refuse requests from an
account without an ID.

The surveyor is derived from that verified identity. Request bodies and query strings
are never read for a surveyor id. `CADASTRA_AUTH=off` replaces the check with a single
labelled development identity and is for local use only.

## Assignment and work areas

Every request is answered for the account's **current area**, chosen by
`services/assignment_service.resolve_context` in this order:

1. the account's active **work area**: a boundary the surveyor drew or uploaded in the
   app, stored in the `work_areas` table, labelled `SELF-DECLARED WORK AREA` with a
   note that it is not an official assignment;
2. a **registry assignment**: a feature of
   `backend/data/surveyors/surveyor_assignments.geojson` whose `assigned_to` lists the
   signed-in e-mail, labelled `ASSIGNED` (with several, the one the surveyor activated,
   kept in `assignment_preferences`, else the first);
3. the registry's **demo** entry (`is_demo`), labelled `DEMO ASSIGNMENT`, only when
   `CADASTRA_ALLOW_DEMO_ASSIGNMENT=true`;
4. nothing: the API says so and the screens say "No work area yet. Add one to begin."

An account never sees another account's work area; its id answers `404` exactly like
an id that does not exist. Registry assignments are read-only in the app.
`services/work_area_service.py` validates a boundary before storing it: one Polygon or
MultiPolygon, reprojected from a declared CRS, structurally checked by
`gis/geometry.analyse_geometry`, tested for self-intersection with Shapely, and measured
in the local UTM zone (the client never supplies an area). Each create, edit, delete and
activation is written to the audit log with before and after values.

The current area provides the boundary drawn on the map, the bounding box every map and
analytics query is filtered to, and the `assignment_id` recorded on new jobs, reviews and
export metadata (exports also carry the area's label, so an export from a self-declared
area says so). In the browser, switching area reloads the profile and remounts the page
(`AppShell` keys the page on the area id), so the map, figures, review queue and
analytics are fetched again.

The boundary's area is measured in the local UTM zone. If the registry declares an area
that differs from the geometry by more than 10%, or the boundary is an axis-aligned
rectangle, the API returns a note and the dashboard shows it.

## Layers and sources

A **source** is a set of layers that belong together:

- `existing`: the two GeoJSON files shipped with the project (candidate parcels and
  land-cover features).
- `job:<JOB-ID>`: the output of one processing job.

Each source has two **kinds** of layer: `parcels` and `landcover`. Feature identifiers
are `CAND-000001` for candidate parcels and `FLD-`, `BLD-`, `RD-`, `WTR-`, `OTH-` for
land-cover classes.

### The layer index

The existing layers are 53 MB and 122 MB of GeoJSON. They are never sent to the browser
whole and never parsed per request. On first use `gis/layers.py` walks each file once
and writes an SQLite cache with an R-tree:

- structural validation of every geometry; unusable features are counted and reported,
  not dropped silently;
- reprojection to longitude/latitude from the file's declared CRS (CRS84, EPSG:4326, any
  UTM zone and Web Mercator without PROJ; any other CRS through pyproj);
- area, perimeter, extent and compactness measured in the UTM zone of the feature. A
  supplied `area_m2` is never used: the measured value is, and the number of supplied
  values that disagree by more than 1 % is recorded in the layer's `area_check`;
- three geometries per feature: full, 5 cm generalised ("detail") and 40 cm generalised
  ("overview");
- review priority from `ai/qa.py`.

Indexing the Uplarshi layers takes about 35 seconds and 300 MB of memory once; the cache
(47 MB) is reused until a file changes.

Map requests are answered from the cache by bounding box and zoom: the overview geometry
at low zoom, detail at high zoom, the largest features first, capped at
`MAP_FEATURE_LIMIT`. Exports stream the full-resolution geometry.

This hot path uses NumPy only. Shapely, pyproj, Rasterio, GeoPandas and PyTorch are
imported lazily where they are needed (pipeline, terrain, exact parcel overlay,
GeoPackage export), so the API starts and serves the map even if the model stack is not
installed, and reports that state at `/api/system/status`.

## AI pipeline

`ai/pipeline.py`, called by `services/processing_service.py`:

```
GeoTIFF
  → inspect and validate (CRS, transform, north-up, 8-bit RGB, size)
  → plan 512 px tiles with a 32 px context margin
  → for each tile: read window, build valid-pixel mask, U-Net / ResNet34, softmax
        class      = arg max                       → prediction.tif  (uint8, NoData 255)
        confidence = max probability               → confidence.tif  (float32)
        entropy    = −Σ p ln p                     → entropy.tif     (float32, nats)
  → polygonise the class raster in chunks, stitch regions cut by chunk borders
  → repair geometry (make_valid), detect overlaps and slivers
  → measure in a metric CRS
  → derive candidate parcels
  → QA: review priority per feature
  → ai_features.geojson, candidate_parcels.geojson, qa_report.json, run_summary.json
```

Details that matter:

- **Memory is bounded** by one tile during inference and one chunk during
  polygonisation, whatever the size of the mosaic. Rasters are read and written window
  by window.
- **Tile borders**: the model sees each tile plus a margin of `TILE_OVERLAP / 2` pixels;
  only the core is written.
- **NoData**: the dataset mask (NoData value or alpha band) is used. If neither is
  declared, pure black is treated as NoData and the job reports that. Tiles with no
  valid pixels are skipped. NoData is never classified.
- **CRS**: the output rasters keep the source CRS and transform. Vectors are measured in
  the source CRS only if it is projected in metres *and* not a Mercator projection;
  Web Mercator (EPSG:3857, common for exported orthomosaics) and geographic rasters are
  measured in the UTM zone that contains them. Web Mercator metres are not ground
  metres: at Uplarshi (28.56 N) they overstate areas 1.30 times and lengths 1.14 times.
  Area, perimeter and every distance threshold (fragment size, minimum candidate
  parcel, road access) are applied in the measuring CRS. The ground size of a pixel is
  measured the same way and reported as `resolution_m` and `pixel_ground_area_m2`.
  Vectors are written as longitude/latitude GeoJSON for the web map.
- **Sieve**: before polygonising, connected specks smaller than the minimum mapping
  unit (`SLIVER_AREA_M2`, divided by the true ground area of one pixel) are merged into
  the neighbouring class (`rasterio.features.sieve`, 4-connected). Each chunk is sieved
  with a margin of 2 x sqrt(threshold) pixels so that a region cut by a chunk border is
  not mistaken for a speck. The pixels reassigned are counted per class (removed and
  gained, with their ground area) in `run_summary.json` and `qa_report.json` under
  `sieve`. `SIEVE_MIN_PIXELS` overrides the derived threshold.
- **Overlap noise**: regions traced from one pixel grid cannot overlap, but after
  reprojection to the measuring CRS their shared edges can differ by floating-point
  noise (up to 3 x 10^-7 m2 measured on Uplarshi). Overlaps smaller than 1 % of a
  pixel are therefore not reported.
- **Stage timing**: `run_summary.json` records wall-clock seconds per stage under
  `stage_seconds`; geometry repair reports progress while it runs.
- **Per-feature uncertainty** is the mean confidence and mean entropy of the feature's
  pixels, computed with one `bincount` per chunk.
- **Polygonisation** runs in pixel coordinates, so vertices are exact integers and
  regions cut by a chunk border meet exactly. They are joined with a union-find over
  shared edges (not corners), then transformed to map coordinates.
- **Geometry status** of every feature is `VALID`, `REPAIRED`, `INVALID` or `EMPTY`.
  Empty geometries are removed and listed in the QA report.
- **Progress** shown to the surveyor is the stage state the pipeline reports. There are
  no timers and no invented percentages.

### Model and preprocessing

`ai/model.py` builds `smp.Unet(encoder_name="resnet34", encoder_weights=None,
in_channels=3, classes=6)` and loads the checkpoint strictly. A checkpoint that does not
match is reported with the missing, unexpected and mismatched tensors; there is no
fallback model.

The checkpoint has 278 tensors, all matching that layout
(`backend/tests/fixtures/checkpoint_manifest.json` records their names and shapes; a
test compares the model against it).

**Input normalisation.** The training code was not available, so the normalisation was
inferred from the checkpoint. The first batch-norm layer stores the mean of the first
convolution's output over the training data. Solving that for the mean input gives about
(0.383, 0.394, 0.358) per channel with a spread near 0.17, which is what 8-bit imagery
divided by 255 looks like. Under ImageNet mean/std normalisation the same statistics
would need imagery with almost no contrast. `scale_255` is therefore the default. This
is an inference, not a confirmed fact: if you have the training script, check it, and
compare the two on real imagery with

```bash
python -m backend.scripts.compare_normalization path/to/ortho.tif
```

then set `MODEL_NORMALIZATION` accordingly.

### Review priority

`ai/qa.py` ranks a feature from signals that can be measured. A high rank is a reason to
look at the feature first. It is not proof the feature is wrong.

| Signal | Medium | High |
| --- | --- | --- |
| Mean confidence | below 0.80 | below 0.60 |
| Mean entropy (nats, maximum ln 6 ≈ 1.79) | above 0.70 | above 1.20 |
| Geometry | repaired automatically | still invalid |
| Overlap with another feature | yes | |
| Rings, outer plus holes (features of 25 m² or more) | more than 50 | more than 500 |
| Compactness 4πA/P² (features of 25 m² or more) | below 0.02 | below 0.005 |
| Candidate parcel of 25 m² or more with no road within 5 m | yes | |

Features under 1 m² are flagged as fragments and hidden from the queue by default. When
a layer has no confidence or entropy, the feature is flagged `UNCERTAINTY_UNAVAILABLE`
and the interface says "Model uncertainty not recorded" rather than showing a number.

These thresholds are starting values chosen for this prototype. They are not calibrated
against ground truth and should be revisited once verified data exists.

## Parcel reasoning

Two situations, and the response always says which one applies:

- **Existing parcel GIS is available.** A polygon layer placed in `data/land_records/`
  (or uploaded under "Existing maps and land records") is drawn on the map as the
  *Existing GIS* layer. With "Select existing GIS" switched on, clicking one of its
  parcels returns the record as supplied (attributes, area and perimeter measured from
  its geometry) and the AI features of the active source intersected with it. The record
  is never modified, and the application does not claim to have verified it.
- **No parcel GIS** (the current state of the project data). Candidate parcels are the
  contiguous Field regions of the segmentation, each with its distance to the nearest
  Road feature. This is the same definition the project's `candidate_parcels.geojson`
  uses. They are labelled CANDIDATE PARCEL / AI GENERATED / PRELIMINARY.

In both cases `services/parcel_service.py` reports the land-cover composition, building
count and area inside the parcel's outer boundary. With Shapely installed this is an
exact polygon intersection; without it, a containment test, and the response names the
method used.

## Persistence

`core/store.py` keeps state in one SQLite database (`data/state/cadastra.db`):

| Table | Holds |
| --- | --- |
| `jobs` | Processing jobs: status, stage, progress, per-stage state, raster metadata, summary, error, surveyor, assignment, timestamps |
| `reviews` | Every surveyor decision: feature, action, comment, model confidence and entropy at the time, original and edited geometry, ground truth |
| `audit_log` | Who did what and when, with before and after values and the reason |
| `datasets` | Registered datasets |
| `work_areas` | Self-declared work areas: owner e-mail and surveyor id, name, state, district, taluk, village, boundary (lon/lat GeoJSON), measured area, origin (`drawn` or `uploaded`), whether it is the owner's current area, timestamps |
| `assignment_preferences` | Which registry assignment an account chose, when it has several |

Jobs survive a restart. A job that was running when the server stopped is marked failed
with an explanation.

SQLite was chosen because the project had no database schema or credentials configured
and one server is enough for the prototype. `Store` is the only module that touches the
database, so moving to Supabase Postgres means reimplementing that one class.

## Terrain and 3D

Building height is `DSM − DTM`, sampled inside each building footprint (90th percentile
of the DSM minus the median of the DTM). It is computed only from rasters in
`data/dsm/` and `data/dtm/`. With either missing, the API answers
"DSM/DTM data required for measured terrain and building height." and the 3D view draws
flat footprints. Height is never derived from footprint area.

## Frontend

React 19, Vite, React Router, Leaflet through react-leaflet, Three.js through React
Three Fiber and Drei, supabase-js, axios.

| Route | Page |
| --- | --- |
| `/` | Home |
| `/about` | About |
| `/login`, `/reset-password` | Sign in, forgot password, set a new password |
| `/signup` | Create a surveyor account (e-mail, or Google) with a government surveyor ID |
| `/complete-profile` | Enter the government surveyor ID if the account has none |
| `/dashboard` | Assignment, KPIs, map, review queue |
| `/map` | Full 2D map and 3D view |
| `/survey` | Current area, work areas (list, add, edit, delete, activate) |
| `/survey/datasets` | Dataset discovery and upload |
| `/survey/processing` | Upload, validate, run, follow stages |
| `/survey/review` | Review queue, map, decisions, audit trail |
| `/survey/analytics` | Charts from `/api/analytics` |
| `/survey/export` | GeoJSON, CSV, GeoPackage |

Everything from `/dashboard` down is inside `ProtectedLayout`, which redirects to `/login` without a session.

- `context/AuthContext` holds the Supabase session, which persists across reloads.
- `context/WorkspaceContext` holds the surveyor, the assignment and the active source.
- `components/MapWorkbench` combines the map, layer control, feature panel and review
  form, and is reused on the dashboard, the map page and the review page.
- `components/MapView` fetches features for the visible extent and zoom and draws them
  with Leaflet's canvas renderer.
- `components/Globe` draws the Earth at night (NASA-derived day, city-light and cloud
  textures in `public/textures/earth/`, shaded in a custom shader), with orbit lines and
  the survey site over India marked. Until the textures load, or if they cannot, it
  falls back to a texture painted from the hand-simplified outlines in
  `assets/land-outline.json`. It is decoration and not a geographic dataset.
