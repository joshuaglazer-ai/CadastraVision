import Icon from "./Icon";

export function LoadingState({ label = "Loading", detail, compact = false }) {
  return (
    <div className={`state state--loading ${compact ? "state--compact" : ""}`} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <div>
        <p className="state__title">{label}</p>
        {detail ? <p className="state__detail">{detail}</p> : null}
      </div>
    </div>
  );
}

export function EmptyState({ icon = "info", title, detail, action, compact = false }) {
  return (
    <div className={`state state--empty ${compact ? "state--compact" : ""}`}>
      <Icon name={icon} size={compact ? 18 : 24} />
      <div>
        <p className="state__title">{title}</p>
        {detail ? <p className="state__detail">{detail}</p> : null}
        {action ? <div className="state__action">{action}</div> : null}
      </div>
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", detail, onRetry, compact = false }) {
  return (
    <div className={`state state--error ${compact ? "state--compact" : ""}`} role="alert">
      <Icon name="alert" size={compact ? 18 : 24} />
      <div>
        <p className="state__title">{title}</p>
        {detail ? <p className="state__detail">{detail}</p> : null}
        {onRetry ? (
          <div className="state__action">
            <button type="button" className="btn btn--secondary btn--sm" onClick={onRetry}>
              <Icon name="refresh" size={15} /> Try again
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Inline value that says so when the data does not exist. */
export function Unavailable({ children = "Data unavailable" }) {
  return <span className="unavailable">{children}</span>;
}
