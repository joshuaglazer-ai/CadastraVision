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
| `backend/tests/` | 240 tests (see [Tests](#tests)) |
| `frontend/` | React + Vite + Leaflet + React Three Fiber application |
| `docs/` | [architecture](docs/architecture.md), [API](docs/api.md), [deployment](docs/deployment.md) |

## Files you must supply

These are not stored in git (they are large, and the repository is public).

| File | Put it at | Needed for |
| --- | --- | --- |
| `best_weighted_multiclass_unet.pth` (98 MB) | `backend/models/` | AI processing |
| `candidate_parcels.geojson` (53 MB) | `backend/data/parcels/` | Existing candidate parcel layer |
| `uplarshi_landcover_with_attributes.geojson` (122 MB) | `backend/data/landcover/` | Existing land-cover layer |

The quickest way to put them there, from a folder where you downloaded them:

```bash
python -m backend.scripts.prepare_data --from ~/Downloads
```

This also recognises browser download names such as `candidate_parcels (2).geojson` and
`best_weighted_multiclass_unet(2).pth`, copies each file to its place under the clean
name, builds the layer index, and prints what it copied and what it could not find. It
never picks between differing copies: if your folder has two different
`uplarshi_landcover_with_attributes (n).geojson` files, it copies neither and tells you.

If a file sits in its folder under another name, the backend uses it as long as it is the
only file of that type there. With several candidates it uses none, and the layer
control, the Datasets page and `/api/system/status` say which file is expected and
which files are in the way.

Optional datasets go in `backend/data/imagery/`,
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

### 3. Accounts, sign-in and work areas

Authentication uses Supabase. A surveyor either creates an account on **Create an
account** (`/signup`: full name, government surveyor ID, e-mail, password of at least 8
characters) or uses **Continue with Google**. E-mail sign-up must be enabled in the
Supabase project, and Google sign-in needs the Google provider configured there
([docs/deployment.md](docs/deployment.md#sign-in-with-google)). With e-mail confirmation
on, a new e-mail account opens the link sent to it before signing in.

Every account must enter a **government surveyor ID** before the workspace opens; a
Google account is asked for it once, after its first sign-in. The ID is stored in the
account's Supabase profile and is **self-declared**: nothing checks it against a
government register, and the application labels it so.

On the **Survey workspace** page a surveyor manages their **work areas**: draw a
boundary on the map, upload it as a GeoJSON file, or take the current map view as a
rough rectangle; then name it and give its state, district, taluk and village. The
server checks the boundary and measures it. Work areas are labelled **SELF-DECLARED WORK
AREA** everywhere, including exports; they are not official assignments. A switcher in
the bar under the navigation moves between the account's areas, and the map, key
figures, review queue and analytics follow the current one.

Official assignments come from `backend/data/surveyors/surveyor_assignments.geojson`:
an administrator lists the surveyor's sign-in e-mail under `assigned_to`. They are
labelled ASSIGNED and are read-only in the app. Each account sees only its own work
areas and the registry assignments for its e-mail. An account with neither sees the
DEMO assignment when `CADASTRA_ALLOW_DEMO_ASSIGNMENT=true`; with `false` (recommended for
production) it sees "No work area yet. Add one to begin."

To try the application without Supabase, run both halves in the explicit development
mode. Every screen then carries a "Development session" banner, and the sign-up page
says that account creation is unavailable in this mode.

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
| `CADASTRA_MODEL_PATH` | `backend/models/best_weighted_multiclass_unet.pth` | The default (village) checkpoint; its folder also holds `registry.json` and the other checkpoints |
| `MODEL_NORMALIZATION` | `scale_255` | Input scaling; must match training (see architecture notes) |
| `TILE_SIZE`, `TILE_OVERLAP` | `512`, `64` | Inference window and context margin, in pixels |
| `POLYGONIZE_CHUNK` | `4096` | Polygonisation chunk, in pixels |
| `SIEVE_MIN_PIXELS` | derived | Specks smaller than this many pixels are merged into the neighbouring class before polygonising. Unset: `SLIVER_AREA_M2` divided by the true ground area of one pixel (1,468 px for the 2.6 cm Uplarshi orthomosaic). Set it only to override. |
| `SLIVER_AREA_M2` | `1.0` | Minimum mapping unit; smaller features are fragments |
| `MIN_CANDIDATE_PARCEL_M2` | `25` | Minimum size counted as a candidate parcel |
| `ROAD_ACCESS_DISTANCE_M` | `5` | A road within this distance counts as road access |
| `MAP_FEATURE_LIMIT` | `4000` | Most features sent to the map per request |
| `MAX_UPLOAD_MB` | `4096` | Upload size limit |
| `EXPORT_BACKGROUND` | `false` | Also vectorise the Background class |
| `PLOTS_ENABLED` | `true` | Build candidate plots after each processing job |
| `PLOT_LIMIT_M` | `25` | Land farther than this from a building is not assigned to a plot |
| `PLOT_GRID_M` | `0.10` | Grid on which the land is divided (metres) |
| `PLOT_MIN_BUILDING_M2` | `5` | Smallest building that gets a plot of its own |

Frontend (`frontend/.env`; everything prefixed `VITE_` is shipped to the browser, so only
public values belong here):

| Variable | Meaning |
| --- | --- |
| `VITE_API_URL` | Backend URL, default `http://localhost:8000` |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Supabase project URL and anon key |
| `VITE_AUTH_MODE` | `off` only together with `CADASTRA_AUTH=off` |

## The models

A surveyor chooses the model for each processing job on the Processing page. The choices
come from `backend/models/registry.json`:

| Id | Name | File (in `backend/models/`) | Suits | Trained on | Licence |
| --- | --- | --- | --- | --- | --- |
| `village` (default) | Village model (SVAMITVA) | `best_weighted_multiclass_unet.pth` | village | SVAMITVA drone orthoimagery of rural villages | Project checkpoint |
| `urban` | Urban model (UAVPal, Bhopal) | `cadastra_unet_resnet34_uavpal_sep.pth` | urban | Fine-tuned on UAVPal drone imagery of Bhopal, trained to keep touching buildings apart | CC BY-NC-SA 4.0: research and demo use |

Both are the same architecture (U-Net / ResNet34, 6 classes, 8-bit RGB scaled by 1/255);
the registry refuses any entry that is not. A model whose file is not in
`backend/models/` is listed as unavailable with the reason, never an error. The browser
sends only a model id; the server finds the file. Only one model is held in memory:
switching releases the previous one before loading the next.

Each job records the model it ran with (id, file name and the first 12 hex digits of the
file's SHA-256). Every feature carries `model_id`, `model_file` and `model_hash`; the
Layers dropdown names the model; GeoJSON, CSV and GeoPackage exports state it. Jobs from
before models were selectable are recorded as the village model, marked as recorded
afterwards. The checkpoint files are never committed.

### The village model

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

On Windows 11 with Python 3.13, PyTorch 2.14 (CPU) and Node.js 22, on 3 October 2026:

- `python -m pytest backend/tests`: all 240 tests pass against FastAPI, PyTorch,
  Rasterio, GeoPandas and Shapely, none skipped. This includes model loading, the
  end-to-end pipeline on a synthetic GeoTIFF, polygonisation, geometry repair, DSM/DTM
  heights, the file-name fallback for the three required files, and work areas
  (create, list, edit, delete, activate, isolation between two accounts, rejected
  geometry, precedence of the current area, audit entries).
- `python -m backend.scripts.selfcheck`: all checks pass, including the real checkpoint
  loaded strictly and run through the whole pipeline on a synthetic GeoTIFF.
- `python -m backend.scripts.prepare_data --from <downloads folder>` with the real files.
- `npm test` (22 tests), `npm run lint` and `npm run build`.
- The home, sign-in and sign-up pages, with the 3D globe, rendered in Chrome.
- In development mode, driven in Chrome: adding a work area by clicking its corners on
  the map, by uploading a GeoJSON file in UTM, and from the map view; the measurement
  shown is the server's; the list, the area switcher and the labels.

Not yet run:

- The AI pipeline with the real checkpoint on real drone imagery (no orthoimage is in
  the project; see below).
- GeoPackage export (no test covers it).
- Supabase sign-in, account creation, Google sign-in and saving the government
  surveyor ID against a live Supabase project (no test account was available). The sign-up
  error messages are written from Supabase's documented error codes and are unit-tested
  as such, not observed from a live project.

Run `python -m backend.scripts.selfcheck`, `python -m pytest backend/tests` and
`npm run build` on your machine first. If any of them fails, that is a defect to fix, not
a setup problem to work around.

## Candidate plots

Candidate parcels are open-land regions, which suits farmland; inside a settlement they
say nothing about plots. For that, every processing job also gets **candidate plots**,
made by morphological tessellation (Fleischmann et al., 2020, a published proxy for plots
where no cadastre exists):

- every Building feature of at least `PLOT_MIN_BUILDING_M2` (5 m²) is a seed;
- the land to divide is valid image area that is not Road or Water, within
  `PLOT_LIMIT_M` (25 m) of a building;
- each piece of land goes to its nearest building, growing on a `PLOT_GRID_M` (10 cm) grid
  through land only, so roads and water are walls even where the detected network does not
  close; each plot is then cut exactly to its building's limit, has the road and water
  polygons subtracted, and is measured in the local UTM zone like every other feature.

It runs on a completed job's outputs and never changes the job's segmentation, features or
measurements. It runs automatically after each new job, and for older jobs from the job's
result on the Processing page ("Build candidate plots"), `POST
/api/processing/{job_id}/plots`, or `python -m backend.scripts.build_plots <job id>`.

Each plot records its building's feature id, area, perimeter, the building area inside it
and the coverage ratio, the detected buildings inside it counted two ways
(`buildings_inside_seed_rule`: buildings of at least 5 m², the rule that gives a building
a plot; `buildings_inside_all`: every detected building feature), road access and distance,
its building's mean model confidence, and `delineation_method:
"morphological_tessellation"`. It is labelled **Candidate plot**, AI GENERATED /
PRELIMINARY, and starts as review required with the reason "Boundary proposed by
geometric subdivision around a detected building; not observed in imagery". A plot whose
building covers under 5 % of it is also flagged "Building covers under 5% of this plot; the
building or the plot may not be real" (flagged only, never removed). It has its own
map layer, count in the key figures and analytics, export layer, and the same review
actions as other features. When the area has an existing GIS layer, Analytics compares the
plots with it: plots holding exactly one, several or no reference features, and reference
features with a plot to themselves.

What it gets wrong, measured on the full Uplarshi image (job `JOB-2FEF95C563`, village
model): 170 plots from 170 buildings, median 252 m² (10th to 90th percentile 46 to
1,093 m²).

- **It cannot split what the model merged.** The largest plot (2.02 ha) belongs to one
  "building" of 1.10 ha, a merged cluster of the village core's roofs with 20 detected
  buildings inside. Any plot is only as good as the building outlines it starts from.
- **Plots around uncertain small buildings are mostly farmland.** 36 plots are under 5 %
  built (all flagged); their buildings have median confidence 0.52 (0.67 for all plots), so
  many are probably not buildings, and the plot is the 25 m of field around them.
- **Plots holding exactly one building, counted two ways:**
  - counting buildings of at least 5 m² (the rule that seeds a plot): **100 % (170 of
    170)**. This is true by construction and says nothing about quality: every such
    building gets its own plot, so no plot can hold two of them;
  - counting every detected building feature: **58 % (98 of 170)**. Every extra building
    counted inside a plot (outside the merged one) is under 5 m², median 1.8 m², with model
    confidence around 0.4: specks that get no plot of their own, mostly not houses.
- Boundaries between neighbours are equidistant lines, not observed walls or fences; where
  a detected road has a gap, a plot can reach through it.

On the Uplarshi centre crop (`JOB-98528EC34E`): 16 plots, median 68 m²; one building per
plot 100 % counting buildings of at least 5 m², 81 % (13 of 16) counting every detected
building feature; none under 5 % built. Not yet run on urban imagery with the urban model.

## Known limitations

### What the model output supports (measured on the Uplarshi centre crop)

These are properties of what the model produces, measured on job `JOB-98528EC34E`, not
gaps in the code:

- **Road-bounded blocks cannot be derived from this output**, so plots are not made by
  splitting blocks (candidate plots use morphological tessellation instead, which needs no
  closed blocks; see [Candidate plots](#candidate-plots) for what it gets wrong):
  - The detected roads do not form a closed network. The crop's 64 road features remain
    64 separate pieces, and removing them from the crop leaves **one road-bounded block**
    of 10,847 m² (of 11,932 m²). Widening every road by 1 m or 2 m to close gaps still
    leaves one block.
  - **No candidate parcel contains a building.** Candidate parcels are Field regions,
    and Field and Building are separate classes of the segmentation.
  - The model merges neighbouring roofs: the crop has **16 buildings of 5 m² or more**
    (6,853 m²), against **21** in the existing land-cover layer over the same ground, and
    16 % more building area (6,880 m² against 5,955 m²).
- **Most features are ranked High for review.** On the crop, 136 of 194 features (70 %)
  are High, all of them because the feature's mean model confidence is below 0.60
  (range 0.34 to 0.60, median 0.46); 111 also have high entropy. 84 of the 136 are under
  5 m², pieces made mostly of class-boundary pixels where the model is least sure. Mean
  confidence is 0.74 per pixel and 0.54 per feature. The thresholds are unchanged
  starting values, not calibrated against ground truth, so the ranking orders the
  review but is not a measure of error.

- **18 fragments under 1 m² survive the sieve on the full image** (1.4 % of features): islands of valid pixels at the image's transparent margin with no neighbour large enough to merge into; a fix (stepped sieve) is kept on branch `stepped-sieve`, not in the submitted pipeline.

### Data that does not exist yet

These follow from data that does not exist yet, not from missing code:

- **The orthoimage is not in git.** The Uplarshi orthomosaic (`uplarshi.tif`, 1.09 GB,
  Web Mercator, 2.6 cm ground pixels) is supplied locally in `backend/data/imagery/`.
  The measurements below come from a 4096 x 4096 px crop over the village centre
  (`uplarshi_centre_4096.tif`, 1.19 ha), processed with the current pipeline.
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
