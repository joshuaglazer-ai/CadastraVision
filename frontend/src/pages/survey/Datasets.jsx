import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import DatasetCard from "../../components/DatasetCard";
import Icon from "../../components/Icon";
import { ErrorState, LoadingState } from "../../components/States";
import { Tag } from "../../components/StatusBadge";
import { createJobFromDataset, errorMessage, getDatasets, uploadDataset } from "../../lib/api";

const STATUS_TONE = { AVAILABLE: "verified", "REQUIRES REVIEW": "review", "NOT AVAILABLE": "muted" };
const KIND_ICON = { raster: "satellite", vector: "shapes", points: "crosshair", document: "file", mixed: "layers" };

function Category({ category, onUploaded, onProcess }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState(null);

  async function handleFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setProgress(0);
    setMessage(null);
    try {
      const dataset = await uploadDataset(category.key, file, setProgress);
      setMessage({ tone: "ok", text: `${dataset.name} added to ${category.label}.` });
      onUploaded();
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, "The file could not be uploaded.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel__head">
        <div className="row" style={{ gap: 10, flexWrap: "nowrap", alignItems: "flex-start" }}>
          <Icon name={KIND_ICON[category.kind] || "database"} size={20} />
          <div>
            <h3>{category.label}</h3>
            <p className="muted" style={{ fontSize: "0.9rem", marginTop: 2, maxWidth: "70ch" }}>
              {category.description}
            </p>
          </div>
        </div>
        <Tag tone={STATUS_TONE[category.status] || "muted"}>
          {category.status === "NOT AVAILABLE" ? "not available" : category.status.toLowerCase()}
        </Tag>
      </div>

      <div className="panel__body dataset-category">
        {category.datasets.length ? (
          <div className="grid grid--3">
            {category.datasets.map((dataset) => (
              <DatasetCard
                key={dataset.dataset_id}
                dataset={dataset}
                action={
                  ["drone", "satellite"].includes(dataset.source_type) &&
                  dataset.status !== "REQUIRES REVIEW" &&
                  dataset.origin === "Local file" ? (
                    <button type="button" className="btn btn--secondary btn--sm" onClick={() => onProcess(dataset)}>
                      <Icon name="cpu" size={15} /> Process with the model
                    </button>
                  ) : null
                }
              />
            ))}
          </div>
        ) : (
          <div className="dataset-empty">
            <Icon name="info" size={18} />
            <span>{category.empty_message}. Nothing has been provided for this source yet.</span>
          </div>
        )}

        {category.notes?.map((note) => (
          <div className="notice notice--error" key={note} role="alert">
            <Icon name="alert" size={16} />
            <p>{note}</p>
          </div>
        ))}

        {message ? (
          <div className={`notice ${message.tone === "ok" ? "notice--ok" : "notice--error"}`} role={message.tone === "ok" ? "status" : "alert"}>
            <Icon name={message.tone === "ok" ? "check" : "alert"} size={16} />
            <p>{message.text}</p>
          </div>
        ) : null}

        {category.upload_allowed ? (
          <div className="row">
            <input
              ref={input}
              type="file"
              className="sr-only"
              accept={category.accepted_extensions.join(",")}
              onChange={handleFile}
              aria-label={`Upload to ${category.label}`}
            />
            <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => input.current?.click()}>
              <Icon name="upload" size={15} />
              {busy ? `Uploading ${Math.round(progress * 100)}%` : "Add a file"}
            </button>
            <span className="field__hint">Accepts {category.accepted_extensions.join(", ")}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default function Datasets() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [state, setState] = useState({ loading: true, error: "" });
  const [actionError, setActionError] = useState("");

  const load = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      setData(await getDatasets());
      setState({ loading: false, error: "" });
    } catch (error) {
      setState({ loading: false, error: errorMessage(error, "The datasets could not be listed.") });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function process(dataset) {
    setActionError("");
    try {
      const job = await createJobFromDataset(dataset.dataset_id);
      navigate(`/survey/processing?job=${encodeURIComponent(job.job_id)}`);
    } catch (error) {
      setActionError(errorMessage(error, "A processing job could not be created for this dataset."));
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Datasets</h1>
          <p>
            Every data source for this assignment, as found on the server. A source with nothing in it is
            shown as not available.
          </p>
        </div>
        <div className="page-head__actions">
          <button type="button" className="btn btn--secondary" onClick={load}>
            <Icon name="refresh" size={16} /> Check again
          </button>
        </div>
      </div>

      {actionError ? (
        <div className="notice notice--error" role="alert">
          <Icon name="alert" size={16} />
          <p>{actionError}</p>
        </div>
      ) : null}

      {state.error ? (
        <div className="panel">
          <ErrorState title="Datasets unavailable" detail={state.error} onRetry={load} />
        </div>
      ) : !data ? (
        <LoadingState label="Looking for datasets" />
      ) : (
        <>
          <div className="notice">
            <Icon name="database" size={18} />
            <p>
              <strong>
                {data.summary.available_categories} of {data.summary.total_categories} source types
              </strong>{" "}
              have data, {data.summary.total_datasets} dataset{data.summary.total_datasets === 1 ? "" : "s"} in
              total.
            </p>
          </div>

          {data.categories.map((category) => (
            <Category key={category.key} category={category} onUploaded={load} onProcess={process} />
          ))}

          <section className="panel">
            <div className="panel__head">
              <h3>Online basemaps</h3>
              <Tag tone="info">external service</Tag>
            </div>
            <div className="panel__body">
              <ul className="reason-list">
                {data.basemaps.map((basemap) => (
                  <li key={basemap.name}>
                    <strong style={{ color: "var(--foam)" }}>{basemap.name}</strong>, {basemap.type.toLowerCase()}. {basemap.note}
                  </li>
                ))}
              </ul>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
