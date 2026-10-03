# Cadastra Vision

**AI + GIS Surveyor Assistance Platform** (Smart India Hackathon, problem statement 12)

> AI proposes. GIS validates. Surveyors verify.

Cadastra Vision takes a surveyor from sign-in to a GIS-ready export for their assigned
area: it discovers the datasets for the assignment, runs a six-class segmentation model
over drone orthoimagery, turns the result into measured GIS features with model-derived
uncertainty, ranks them for review, records every surveyor decision, and exports the
result with its verification status.

**Everything the model produces is AI generated and preliminary. A candidate parcel is a
spatial candidate for a surveyor to inspect. It is not a legal cadastral ownership
record.** The application says so on the map, in every panel and in every export.

```
LOGIN → SURVEYOR → ASSIGNED AREA → DATASETS → AI PROCESSING → SIX-CLASS SEGMENTATION
      → GIS FEATURES → PARCEL REASONING → QA / UNCERTAINTY → SURVEYOR VERIFICATION → EXPORT
```

## What is in the repository

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI service: authentication, assignment, datasets, AI pipeline, GIS layer index, reviews, analytics, export, terrain |
| `backend/ai/` | U-Net / ResNet34 model, windowed inference, polygonisation, geometry repair, QA, candidate parcels |
| `backend/gis/` | GeoJSON reader, CRS handling, metric measurement, indexed layer cache |
| `backend/tests/` | 202 tests (see [Tests](#tests)) |
| `frontend/` | React + Vite + Leaflet + React Three Fiber application |
| `docs/` | [architecture](docs/architecture.md), [API](docs/api.md), [deployment](docs/deployment.md) |

## Files you must supply

These are not stored in git (they are large, and the repository is public).

| File | Put it at | Needed for |
| --- | --- | --- |
| `best_weighted_multiclass_unet.pth` (98 MB) | `backend/models/` | AI processing |
| `candidate_parcels.geojson` (53 MB) | `backend/data/parcels/` | Existing candidate parcel layer |
| `uplarshi_landcover_with_attributes.geojson` (122 MB) | `backend/data/landcover/` | Existing land-cover layer |

Use exactly those file names. Optional datasets go in `backend/data/imagery/`,
`satellite/`, `dsm/`, `dtm/`, `land_records/`, `gis/`, `survey_of_india/`, `documents/`
and `gnss/`, or are uploaded from the Datasets page. A category with no file is shown as
**Not available**; nothing is generated to fill it.

## Quick start

Requirements: Python 3.10 or newer, Node.js 22 or newer.

### 1. Backend

```bash
# from the repository root
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# CPU build of PyTorch (for a GPU, install the CUDA build from pytorch.org instead)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r backend/requirements.txt

cp backend/.env.example backend/.env   # then fill in SUPABASE_URL and SUPABASE_KEY

python -m backend.scripts.selfcheck    # checks packages, the checkpoint, the pipeline, the layers
uvicorn backend.main:app --reload --port 8000
```

The first start indexes the two GeoJSON layers (about half a minute); later starts reuse
the cache. API documentation is served at <http://localhost:8000/docs>.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env                   # then fill in VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
npm run dev                            # http://localhost:5173
```

### 3. Signing in

Authentication uses Supabase e-mail and password. Create the surveyor's user in your
Supabase project (Authentication → Users), then list their e-mail under `assigned_to` in
`backend/data/surveyors/surveyor_assignments.geojson` to give them an assignment
(format in [docs/deployment.md](docs/deployment.md#assignments)).

To try the application without Supabase, run both halves in the explicit development
mode. Every screen then carries a "Development session" banner.

```bash
# backend/.env            # frontend/.env
CADASTRA_AUTH=off         VITE_AUTH_MODE=off
```

## Environment variables

Backend (`backend/.env`; every variable is optional except the two Supabase values):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUPABASE_URL` | none | Supabase project URL |
| `SUPABASE_KEY` | none | Supabase **anon** key. Never a service-role key. |
| `CADASTRA_AUTH` | `supabase` | `supabase` verifies every request; `off` is local development only |
| `CADASTRA_ALLOW_DEMO_ASSIGNMENT` | `true` | Users with no assignment see the registry's demo assignment, labelled DEMO |
| `FRONTEND_URL`, `CORS_ORIGINS` | localhost | Allowed browser origins |
| `CADASTRA_DATA_DIR` | `backend/data` | Datasets, layer cache and state database |
| `CADASTRA_PROCESSING_DIR` | `backend/processing` | Uploaded rasters and job outputs |
| `CADASTRA_MODEL_PATH` | `backend/models/best_weighted_multiclass_unet.pth` | Checkpoint |
| `MODEL_NORMALIZATION` | `scale_255` | Input scaling; must match training (see architecture notes) |
| `TILE_SIZE`, `TILE_OVERLAP` | `512`, `64` | Inference window and context margin, in pixels |
| `POLYGONIZE_CHUNK` | `4096` | Polygonisation chunk, in pixels |
| `SIEVE_MIN_PIXELS` | `8` | Specks smaller than this are merged into their neighbour |
| `SLIVER_AREA_M2` | `1.0` | Minimum mapping unit; smaller features are fragments |
| `MIN_CANDIDATE_PARCEL_M2` | `25` | Minimum size counted as a candidate parcel |
| `ROAD_ACCESS_DISTANCE_M` | `5` | A road within this distance counts as road access |
| `MAP_FEATURE_LIMIT` | `4000` | Most features sent to the map per request |
| `MAX_UPLOAD_MB` | `4096` | Upload size limit |
| `EXPORT_BACKGROUND` | `false` | Also vectorise the Background class |

Frontend (`frontend/.env`; everything prefixed `VITE_` is shipped to the browser, so only
public values belong here):

| Variable | Meaning |
| --- | --- |
| `VITE_API_URL` | Backend URL, default `http://localhost:8000` |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Supabase project URL and anon key |
| `VITE_AUTH_MODE` | `off` only together with `CADASTRA_AUTH=off` |

## The model

`best_weighted_multiclass_unet.pth` is a PyTorch `state_dict` for a U-Net with a ResNet34
encoder (`segmentation_models_pytorch`), 3 input channels (8-bit RGB) and 6 output
classes: 0 Background, 1 Field, 2 Building, 3 Road, 4 Water, 5 Other. It was trained on
SVAMITVA drone orthoimagery.

The model can be run on new survey imagery of a similar kind. How well it does there
varies with geography, sensor, resolution, season, illumination and image quality. For
that reason every feature carries the model's confidence and entropy, features are ranked
for review, and nothing is final until a surveyor has looked at it.

Imagery requirements: GeoTIFF, georeferenced with a CRS, north-up, 8-bit RGB (an alpha
band or a NoData value is used as the valid-pixel mask; without either, pure black is
treated as NoData). Centimetre-level drone orthoimagery is what the model was trained on;
coarser imagery is accepted with a warning.

## Tests

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests            # or: python -m unittest discover -s backend/tests -t .
cd frontend && npm test && npm run build
```

The backend suite uses a temporary data directory with synthetic layers of known size, so
it never touches real survey data. Tests that need PyTorch, Rasterio, GeoPandas or the
checkpoint skip themselves when those are absent and say why.

### What has been run, and what has not

The development environment this version was built in had no access to the Python and
npm package registries, so PyTorch, Rasterio, GeoPandas, Shapely, FastAPI, Leaflet,
Three.js and Vite could not be installed there. The record below is what that leaves.

Run and passing there:

- 166 of the 202 backend tests: geometry and projection, GeoJSON validation, the layer
  index, QA rules, persistence, authentication, assignment, processing-job state, parcel
  reasoning, and the HTTP API. FastAPI itself could not be installed, so the API tests
  ran through a small stand-in built on Starlette (the library FastAPI is built on).
  They have not yet run against FastAPI proper.
- Every API endpoint against the real Uplarshi layers (1,752 candidate parcels and 5,363
  land-cover features).
- The checkpoint's 278 tensors, read without PyTorch and compared with the model layout.
- The 8 frontend unit tests, a bundle of the frontend source, and screenshots of every page.

Written but **not yet run anywhere**:

- 36 backend tests that need PyTorch, Rasterio, GeoPandas or Shapely: model loading, the
  end-to-end pipeline on a synthetic GeoTIFF, polygonisation, geometry repair, DSM/DTM
  heights.
- The AI pipeline with the real checkpoint on real imagery.
- GeoPackage export.
- `npm run build`, the Leaflet map, the 3D globe and 3D view with the real libraries.
- Supabase sign-in.

Run `python -m backend.scripts.selfcheck`, `python -m pytest backend/tests` and
`npm run build` on your machine first. If any of them fails, that is a defect to fix, not
a setup problem to work around.

## Known limitations

These follow from data that does not exist yet, not from missing code:

- **No orthoimage is in the project**, so the model has not been run on real imagery
  here. Upload a GeoTIFF on the Processing page to produce features with confidence and
  entropy.
- **The two existing layers carry no confidence or entropy.** They were exported before
  those were recorded. Their review priority therefore comes from geometry and road
  access only, and the application says "Model uncertainty not recorded".
- **Most existing features are fragments.** 3,932 of the 5,363 land-cover features are
  under 1 m², and two "candidate parcels" are merged regions of 4.7 ha and 2.2 ha with
  thousands of holes. They are ranked High priority for that reason.
- **No DSM or DTM**, so the 3D view shows flat footprints and says heights are unavailable.
- **No authoritative parcel layer**, so parcel reasoning runs in candidate mode.
- **The assignment registry holds one demo assignment** with a placeholder rectangle. The
  application labels it DEMO and reports that its declared area (12.5 ha) does not match
  its geometry (1,085 ha).
- **Geometry editing covers the outer ring of single polygons**: drag a vertex, click an
  edge to add one, right-click to remove one. Holes are kept as they are, multi-part
  features cannot be edited, and drawing a new polygon from nothing is not implemented.
- **Jobs run one at a time in the API process.** A job interrupted by a restart is marked
  failed and must be started again.
- **State is in SQLite**, which suits one server. Several API servers would need the
  store moved to Postgres ([architecture](docs/architecture.md#persistence)).
