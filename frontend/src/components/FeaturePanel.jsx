import { CLASS_STYLE } from "../lib/constants";
import { formatArea, formatDate, formatLength, formatNumber, isNumber } from "../lib/format";
import AuditTrail from "./AuditTrail";
import ConfidenceIndicator from "./ConfidenceIndicator";
import Icon from "./Icon";
import ParcelPanel from "./ParcelPanel";
import ReviewPanel from "./ReviewPanel";
import { ErrorState, LoadingState, Unavailable } from "./States";
import StatusBadge, { PreliminaryLabel, PriorityBadge, Tag } from "./StatusBadge";

const GEOMETRY_STATUS = {
  VALID: "Valid",
  REPAIRED: "Repaired automatically",
  INVALID: "Invalid",
  NOT_CHECKED: "Not checked",
};

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

/**
 * Everything known about the selected parcel or feature, and the place
 * where the surveyor records a decision on it.
 */
export default function FeaturePanel({
  source,
  selection,
  detail,
  loading,
  error,
  onRetry,
  onClose,
  onZoom,
  review,
}) {
  const properties = detail?.properties || selection?.properties || {};
  const isParcel = properties.layer === "parcels";
  const isPlot = properties.layer === "plots";
  const verified = ["SURVEYOR_VERIFIED", "EDITED"].includes(properties.verification_status);
  const classStyle =
    CLASS_STYLE[isParcel ? "parcels" : isPlot ? "plots" : properties.class_key] || CLASS_STYLE.unknown;
  const measure = (value, formatter) => (isNumber(value) ? formatter(value) : <Unavailable />);

  return (
    <aside className="side-panel slide-in" aria-label="Selected feature">
      <header className="side-panel__head">
        <div className="stack" style={{ gap: 6 }}>
          <div className="row" style={{ gap: 8 }}>
            <span className="swatch" style={{ color: classStyle.color, background: `${classStyle.fill}55` }} aria-hidden="true" />
            <span className="muted">
              {isParcel ? "Candidate parcel" : isPlot ? "Candidate plot" : properties.class_name || "Feature"}
            </span>
          </div>
          <span className="side-panel__id">{selection?.id}</span>
          <div className="row" style={{ gap: 6 }}>
            <PreliminaryLabel verified={verified} />
            {properties.verification_status && properties.verification_status !== "AI_GENERATED" ? (
              <StatusBadge status={properties.verification_status} size="sm" />
            ) : null}
          </div>
        </div>
        <div className="row" style={{ gap: 2, flexWrap: "nowrap" }}>
          <button type="button" className="icon-btn" onClick={onZoom} aria-label="Zoom to this feature" title="Zoom to this feature">
            <Icon name="zoom-fit" size={17} />
          </button>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close panel" title="Close">
            <Icon name="x" size={17} />
          </button>
        </div>
      </header>

      <div className="side-panel__scroll">
        {loading && !detail ? <LoadingState compact label="Loading feature" /> : null}
        {error ? <ErrorState compact title="This feature could not be loaded" detail={error} onRetry={onRetry} /> : null}

        {detail ? (
          <>
            {!verified ? (
              <p className="uncertainty__note">
                {isPlot
                  ? "Boundary proposed by geometric subdivision around a detected building; not observed in imagery. A preliminary proposal for a surveyor to check, not a cadastral record."
                  : "Preliminary geometry from AI segmentation. Not a legal cadastral ownership record."}
              </p>
            ) : null}

            <section>
              <h4>Measurements</h4>
              <div className="metric-grid">
                <Metric label="Area" value={measure(properties.area_m2, formatArea)} />
                <Metric label="Perimeter" value={measure(properties.perimeter_m, formatLength)} />
                <Metric label="Length (N–S)" value={measure(properties.length_m, formatLength)} />
                <Metric label="Width (E–W)" value={measure(properties.width_m, formatLength)} />
              </div>
              <p className="uncertainty__note">
                Measured in {properties.metric_crs || "a projected CRS"}
                {properties.geometry_source === "SURVEYOR_EDIT"
                  ? ". Values describe the AI outline; the surveyor's edit is drawn on the map."
                  : "."}
              </p>
            </section>

            {isParcel ? <ParcelPanel properties={properties} reasoning={detail.reasoning} /> : null}

            {isPlot ? (
              <section>
                <h4>How this plot was made</h4>
                <div className="metric-grid">
                  <Metric label="Building" value={properties.building_feature_id || <Unavailable />} />
                  <Metric label="Building area" value={measure(properties.building_area_m2, formatArea)} />
                  <Metric
                    label="Coverage"
                    value={
                      isNumber(properties.coverage_ratio) ? `${Math.round(properties.coverage_ratio * 100)} %` : <Unavailable />
                    }
                  />
                  <Metric label="Detected buildings inside" value={measure(properties.buildings_inside, (v) => String(v))} />
                  <Metric label="Nearest road" value={measure(properties.nearest_road_distance_m, formatLength)} />
                  <Metric
                    label="Road access"
                    value={
                      properties.road_access_candidate == null ? (
                        <Unavailable>No road detected</Unavailable>
                      ) : properties.road_access_candidate ? (
                        "Yes"
                      ) : (
                        "No"
                      )
                    }
                  />
                </div>
                <p className="uncertainty__note">
                  Method: morphological tessellation. Land within {properties.delineation_limit_m} m of the building,
                  not road or water, assigned to its nearest building on a{" "}
                  {Math.round((properties.delineation_grid_m || 0) * 100)} cm grid.
                  {properties.buildings_inside > 1
                    ? ` ${properties.buildings_inside} detected buildings lie inside this plot.`
                    : ""}
                </p>
              </section>
            ) : null}

            <section>
              <h4>Model-derived uncertainty</h4>
              {isPlot ? (
                <p className="uncertainty__note">
                  The plot boundary is not a model output, so it has no model uncertainty of its own. Its
                  building&apos;s mean model confidence is{" "}
                  {isNumber(properties.building_confidence) ? properties.building_confidence.toFixed(2) : "not recorded"}.
                </p>
              ) : (
                <ConfidenceIndicator
                  confidence={properties.confidence}
                  entropy={properties.entropy}
                  uncertainty={properties.uncertainty}
                />
              )}
            </section>

            <section>
              <h4>Quality checks</h4>
              <div className="row" style={{ gap: 6 }}>
                <PriorityBadge priority={properties.review_priority} />
                {(properties.qa_flags || []).includes("FRAGMENT") ? <Tag tone="muted">Fragment</Tag> : null}
                {properties.has_overlap ? <Tag tone="review" icon="alert">Overlap</Tag> : null}
              </div>
              {properties.review_reasons?.length ? (
                <ul className="reason-list">
                  {properties.review_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted" style={{ fontSize: "0.9rem" }}>
                  No geometry or uncertainty issue was detected.
                </p>
              )}
              <dl className="kv">
                <dt>Topology</dt>
                <dd>{GEOMETRY_STATUS[properties.geometry_status] || properties.geometry_status || <Unavailable />}</dd>
                <dt>Rings / vertices</dt>
                <dd>
                  {formatNumber(properties.ring_count, 0, "")} / {formatNumber(properties.vertex_count, 0, "")}
                </dd>
                {properties.processing_job_id ? (
                  <>
                    <dt>Processing job</dt>
                    <dd className="mono">{properties.processing_job_id}</dd>
                  </>
                ) : null}
                {properties.generated_at ? (
                  <>
                    <dt>Generated</dt>
                    <dd>{formatDate(properties.generated_at, true)}</dd>
                  </>
                ) : null}
              </dl>
            </section>

            <ReviewPanel source={source} feature={detail} {...review} />

            <section>
              <h4>History</h4>
              <AuditTrail events={detail.audit} emptyText="No surveyor has reviewed this feature yet." />
            </section>
          </>
        ) : null}
      </div>
    </aside>
  );
}
