import { CLASS_STYLE } from "../lib/constants";
import { formatArea, formatNumber, formatPercent, isNumber } from "../lib/format";
import { Unavailable } from "./States";

const colourOf = (key) => (CLASS_STYLE[key] || CLASS_STYLE.unknown).color;

/** Horizontal bars of area per land-cover class. */
export function ClassAreaChart({ classes }) {
  if (!classes?.length) return <Unavailable />;
  const max = Math.max(...classes.map((entry) => entry.area_m2), 1);
  return (
    <div className="bars" role="img" aria-label="Area per land-cover class">
      {classes.map((entry) => (
        <div className="bar" key={entry.class_key}>
          <span className="bar__label">
            <span className="dot" style={{ background: colourOf(entry.class_key) }} />
            {entry.class_name}
          </span>
          <div className="bar__track">
            <div
              className="bar__fill"
              style={{ width: `${(entry.area_m2 / max) * 100}%`, background: colourOf(entry.class_key) }}
            />
          </div>
          <span className="bar__value">
            {formatArea(entry.area_m2)}
            {isNumber(entry.share_of_mapped) ? ` · ${formatPercent(entry.share_of_mapped, 0)}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Feature counts per class, split into mapped features and fragments. */
export function ClassCountChart({ classes, threshold }) {
  if (!classes?.length) return <Unavailable />;
  const max = Math.max(...classes.map((entry) => entry.count), 1);
  return (
    <div className="stack">
      <div className="bars" role="img" aria-label="Feature count per land-cover class">
        {classes.map((entry) => (
          <div className="bar" key={entry.class_key}>
            <span className="bar__label">
              <span className="dot" style={{ background: colourOf(entry.class_key) }} />
              {entry.class_name}
            </span>
            <div className="bar__track" style={{ display: "flex" }}>
              <div
                className="bar__fill"
                style={{
                  width: `${(entry.features_excluding_fragments / max) * 100}%`,
                  background: colourOf(entry.class_key),
                  borderRadius: "3px 0 0 3px",
                }}
              />
              <div
                style={{
                  width: `${(entry.fragments / max) * 100}%`,
                  background: colourOf(entry.class_key),
                  opacity: 0.32,
                }}
              />
            </div>
            <span className="bar__value">
              {formatNumber(entry.features_excluding_fragments)} + {formatNumber(entry.fragments)}
            </span>
          </div>
        ))}
      </div>
      <p className="uncertainty__note">
        Solid: features of at least {threshold} m². Faded: fragments below that, which are mostly
        segmentation noise.
      </p>
    </div>
  );
}

const WORKLOAD = [
  { key: "high", label: "High priority", colour: "#ff7a6b" },
  { key: "medium", label: "Medium priority", colour: "#f2b544" },
  { key: "low", label: "Low priority", colour: "#6f93ad" },
  { key: "verified", label: "Verified", colour: "#3fd6a3" },
  { key: "flagged", label: "Flagged or rejected", colour: "#c75a4e" },
];

/** One stacked bar of the review workload. */
export function WorkloadChart({ counts }) {
  if (!counts) return <Unavailable />;
  const total = WORKLOAD.reduce((sum, item) => sum + (counts[item.key] || 0), 0);
  if (!total) return <p className="muted">No features to review for this source.</p>;
  return (
    <div className="stack">
      <div className="stacked" role="img" aria-label="Review workload by group">
        {WORKLOAD.map((item) =>
          counts[item.key] ? (
            <span
              key={item.key}
              title={`${item.label}: ${formatNumber(counts[item.key])}`}
              style={{ width: `${(counts[item.key] / total) * 100}%`, background: item.colour }}
            />
          ) : null
        )}
      </div>
      <div className="chart-legend">
        {WORKLOAD.map((item) => (
          <span key={item.key}>
            <span className="dot" style={{ background: item.colour }} />
            {item.label}: <strong>{formatNumber(counts[item.key] || 0)}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Histogram of per-pixel confidence written by a processing job. */
export function ConfidenceHistogram({ histogram }) {
  const pixels = histogram?.pixels;
  if (!pixels?.length) return <Unavailable />;
  const max = Math.max(...pixels, 1);
  const total = pixels.reduce((sum, value) => sum + value, 0) || 1;
  return (
    <div className="stack">
      <div
        role="img"
        aria-label="Distribution of per-pixel model confidence"
        style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 96 }}
      >
        {pixels.map((value, index) => (
          <div
            key={index}
            title={`${(index / 10).toFixed(1)}–${((index + 1) / 10).toFixed(1)}: ${formatPercent(value / total)}`}
            style={{
              flex: 1,
              height: `${Math.max((value / max) * 100, 1)}%`,
              background: index < 6 ? "#ff7a6b" : index < 8 ? "#f2b544" : "#5fe0e6",
              borderRadius: "2px 2px 0 0",
            }}
          />
        ))}
      </div>
      <div className="row row--between muted" style={{ fontSize: "0.8rem" }}>
        <span>0.0</span>
        <span>Confidence</span>
        <span>1.0</span>
      </div>
    </div>
  );
}

export function Stat({ label, value, note }) {
  return (
    <div className="metric" style={{ padding: "12px 14px" }}>
      <span>{label}</span>
      <strong style={{ fontSize: "1.3rem" }}>{value}</strong>
      {note ? <span style={{ color: "var(--haze)" }}>{note}</span> : null}
    </div>
  );
}
