import { useEffect, useState } from "react";

import DisclaimerBanner from "../../components/DisclaimerBanner";
import Icon from "../../components/Icon";
import { Unavailable } from "../../components/States";
import { useWorkspace } from "../../context/WorkspaceContext";
import { blobErrorMessage, downloadExport, errorMessage, getExportSummary } from "../../lib/api";
import { formatDate, formatNumber } from "../../lib/format";

const LAYERS = [
  { key: "parcels", label: "Candidate parcels" },
  { key: "landcover", label: "Land-cover features (buildings, roads, fields, water, other)" },
  { key: "plots", label: "Candidate plots (geometric proposal around each building)" },
];

const FILTERS = [
  { key: "all", label: "All features", note: "Each feature carries its own verification status." },
  { key: "verified", label: "Surveyor verified only", note: "Approved or edited features." },
  { key: "unverified", label: "Not yet verified", note: "AI generated, flagged or rejected features." },
];

const FORMATS = [
  { key: "geojson", label: "GeoJSON", note: "Full-resolution geometry with provenance. Opens in QGIS and ArcGIS." },
  { key: "csv", label: "CSV attributes", note: "The attribute table without geometry." },
  { key: "gpkg", label: "GeoPackage", note: "Needs GeoPandas with GeoPackage support on the server." },
];

export default function Export() {
  const { source } = useWorkspace();
  const [layer, setLayer] = useState("parcels");
  const [status, setStatus] = useState("all");
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState(null);

  useEffect(() => {
    let active = true;
    setSummary(null);
    setSummaryError("");
    getExportSummary(source, layer, status)
      .then((data) => active && setSummary(data))
      .catch((error) => active && setSummaryError(errorMessage(error, "The export could not be prepared.")));
    return () => {
      active = false;
    };
  }, [source, layer, status]);

  async function download(format) {
    setBusy(format);
    setMessage(null);
    try {
      const filename = await downloadExport(format, source, { layer, status });
      setMessage({ tone: "ok", text: `Saved ${filename}.` });
    } catch (error) {
      setMessage({ tone: "error", text: await blobErrorMessage(error) });
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Export</h1>
          <p>
            GIS-ready files with their provenance. Every feature says whether it is AI generated or
            surveyor verified.
          </p>
        </div>
      </div>

      <div className="grid grid--sidebar">
        <section className="panel">
          <div className="panel__head">
            <h3>What to export</h3>
          </div>
          <div className="panel__body stack" style={{ gap: 18 }}>
            <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="field__label" style={{ marginBottom: 8, padding: 0 }}>Layer</legend>
              {LAYERS.map((item) => (
                <label key={item.key} className="check">
                  <input type="radio" name="layer" checked={layer === item.key} onChange={() => setLayer(item.key)} />
                  {item.label}
                </label>
              ))}
            </fieldset>

            <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="field__label" style={{ marginBottom: 8, padding: 0 }}>Features</legend>
              {FILTERS.map((item) => (
                <label key={item.key} className="check" style={{ alignItems: "flex-start" }}>
                  <input type="radio" name="status" checked={status === item.key} onChange={() => setStatus(item.key)} style={{ marginTop: 3 }} />
                  <span>
                    {item.label}
                    <span className="field__hint" style={{ display: "block" }}>{item.note}</span>
                  </span>
                </label>
              ))}
            </fieldset>

            <div className="stack">
              <span className="field__label" style={{ fontSize: "0.86rem", fontWeight: 500, color: "var(--mist)" }}>Format</span>
              {FORMATS.map((format) => (
                <div key={format.key} className="dataset-card" style={{ gridTemplateColumns: "1fr auto", alignItems: "center" }}>
                  <div>
                    <strong>{format.label}</strong>
                    <p className="field__hint">{format.note}</p>
                  </div>
                  <button
                    type="button"
                    className={`btn ${format.key === "geojson" ? "btn--primary" : "btn--secondary"}`}
                    disabled={Boolean(busy) || Boolean(summaryError)}
                    onClick={() => download(format.key)}
                  >
                    <Icon name="download" size={16} />
                    {busy === format.key ? "Preparing" : `Download ${format.label}`}
                  </button>
                </div>
              ))}
            </div>

            {message ? (
              <div className={`notice ${message.tone === "ok" ? "notice--ok" : "notice--error"}`} role={message.tone === "ok" ? "status" : "alert"}>
                <Icon name={message.tone === "ok" ? "check" : "alert"} size={16} />
                <p>{message.text}</p>
              </div>
            ) : null}
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <h3>Provenance written into the file</h3>
          </div>
          <div className="panel__body stack">
            {summaryError ? (
              <div className="notice notice--error" role="alert">
                <Icon name="alert" size={16} />
                <p>{summaryError}</p>
              </div>
            ) : !summary ? (
              <Unavailable>Loading</Unavailable>
            ) : (
              <>
                <dl className="kv">
                  <dt>Record status</dt>
                  <dd>{summary.record_status}</dd>
                  <dt>Project</dt>
                  <dd>{summary.project}</dd>
                  <dt>Assignment</dt>
                  <dd className="mono">{summary.assignment.assignment_id || "None"}</dd>
                  <dt>Village</dt>
                  <dd>{summary.assignment.village || <Unavailable>Not set</Unavailable>}</dd>
                  <dt>Surveyor</dt>
                  <dd className="mono">{summary.surveyor.surveyor_id}</dd>
                  <dt>Processing job</dt>
                  <dd className="mono">{summary.processing_job ? summary.processing_job.job_id : "Existing layers"}</dd>
                  <dt>Model</dt>
                  <dd>{summary.model.name}</dd>
                  <dt>Verified features</dt>
                  <dd>{formatNumber(summary.verification.verified_features)}</dd>
                  <dt>Flagged features</dt>
                  <dd>{formatNumber(summary.verification.flagged_features)}</dd>
                  <dt>CRS</dt>
                  <dd>{summary.crs}</dd>
                  <dt>Time</dt>
                  <dd>{formatDate(summary.exported_at, true)}</dd>
                </dl>
                <p className="uncertainty__note">{summary.legal_notice}</p>
              </>
            )}
          </div>
        </section>
      </div>

      <DisclaimerBanner />
    </main>
  );
}
