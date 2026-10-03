import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ConfidenceHistogram, Stat } from "../../components/AnalyticsPanel";
import DisclaimerBanner from "../../components/DisclaimerBanner";
import Icon from "../../components/Icon";
import ProcessingPipeline from "../../components/ProcessingPipeline";
import { EmptyState, ErrorState, LoadingState, Unavailable } from "../../components/States";
import { Tag } from "../../components/StatusBadge";
import { useWorkspace } from "../../context/WorkspaceContext";
import {
  errorMessage,
  getJob,
  getJobs,
  getPipelineStages,
  startProcessing,
  uploadGeoTIFF,
} from "../../lib/api";
import { formatArea, formatBytes, formatDate, formatNumber, formatPercent } from "../../lib/format";

const ACTIVE = ["QUEUED", "PROCESSING"];
const STATUS_TONE = { UPLOADED: "info", QUEUED: "info", PROCESSING: "info", COMPLETED: "verified", FAILED: "flagged" };

function RasterFacts({ meta }) {
  if (!meta) return null;
  const gsd = meta.resolution_m ? Math.max(...meta.resolution_m) : null;
  return (
    <dl className="kv">
      <dt>Size</dt>
      <dd>{formatNumber(meta.width)} × {formatNumber(meta.height)} px</dd>
      <dt>Bands</dt>
      <dd>{meta.band_count} ({(meta.dtypes || []).join(", ")})</dd>
      <dt>CRS</dt>
      <dd>{meta.crs || <Unavailable>None</Unavailable>}</dd>
      <dt>Pixel size</dt>
      <dd>{gsd != null ? (gsd < 1 ? `${formatNumber(gsd * 100, 1)} cm` : `${formatNumber(gsd, 2)} m`) : <Unavailable />}</dd>
      <dt>NoData</dt>
      <dd>{meta.alpha_band ? "Alpha band" : meta.nodata != null ? String(meta.nodata) : "Not declared"}</dd>
      <dt>File size</dt>
      <dd>{formatBytes(meta.size_bytes)}</dd>
    </dl>
  );
}

