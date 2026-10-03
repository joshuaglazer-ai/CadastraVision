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
python -m backend.scripts.prepare_data --from ~/Downloads   # copy the three required files in
python -m backend.scripts.selfcheck
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### The three required files

| File | Location |
| --- | --- |
| `best_weighted_multiclass_unet.pth` | `backend/models/` (or the path in `CADASTRA_MODEL_PATH`) |
| `candidate_parcels.geojson` | `backend/data/parcels/` |
| `uplarshi_landcover_with_attributes.geojson` | `backend/data/landcover/` |

`python -m backend.scripts.prepare_data --from <folder>` finds them in a folder such as
your downloads, including browser download names like `candidate_parcels (2).geojson`
or `best_weighted_multiclass_unet(2).pth`, copies them to the locations above under the
clean names, then builds the layer index. It prints each file it copied, left unchanged
or could not find, and exits with status 1 if any is missing. When the folder holds
several differing copies and none under the clean name, it copies none of them and lists
them; byte-identical copies count as one. Without `--from` it only builds the index.

If a file is placed by hand under another name, the backend still uses it when it is the
only `.geojson` (or `.pth`) in its folder, and says so on the Datasets page and in
`/api/system/status`. With several candidates in a folder it uses none of them and the
layer control, the Datasets page and `/api/system/status` name the files involved.
Rename the right one to the expected name.

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
| `backend/data/state/cadastra.db` | Jobs, reviews, audit trail, work areas | **Yes** |
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
2. Under Authentication → Sign In / Providers leave **Allow new users to sign up**
   switched on so surveyors can create their own accounts on `/signup`. (Switched off,
   the page shows "sign-up is switched off" and accounts must be created by an
   administrator under Authentication → Users.)
3. **Confirm email** decides what happens after sign-up. On (recommended): the page
   says "Check your e-mail to confirm your account" and the surveyor signs in after
   opening the link. Off: the new account is signed in and taken to the dashboard
   straight away.
4. Under Authentication → URL Configuration set the site URL to the frontend origin and
   add both `<frontend origin>/login` (the sign-up confirmation link returns there) and
   `<frontend origin>/reset-password` (the forgot-password link) to the redirect URLs.
5. Put the project URL and the **anon** key in `backend/.env` and `frontend/.env`.

The service-role key is not needed and must not be placed in either file.

Supabase's built-in e-mail sender is rate-limited to a few messages an hour and is meant
for testing. For more than a handful of surveyors, configure your own SMTP server under
Authentication → Emails.

### Sign in with Google

The "Continue with Google" button works only once the Google provider is set up:

1. In Google Cloud Console → APIs & Services → Credentials, create an **OAuth client ID**
   of type *Web application*. Under *Authorised redirect URIs* add
   `https://<project>.supabase.co/auth/v1/callback` (Supabase shows the exact value on
   its Google provider page).
2. In Supabase → Authentication → Sign In / Providers → **Google**, switch it on and
   paste the client ID and client secret.
3. Keep `<frontend origin>/login` in the redirect URLs (step 4 above); Google sign-in
   returns there.

Until then the button answers "Google sign-in is not enabled for this installation".
The Google client secret belongs in Supabase only, never in either `.env` file.

### Government surveyor ID

Every account must enter a government surveyor ID before the workspace opens: on the
sign-up form, or, after a first Google sign-in, on a one-time "Complete your surveyor
profile" page. It is stored in the account's Supabase profile (`user_metadata`), which
the user can change. Nothing checks it against a government register, so the
application shows it as **self-declared** wherever it appears. If your department needs
verified IDs, verify them outside the application (for example when adding the account
to the assignment registry).

### New accounts and work areas

Creating an account gives a surveyor a sign-in, not an assignment. A surveyor can then
add **work areas** of their own on the Survey workspace page: draw the boundary on the
map, upload it as GeoJSON, or take the current map view as a rough rectangle. These are
stored in the state database and labelled **SELF-DECLARED WORK AREA** everywhere,
including exports. They are not official assignments.

Official assignments still come only from the registry: an administrator adds the
e-mail address the surveyor signed in with to `assigned_to` of the right feature in
`backend/data/surveyors/surveyor_assignments.geojson` (see [Assignments](#assignments)).
The registry is read on every request, so no restart is needed; the surveyor sees the
assignment the next time they open or reload the application, and can switch between it
and their work areas. There is no screen that writes the registry.

An account with neither sees the registry's demo assignment, labelled DEMO, when
`CADASTRA_ALLOW_DEMO_ASSIGNMENT=true`. Set it to `false` in production so a new
account starts from "No work area yet. Add one to begin." instead.

Work areas live in `backend/data/state/cadastra.db` with jobs and reviews; back that
file up.

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
