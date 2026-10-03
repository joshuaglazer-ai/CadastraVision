import { formatArea, formatLength, isNumber } from "../lib/format";
import Icon from "./Icon";
import { ParcelReasoning } from "./ParcelPanel";
import { ErrorState, LoadingState, Unavailable } from "./States";
import { Tag } from "./StatusBadge";

function display(value) {
  if (value === null || value === undefined || value === "") return <Unavailable>Not recorded</Unavailable>;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * A record of an existing GIS / land-records layer, with the AI features
 * overlaid on it. The record is shown as supplied; only the overlay is AI.
 */
export default function ReferencePanel({ selection, detail, loading, error, onRetry, onClose, onZoom }) {
  const attributes = Object.entries(detail?.properties || selection?.properties || {});
  const metrics = detail?.metrics;

  return (
    <aside className="side-panel slide-in" aria-label="Selected existing GIS record">
      <header className="side-panel__head">
        <div className="stack" style={{ gap: 6 }}>
          <span className="muted">Existing GIS record</span>
          <span className="side-panel__id">
            {selection.dataset.name} · {selection.index + 1}
          </span>
          <div className="row" style={{ gap: 6 }}>
            <Tag tone="muted" icon="shapes">As supplied · not verified here</Tag>
          </div>
        </div>
        <div className="row" style={{ gap: 2, flexWrap: "nowrap" }}>
          <button type="button" className="icon-btn" onClick={onZoom} aria-label="Zoom to this record" title="Zoom to this record">
            <Icon name="zoom-fit" size={17} />
          </button>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close panel" title="Close">
            <Icon name="x" size={17} />
          </button>
        </div>
      </header>

      <div className="side-panel__scroll">
        {loading && !detail ? <LoadingState compact label="Loading record" /> : null}
        {error ? <ErrorState compact title="This record could not be loaded" detail={error} onRetry={onRetry} /> : null}

        {detail ? (
          <>
            <p className="uncertainty__note">{detail.note}</p>

            <section>
              <h4>Measured from the record's geometry</h4>
              {metrics ? (
                <>
                  <div className="metric-grid">
                    <div className="metric">
                      <span>Area</span>
                      <strong>{isNumber(metrics.area_m2) ? formatArea(metrics.area_m2) : <Unavailable />}</strong>
                    </div>
                    <div className="metric">
                      <span>Perimeter</span>
                      <strong>{isNumber(metrics.perimeter_m) ? formatLength(metrics.perimeter_m) : <Unavailable />}</strong>
                    </div>
                  </div>
                  <p className="uncertainty__note">Measured in {metrics.metric_crs}.</p>
                  {metrics.geometry_problems?.length ? (
                    <ul className="reason-list">
                      {metrics.geometry_problems.map((problem) => (
                        <li key={problem}>{problem}</li>
                      ))}
                    </ul>
                  ) : null}
                </>
              ) : (
                <Unavailable>This record is not a polygon</Unavailable>
              )}
            </section>

            <section>
              <h4>Attributes in the dataset</h4>
              {attributes.length ? (
                <dl className="kv">
                  {attributes.slice(0, 24).map(([key, value]) => (
                    <div key={key} style={{ display: "contents" }}>
                      <dt>{key}</dt>
                      <dd>{display(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <Unavailable>The record has no attributes</Unavailable>
              )}
            </section>

            <ParcelReasoning reasoning={detail.reasoning} title="AI features inside this parcel" />
            <p className="uncertainty__note">
              The overlay is AI generated and preliminary. It does not change or confirm the existing record.
            </p>
          </>
        ) : null}
      </div>
    </aside>
  );
}
