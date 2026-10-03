import { formatBytes, formatDate, formatNumber } from "../lib/format";
import Icon from "./Icon";
import { Tag } from "./StatusBadge";

const STATUS_TONE = {
  AVAILABLE: "verified",
  PROCESSED: "verified",
  PROCESSING: "info",
  "REQUIRES REVIEW": "review",
  "NOT AVAILABLE": "muted",
};

const KIND_ICON = { raster: "satellite", vector: "shapes", points: "crosshair", document: "file" };

function resolution(dataset) {
  if (!dataset.resolution_m) return null;
  const value = Math.max(...dataset.resolution_m);
  return value < 1 ? `${formatNumber(value * 100, 1)} cm/px` : `${formatNumber(value, 2)} m/px`;
}

function extent(dataset) {
  if (!dataset.extent) return null;
  const [west, south, east, north] = dataset.extent;
  return `${south.toFixed(4)}–${north.toFixed(4)}° N, ${west.toFixed(4)}–${east.toFixed(4)}° E`;
}

export default function DatasetCard({ dataset, action }) {
  const rows = [
    ["Source", dataset.origin],
    ["CRS", dataset.crs],
    ["Resolution", resolution(dataset)],
    ["Size", dataset.width ? `${formatNumber(dataset.width)} × ${formatNumber(dataset.height)} px` : null],
    ["Features", dataset.feature_count != null ? formatNumber(dataset.feature_count) : null],
    ["Extent", extent(dataset)],
    ["Date", formatDate(dataset.date)],
    ["File size", formatBytes(dataset.size_bytes)],
    ["Processing", dataset.processing_state ? dataset.processing_state.toLowerCase() : null],
  ].filter(([, value]) => value);

  return (
    <article className="dataset-card">
      <div className="row row--between" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
        <div className="row" style={{ gap: 9, flexWrap: "nowrap", alignItems: "flex-start" }}>
          <Icon name={KIND_ICON[dataset.kind] || "database"} size={18} />
          <span className="dataset-card__name">{dataset.name}</span>
        </div>
        <Tag tone={STATUS_TONE[dataset.status] || "muted"}>{dataset.status.toLowerCase()}</Tag>
      </div>
      <dl className="kv">
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "contents" }}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {dataset.notes?.length ? (
        <ul className="reason-list">
          {dataset.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      {action ? <div className="row">{action}</div> : null}
    </article>
  );
}
