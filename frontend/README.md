# Cadastra Vision frontend

React 19, Vite, React Router, Leaflet (react-leaflet), Three.js (React Three Fiber and
Drei), supabase-js and axios.

```bash
npm install
cp .env.example .env     # VITE_API_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
npm run dev              # http://localhost:5173
npm test                 # unit tests (Node's built-in runner)
npm run lint
npm run build            # output in dist/
```

Node.js 22 or newer.

Only public values go in `.env`: everything prefixed `VITE_` is shipped to the browser.
Use the Supabase **anon** key, never a service-role key.

`VITE_AUTH_MODE=off` skips sign-in for local development and must be paired with
`CADASTRA_AUTH=off` on the backend. The application then shows a development banner on
every page.

## Layout

```
src/
  App.jsx                 routes; everything under ProtectedLayout needs a session
  context/                AuthContext (Supabase session), WorkspaceContext (surveyor, assignment, source)
  lib/                    api.js (all backend calls), supabase.js, format.js, constants.js, earth.js
  components/             MapWorkbench, MapView, LayerControl, FeaturePanel, ParcelPanel,
                          ReferencePanel, ReviewPanel, ReviewQueue, AuditTrail, ThreeDParcelView,
                          ProcessingPipeline, DatasetCard, AnalyticsPanel, KPISection, Globe, ...
  pages/                  Home, About, Login, ResetPassword, Dashboard, MapPage, Survey and survey/*
  styles/index.css        design tokens and all styles
```

The frontend displays what the API returns. It does not compute counts, areas, confidence
or status, and it has no sample data. When the API has nothing, the screen says
"Data unavailable", "Awaiting dataset" or "Requires surveyor input".

See [../docs/architecture.md](../docs/architecture.md) for how the pieces fit together.
