# Deployment

## What runs where

| Part | Runs as | Needs |
| --- | --- | --- |
| Backend | `uvicorn backend.main:app` (one process) | Python 3.10+, the packages in `backend/requirements.txt`, the checkpoint, a writable data and processing directory |
| Frontend | Static files from `npm run build` | Any static web server |
| Identity | Supabase Auth | A Supabase project |

Run **one** backend process. Processing jobs run on a worker thread inside it and state
is in SQLite; several processes would each run their own queue. A GPU is used
automatically when the CUDA build of PyTorch is installed.

## Backend

```bash
python -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # or the CUDA build
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
python -m backend.scripts.selfcheck
python -m backend.scripts.prepare_data        # optional: build the layer index now
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Set in `backend/.env` for production:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<anon key>
CADASTRA_AUTH=supabase
CADASTRA_ALLOW_DEMO_ASSIGNMENT=false
FRONTEND_URL=https://<your frontend origin>
```

Leave `CADASTRA_AUTH=off` for a developer's own machine only. The server logs a warning
at start when it is set.

### Docker

```bash
docker build -f backend/Dockerfile -t cadastra-vision-api .
docker run -p 8000:8000 --env-file backend/.env \
  -v /srv/cadastra/models:/app/backend/models \
  -v /srv/cadastra/data:/app/backend/data \
  -v /srv/cadastra/processing:/app/backend/processing \
  cadastra-vision-api
```

The checkpoint and the data are mounted, not built into the image. The image has not
been built in the development environment; treat the Dockerfile as untested until you
have built it once.

### Directories to keep

| Directory | Contents | Back up |
| --- | --- | --- |
| `backend/models/` | Checkpoint | Keep a copy |
| `backend/data/<category>/` | Datasets | Yes |
| `backend/data/surveyors/` | Assignment registry | Yes |
| `backend/data/state/cadastra.db` | Jobs, reviews, audit trail | **Yes** |
| `backend/data/cache/` | Layer index | No, it is rebuilt |
| `backend/processing/` | Uploaded rasters and job outputs | Outputs yes |

## Frontend

```bash
cd frontend
npm install
cp .env.example .env      # VITE_API_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
npm run build             # output in frontend/dist
```

Serve `frontend/dist` from any static host. The application uses client-side routes, so
configure the host to return `index.html` for unknown paths.

The basemaps are public tile services (Esri World Imagery and OpenStreetMap). Check
their terms for your use, or choose "No basemap", which draws only project data.

## Supabase

1. Create a project. Under Authentication → Providers keep **Email** enabled.
2. Create the surveyors' users (Authentication → Users), or allow sign-up.
3. Under Authentication → URL Configuration set the site URL to the frontend origin and
   add `<frontend origin>/reset-password` to the redirect URLs. The forgot-password
   e-mail links there.
4. Put the project URL and the **anon** key in `backend/.env` and `frontend/.env`.

The service-role key is not needed and must not be placed in either file.

## Assignments

`backend/data/surveyors/surveyor_assignments.geojson` is a GeoJSON FeatureCollection in
longitude/latitude (WGS 84), one feature per assignment:

```json
{
  "type": "Feature",
  "properties": {
    "assignment_id": "ASGN-2026-014",
    "assigned_to": ["surveyor@agency.gov.in"],
    "surveyor_id": "SRV-0412",
    "surveyor_name": "Name as it should appear",
    "designation": "Surveyor",
    "department": "Department name",
    "assignment_status": "active",
    "assigned_date": "2026-09-15",
    "state": "Uttar Pradesh",
    "district": "Gautam Buddh Nagar",
    "taluk": "Dadri",
    "village": "Uplarshi",
    "declared_area_ha": 17.8,
    "boundary_source": "Where this boundary came from"
  },
  "geometry": { "type": "Polygon", "coordinates": [] }
}
```

- `assigned_to` holds the sign-in e-mail addresses that hold the assignment.
- The geometry is the assigned boundary. Its area is measured by the server;
  `declared_area_ha` is compared with it and a mismatch is reported.
- A feature with `"is_demo": true` is offered to users who have no assignment when
  `CADASTRA_ALLOW_DEMO_ASSIGNMENT=true`, and is labelled DEMO.

The file shipped in the repository contains a single demo entry with a placeholder
rectangle. Replace it with real assignments before field use.

## Adding datasets

Copy files into the category directory, or upload them on the Datasets page.

| Category | Directory | Formats | Used for |
| --- | --- | --- | --- |
| Drone / ORI imagery | `data/imagery/` | GeoTIFF | AI processing |
| Satellite imagery | `data/satellite/` | GeoTIFF | AI processing (with a resolution warning) |
| DSM, DTM | `data/dsm/`, `data/dtm/` | GeoTIFF | Terrain and measured building height |
| Existing maps and land records | `data/land_records/` | GeoJSON | Existing GIS layer and parcel reasoning |
| GIS reference data | `data/gis/` | GeoJSON | Reference layer |
| Survey of India data | `data/survey_of_india/` | GeoJSON, GeoTIFF | Reference layer |
| Owner documents | `data/documents/` | PDF, images, Word | Kept for the surveyor; not read by the AI |
| GNSS / ground truth | `data/gnss/` | CSV (`latitude`, `longitude` columns), GeoJSON | Points on the map |

GeoJSON layers must be a FeatureCollection. A layer without a `crs` member is read as
longitude/latitude; a named CRS is honoured and reprojected. Reference layers larger than
25 MB are listed but not drawn. Shapefile, KML and GeoPackage uploads are stored and
listed, but only GeoJSON is drawn on the map.

## Checks after deployment

1. `GET /health` answers `{"status": "ok"}`.
2. `GET /api/system/status` (signed in) shows `checkpoint_present: true`,
   `runtime_available: true` and no `indexing_errors`.
3. Upload a small GeoTIFF on the Processing page and follow it to "Ready for review".
4. Approve one feature and confirm it appears under Verified and in the audit trail.
5. Export GeoJSON and confirm the metadata and per-feature status.
