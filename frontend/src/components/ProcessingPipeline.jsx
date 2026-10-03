import Icon from "./Icon";

const STATE_LABEL = { pending: "Waiting", running: "Running", done: "Done", failed: "Failed" };

/**
 * The pipeline stages of one job, exactly as the backend reports them.
 * Progress is whatever the job record says; nothing here animates on a
 * timer.
 */
export default function ProcessingPipeline({ stages, progress, status }) {
  if (!stages?.length) return null;
  const percent = Math.max(0, Math.min(100, Number(progress) || 0));

  return (
    <div className="stack">
      <div>
        <div className="row row--between" style={{ marginBottom: 6 }}>
          <span className="muted">Overall progress</span>
          <strong>{percent.toFixed(percent < 100 && percent > 0 ? 1 : 0)}%</strong>
        </div>
        <div
          className="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
          aria-label="Processing progress"
        >
          <div className="progress__fill" style={{ width: `${percent}%` }} />
        </div>
      </div>

      <ol className="pipeline">
        {stages.map((stage, index) => {
          const state = stage.status || "pending";
          return (
            <li key={stage.key} className={`pipeline__stage pipeline__stage--${state}`}>
              <span className="pipeline__dot" aria-hidden="true">
                {state === "done" ? (
                  <Icon name="check" size={14} strokeWidth={2.4} />
                ) : state === "failed" ? (
                  <Icon name="x" size={14} strokeWidth={2.4} />
                ) : state === "running" ? (
                  <span className="spinner" style={{ width: 13, height: 13, margin: 0 }} />
                ) : (
                  index + 1
                )}
              </span>
              <span className="pipeline__label">{stage.label}</span>
              <span className="pipeline__state">
                {STATE_LABEL[state] || state}
                {state === "running" && stage.fraction > 0 ? ` · ${Math.round(stage.fraction * 100)}%` : ""}
              </span>
              {stage.detail ? <span className="pipeline__detail">{stage.detail}</span> : null}
              {state === "running" && stage.fraction > 0 ? (
                <div className="progress pipeline__bar" aria-hidden="true">
                  <div className="progress__fill" style={{ width: `${Math.round(stage.fraction * 100)}%` }} />
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {status === "FAILED" ? (
        <p className="field__hint">Fix the cause shown above, then start the job again.</p>
      ) : null}
    </div>
  );
}