function Summary({ job, onOpen }) {
  const summary = job.summary;
  if (!summary) return null;
  const segmentation = summary.segmentation || {};
  const classes = Object.entries(summary.classes || {});
  return (
    <section className="panel">
      <div className="panel__head">
        <h3>Result</h3>
        <Tag tone="ai" icon="cpu">AI generated · Preliminary</Tag>
      </div>
      <div className="panel__body stack">
        <div className="stat-row">
          <Stat label="AI features" value={formatNumber(summary.feature_count)} />
          <Stat label="Candidate parcels" value={formatNumber(summary.candidate_parcel_count)} />
          <Stat label="Mean confidence" value={formatNumber(segmentation.mean_confidence, 3)} note="max softmax probability" />
          <Stat label="Mean entropy" value={formatNumber(segmentation.mean_entropy, 3)} note="nats, of a possible 1.792" />
          <Stat
            label="Review required"
            value={formatNumber((summary.priority_counts?.High || 0) + (summary.priority_counts?.Medium || 0))}
            note={`${formatNumber(summary.priority_counts?.High || 0)} high priority`}
          />
        </div>

        <div className="grid grid--2">
          <div className="stack">
            <h4>Features by class</h4>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Class</th>
                    <th className="num">Features</th>
                    <th className="num">Area</th>
                    <th className="num">Confidence</th>
                    <th className="num">Entropy</th>
                  </tr>
                </thead>
                <tbody>
                  {classes.map(([name, entry]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td className="num">{formatNumber(entry.features)}</td>
                      <td className="num">{formatArea(entry.area_m2)}</td>
                      <td className="num">{formatNumber(entry.mean_confidence, 2)}</td>
                      <td className="num">{formatNumber(entry.mean_entropy, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="stack">
            <h4>Per-pixel confidence</h4>
            <ConfidenceHistogram histogram={segmentation.confidence_histogram} />
            <dl className="kv">
              <dt>Tiles</dt>
              <dd>{formatNumber(segmentation.tiles)} ({formatNumber(segmentation.skipped_tiles)} empty)</dd>
              <dt>Valid pixels</dt>
              <dd>{formatPercent(segmentation.valid_pixels / segmentation.total_pixels)}</dd>
              <dt>Geometry repaired</dt>
              <dd>{formatNumber(summary.topology?.repaired)}</dd>
              <dt>Measured in</dt>
              <dd>{summary.metric_crs}</dd>
              <dt>Device</dt>
              <dd>{summary.device}</dd>
              <dt>Run time</dt>
              <dd>{formatNumber(summary.elapsed_seconds, 1)} s</dd>
            </dl>
          </div>
        </div>

        {summary.warnings?.length ? (
          <div className="notice notice--warn">
            <Icon name="alert" size={16} />
            <ul className="reason-list" style={{ paddingLeft: 16 }}>
              {summary.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="row">
          <button type="button" className="btn btn--primary" onClick={() => onOpen(job, "/map")}>
            <Icon name="map" size={16} /> Open on the map
          </button>
          <button type="button" className="btn btn--secondary" onClick={() => onOpen(job, "/survey/review")}>
            <Icon name="check" size={16} /> Review these features
          </button>
        </div>
      </div>
    </section>
  );
}

export default function Processing() {
  const { system, refreshSystem, setSource } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const fileInput = useRef(null);
  const [pollKey, setPollKey] = useState(0);

  const [jobs, setJobs] = useState(null);
  const [job, setJob] = useState(null);
  const [stageList, setStageList] = useState([]);
  const [listError, setListError] = useState("");
  const [upload, setUpload] = useState({ busy: false, progress: 0, error: "" });
  const [actionError, setActionError] = useState("");
  const [dragging, setDragging] = useState(false);

  const jobId = params.get("job");
  const model = system?.model;
  const modelReady = Boolean(model?.checkpoint_present && model?.runtime_available);

  const loadJobs = useCallback(async () => {
    try {
      const data = await getJobs();
      setJobs(data.jobs);
      setListError("");
      return data.jobs;
    } catch (error) {
      setListError(errorMessage(error, "Processing jobs could not be listed."));
      return [];
    }
  }, []);

  useEffect(() => {
    loadJobs();
    getPipelineStages()
      .then((data) => setStageList(data.stages))
      .catch(() => {});
  }, [loadJobs]);

  // Follow the selected job. While it is queued or running its record is
  // re-read; what is shown is always what the server has stored.
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return undefined;
    }
    let active = true;
    let timer = null;

    const poll = async () => {
      try {
        const data = await getJob(jobId);
        if (!active) return;
        setJob(data);
        setActionError("");
        if (ACTIVE.includes(data.status)) {
          timer = window.setTimeout(poll, 1500);
        } else {
          loadJobs();
          if (data.status === "COMPLETED") refreshSystem();
        }
      } catch (error) {
        if (active) setActionError(errorMessage(error, "The job could not be read."));
      }
    };
    poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [jobId, pollKey, loadJobs, refreshSystem]);

  const selectJob = (id) => setParams(id ? { job: id } : {});

  async function handleFiles(files) {
    const file = files?.[0];
    if (!file) return;
    if (!/\.tiff?$/i.test(file.name)) {
      setUpload({ busy: false, progress: 0, error: "Choose a GeoTIFF file (.tif or .tiff)." });
      return;
    }
    setUpload({ busy: true, progress: 0, error: "" });
    try {
      const created = await uploadGeoTIFF(file, (fraction) =>
        setUpload((current) => ({ ...current, progress: fraction }))
      );
      setUpload({ busy: false, progress: 1, error: "" });
      await loadJobs();
      selectJob(created.job_id);
    } catch (error) {
      setUpload({ busy: false, progress: 0, error: errorMessage(error, "The upload failed.") });
    }
  }

  async function handleStart() {
    setActionError("");
    try {
      const started = await startProcessing(job.job_id);
      setJob(started);
      setPollKey((value) => value + 1); // follow the job again
    } catch (error) {
      setActionError(errorMessage(error, "The job could not be started."));
    }
  }

  function openResult(finished, path) {
    setSource(`job:${finished.job_id}`);
    navigate(path);
  }

  const stages = job?.stages?.length
    ? job.stages
    : stageList.map((stage) => ({ ...stage, status: "pending", fraction: 0, detail: null }));

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>AI processing</h1>
          <p>
            Upload a georeferenced drone orthoimage and run the segmentation model on it. Each stage
            below reports what the server actually did.
          </p>
        </div>
      </div>

      <div className="grid grid--sidebar">
        <div className="stack" style={{ gap: 16 }}>
          <section className="panel">
            <div className="panel__head">
              <h3>Imagery</h3>
            </div>
            <div className="panel__body stack">
              <div
                className={`dropzone ${dragging ? "is-over" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  handleFiles(event.dataTransfer.files);
                }}
              >
                <Icon name="upload" size={26} />
                <p>
                  <strong>Drop a GeoTIFF here</strong>, or choose a file.
                </p>
                <p className="field__hint">
                  8-bit RGB, georeferenced, north-up. Up to {formatNumber(system?.pipeline?.max_upload_mb)} MB.
                </p>
                <input
                  ref={fileInput}
                  type="file"
                  className="sr-only"
                  accept=".tif,.tiff,image/tiff"
                  onChange={(event) => {
                    handleFiles(event.target.files);
                    event.target.value = "";
                  }}
                  aria-label="Choose a GeoTIFF"
                />
                <button
                  type="button"
                  className="btn btn--secondary"
                  disabled={upload.busy}
                  onClick={() => fileInput.current?.click()}
                >
                  {upload.busy ? `Uploading ${Math.round(upload.progress * 100)}%` : "Choose a GeoTIFF"}
                </button>
                {upload.busy ? (
                  <div className="progress" style={{ width: "min(360px, 100%)" }} aria-hidden="true">
                    <div className="progress__fill" style={{ width: `${upload.progress * 100}%` }} />
                  </div>
                ) : null}
              </div>

              {upload.error ? (
                <div className="notice notice--error" role="alert">
                  <Icon name="alert" size={16} />
                  <p>{upload.error}</p>
                </div>
              ) : null}

              <p className="field__hint">
                Imagery already on the server can be started from <Link to="/survey/datasets">Datasets</Link>.
                The model was trained on SVAMITVA drone imagery; on other imagery its results vary and
                need closer review.
              </p>
            </div>
          </section>

          {job ? (
            <section className="panel">
              <div className="panel__head">
                <div>
                  <h3>{job.input_dataset}</h3>
                  <p className="muted mono" style={{ marginTop: 2 }}>{job.job_id}</p>
                </div>
                <Tag tone={STATUS_TONE[job.status] || "muted"}>{job.status_label}</Tag>
              </div>
              <div className="panel__body stack" style={{ gap: 16 }}>
                <RasterFacts meta={job.raster_meta} />

                {job.raster_meta?.warnings?.length ? (
                  <div className="notice notice--warn">
                    <Icon name="alert" size={16} />
                    <ul className="reason-list" style={{ paddingLeft: 16 }}>
                      {job.raster_meta.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {job.status === "FAILED" && job.error ? (
                  <div className="notice notice--error" role="alert">
                    <Icon name="alert" size={16} />
                    <p>
                      <strong>Processing failed.</strong> {job.error}
                    </p>
                  </div>
                ) : null}
                {actionError ? (
                  <div className="notice notice--error" role="alert">
                    <Icon name="alert" size={16} />
                    <p>{actionError}</p>
                  </div>
                ) : null}

                {["UPLOADED", "FAILED"].includes(job.status) ? (
                  <div className="row">
                    <button type="button" className="btn btn--primary" disabled={!modelReady} onClick={handleStart}>
                      <Icon name="cpu" size={16} />
                      {job.status === "FAILED" ? "Start again" : "Start processing"}
                    </button>
                    {!modelReady ? (
                      <span className="field__error">
                        The model is not ready on this server, so processing cannot start.
                      </span>
                    ) : null}
                  </div>
                ) : null}

                <ProcessingPipeline stages={stages} progress={job.progress} status={job.status} />
              </div>
            </section>
          ) : (
            <section className="panel">
              <div className="panel__head">
                <h3>Pipeline</h3>
              </div>
              <div className="panel__body stack">
                <EmptyState
                  compact
                  icon="satellite"
                  title="Awaiting dataset"
                  detail="AI processing needs a compatible GeoTIFF. Upload one, or choose a job on the right, to see it run through these stages."
                />
                <ProcessingPipeline stages={stages} progress={0} />
              </div>
            </section>
          )}

          {job?.status === "COMPLETED" ? <Summary job={job} onOpen={openResult} /> : null}
        </div>

        <div className="stack" style={{ gap: 16 }}>
          <section className="panel">
            <div className="panel__head">
              <h3>Model</h3>
              {model ? (
                modelReady ? (
                  <Tag tone="verified" icon="check">Ready</Tag>
                ) : (
                  <Tag tone="flagged" icon="alert">Not ready</Tag>
                )
              ) : null}
            </div>
            <div className="panel__body stack">
              {model ? (
                <>
                  <dl className="kv">
                    <dt>Architecture</dt>
                    <dd>{model.architecture} / {model.encoder}</dd>
                    <dt>Input</dt>
                    <dd>{model.input}</dd>
                    <dt>Classes</dt>
                    <dd>{model.classes.length}</dd>
                    <dt>Checkpoint</dt>
                    <dd>
                      {model.checkpoint_present
                        ? `${model.checkpoint_file} (${formatBytes(model.checkpoint_bytes)})`
                        : <Unavailable>Not found</Unavailable>}
                    </dd>
                    <dt>Normalisation</dt>
                    <dd>{model.normalization === "scale_255" ? "x / 255" : "ImageNet mean / std"}</dd>
                    <dt>Tile size</dt>
                    <dd>{system.pipeline.tile_size} px, {system.pipeline.tile_overlap} px overlap</dd>
                    <dt>Device</dt>
                    <dd>{model.device || "Chosen when the model loads"}</dd>
                  </dl>
                  {!model.checkpoint_present ? (
                    <p className="field__error">
                      {model.checkpoint_file_status?.message ||
                        `Copy ${model.checkpoint_file} into backend/models on the server.`}
                    </p>
                  ) : model.checkpoint_file_status?.status === "fallback" ? (
                    <p className="field__hint">{model.checkpoint_file_status.message}</p>
                  ) : null}
                  {model.runtime_error ? <p className="field__error">{model.runtime_error}</p> : null}
                  <p className="uncertainty__note">{model.generalisation_note}</p>
                </>
              ) : (
                <LoadingState compact label="Checking the model" />
              )}
            </div>
          </section>

          <section className="panel panel--flush">
            <div className="panel__head">
              <h3>Jobs</h3>
              <button type="button" className="icon-btn" onClick={loadJobs} aria-label="Refresh jobs" title="Refresh">
                <Icon name="refresh" size={16} />
              </button>
            </div>
            <div className="panel__body">
              {listError ? (
                <ErrorState compact title="Jobs unavailable" detail={listError} onRetry={loadJobs} />
              ) : jobs == null ? (
                <LoadingState compact label="Loading jobs" />
              ) : jobs.length === 0 ? (
                <EmptyState compact icon="clock" title="No jobs yet" detail="Jobs you run are kept here, with their results." />
              ) : (
                <ul className="queue">
                  {jobs.map((item) => (
                    <li key={item.job_id}>
                      <button
                        type="button"
                        className={`queue__item ${item.job_id === jobId ? "is-selected" : ""}`}
                        onClick={() => selectJob(item.job_id)}
                      >
                        <span className="queue__id">{item.job_id}</span>
                        <Tag tone={STATUS_TONE[item.status] || "muted"}>{item.status_label}</Tag>
                        <span className="queue__issue">{item.input_dataset}</span>
                        <span className="queue__meta">
                          <span>{formatDate(item.created_at, true)}</span>
                          {ACTIVE.includes(item.status) ? <span>{formatNumber(item.progress, 0)}%</span> : null}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      </div>

      <DisclaimerBanner />
    </main>
  );
}
