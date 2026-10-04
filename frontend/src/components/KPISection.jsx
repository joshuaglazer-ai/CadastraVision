import { formatArea, formatNumber, isNumber } from "../lib/format";
import Icon from "./Icon";
import { Unavailable } from "./States";
import NoImageryState from "./NoImageryState";
import { noFeaturesInArea } from "../lib/emptyArea";

function Kpi({ icon, label, value, note, alert = false, text = false }) {
  return (
    <div className={`kpi ${alert ? "kpi--alert" : ""}`}>
      <span className="kpi__label">
        <Icon name={icon} size={15} />
        {label}
      </span>
      <span className={`kpi__value ${text ? "kpi__value--text" : ""}`}>{value}</span>
      {note ? <span className="kpi__note">{note}</span> : null}
    </div>
  );
}

const count = (value) => (isNumber(value) ? formatNumber(value) : <Unavailable />);

/**
 * Headline numbers for the selected source. Every value comes from the
 * analytics endpoint; a missing input shows "Data unavailable".
 */
export default function KPISection({ analytics }) {
  const a = analytics || {};
  const parcels = a.parcels;
  const review = a.review;
  const processing = a.processing;

  // Zeros here would read as a surveyed area with nothing in it.
  if (noFeaturesInArea(analytics)) {
    return (
      <section className="panel" aria-label="Key figures">
        <div className="panel__body">
          <NoImageryState />
        </div>
      </section>
    );
  }

  return (
    <section className="kpi-grid" aria-label="Key figures">
      <Kpi
        icon="shapes"
        label="AI features"
        value={count(a.ai_features)}
        note={
          isNumber(a.fragments)
            ? `${formatNumber(a.ai_features_excluding_fragments)} of at least ${a.fragment_threshold_m2} m², ${formatNumber(a.fragments)} fragments`
            : null
        }
      />
      <Kpi
        icon="parcel"
        label="Candidate parcels"
        value={count(a.candidate_parcels)}
        note={
          parcels
            ? `${formatNumber(parcels.at_or_above_minimum)} of at least ${parcels.minimum_area_m2} m²`
            : null
        }
      />
      <Kpi
        icon="shapes"
        label="Candidate plots"
        value={a.plots ? count(a.candidate_plots) : <Unavailable>Not built</Unavailable>}
        note={
          a.plots && isNumber(a.plots.one_building_share)
            ? `${Math.round(a.plots.one_building_share * 100)} % hold exactly one detected building`
            : "Made from a processing job's buildings"
        }
      />
      <Kpi
        icon="building"
        label="Buildings"
        value={count(a.buildings?.count)}
        note={isNumber(a.buildings?.area_m2) ? formatArea(a.buildings.area_m2) : null}
      />
      <Kpi
        icon="road"
        label="Roads"
        value={count(a.roads?.count)}
        note={isNumber(a.roads?.area_m2) ? formatArea(a.roads.area_m2) : null}
      />
      <Kpi
        icon="droplet"
        label="Water"
        value={count(a.water?.count)}
        note={isNumber(a.water?.area_m2) ? formatArea(a.water.area_m2) : null}
      />
      <Kpi
        icon="alert"
        label="Review required"
        alert={Boolean(review?.review_required)}
        value={count(review?.review_required)}
        note={
          review
            ? `${formatNumber(review.counts.high)} high, ${formatNumber(review.counts.medium)} medium priority`
            : null
        }
      />
      <Kpi
        icon="map"
        label="Mapped area"
        value={isNumber(a.mapped_area_m2) ? formatArea(a.mapped_area_m2) : <Unavailable />}
        note={
          isNumber(a.data_extent_area_m2) ? `within a ${formatArea(a.data_extent_area_m2)} extent` : null
        }
      />
      <Kpi
        icon="cpu"
        label="Processing status"
        text
        value={processing ? processing.label : <Unavailable />}
        note={
          processing
            ? processing.total_jobs
              ? `${formatNumber(processing.total_jobs)} job${processing.total_jobs === 1 ? "" : "s"} on record`
              : "Upload a GeoTIFF to run the model"
            : null
        }
      />
    </section>
  );
}
