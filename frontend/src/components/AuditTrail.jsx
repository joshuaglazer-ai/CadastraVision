import { STATUS } from "../lib/constants";
import { formatDate } from "../lib/format";

const ACTION_LABEL = {
  "review.approve": "Approved",
  "review.edit": "Outline edited",
  "review.flag": "Flagged",
  "review.reject": "Rejected",
  "review.add_ground_truth": "Ground truth added",
  "review.update": "Review amended",
  "job.create": "Imagery registered",
  "job.start": "Processing started",
  "job.complete": "Processing completed",
  "job.fail": "Processing failed",
  "dataset.upload": "Dataset uploaded",
  "export.geojson": "GeoJSON exported",
  "export.csv": "CSV exported",
  "export.gpkg": "GeoPackage exported",
};

/** Who did what, when, and how the status changed. */
export default function AuditTrail({ events, showEntity = false, emptyText = "No activity recorded yet." }) {
  if (!events?.length) {
    return <p className="muted" style={{ fontSize: "0.9rem" }}>{emptyText}</p>;
  }
  return (
    <ol className="timeline">
      {events.map((event) => {
        const before = event.before?.verification_status;
        const after = event.after?.verification_status;
        return (
          <li key={event.event_id}>
            <div>
              <strong>{ACTION_LABEL[event.action] || event.action}</strong>
              {showEntity ? <span className="mono"> · {String(event.entity_id).split(":").pop()}</span> : null}
            </div>
            {before && after && before !== after ? (
              <div className="muted">
                {STATUS[before]?.label || before} → {STATUS[after]?.label || after}
              </div>
            ) : null}
            {event.reason ? <div className="muted">“{event.reason}”</div> : null}
            <div className="timeline__meta">
              {event.actor_id}
              {event.actor_email ? ` (${event.actor_email})` : ""} · {formatDate(event.at, true)}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
