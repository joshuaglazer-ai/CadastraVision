import { useCallback, useEffect, useState } from "react";

import { ClassAreaChart, ClassCountChart, Stat, WorkloadChart } from "../../components/AnalyticsPanel";
import DisclaimerBanner from "../../components/DisclaimerBanner";
import Icon from "../../components/Icon";
import { ErrorState, LoadingState, Unavailable } from "../../components/States";
import { useWorkspace } from "../../context/WorkspaceContext";
import { errorMessage, getAnalytics } from "../../lib/api";
import { formatArea, formatNumber, formatPercent, isNumber } from "../../lib/format";

const num = (value, digits = 0) => (isNumber(value) ? formatNumber(value, digits) : <Unavailable />);
const area = (value) => (isNumber(value) ? formatArea(value) : <Unavailable />);

export default function Analytics() {
  const { source } = useWorkspace();
  const [data, setData] = useState(null);
  const [state, setState] = useState({ loading: true, error: "" });

  const load = useCallback(async () => {
    setState({ loading: true, error: "" });
    try {
      setData(await getAnalytics(source));
      setState({ loading: false, error: "" });
    } catch (error) {
      setState({ loading: false, error: errorMessage(error, "Analytics could not be computed.") });
    }
  }, [source]);

  useEffect(() => {
    load();
  }, [load]);

  if (state.error) {
    return (
      <main className="page">
        <div className="panel">
          <ErrorState title="Analytics unavailable" detail={state.error} onRetry={load} />
        </div>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="page">
        <LoadingState label="Computing analytics from the layers" />
      </main>
    );
  }

  const parcels = data.parcels;
  const review = data.review;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Analytics</h1>
          <p>Counted and summed from the layers, the review records and the job history. Nothing is estimated.</p>
        </div>
        <div className="page-head__actions">
          <button type="button" className="btn btn--secondary" onClick={load}>
            <Icon name="refresh" size={16} /> Recompute
          </button>
        </div>
      </div>

      <div className="stat-row">
        <Stat
          label="Candidate parcels"
          value={num(data.candidate_parcels)}
          note={parcels ? `${formatNumber(parcels.at_or_above_minimum)} of at least ${parcels.minimum_area_m2} m²` : null}
        />
        <Stat
          label="Candidate plots"
          value={data.plots ? num(data.candidate_plots) : "–"}
          note={data.plots ? `median ${formatArea(data.plots.median_area_m2)}` : "Not built for this source"}
        />
        <Stat label="Mapped area" value={area(data.mapped_area_m2)} note={isNumber(data.mapped_share_of_extent) ? `${formatPercent(data.mapped_share_of_extent, 0)} of the data extent` : null} />
        <Stat label="Buildings" value={num(data.buildings?.count)} note={isNumber(data.buildings?.area_m2) ? formatArea(data.buildings.area_m2) : null} />
        <Stat label="Roads" value={num(data.roads?.count)} note={isNumber(data.roads?.coverage_of_mapped) ? `${formatPercent(data.roads.coverage_of_mapped)} of mapped area` : null} />
        <Stat label="Fields" value={area(data.fields?.area_m2)} note={isNumber(data.fields?.count) ? `${formatNumber(data.fields.count)} regions` : null} />
        <Stat label="Water" value={area(data.water?.area_m2)} note={isNumber(data.water?.count) ? `${formatNumber(data.water.count)} features` : null} />
        <Stat label="Other" value={area(data.other?.area_m2)} note={isNumber(data.other?.count) ? `${formatNumber(data.other.count)} features` : null} />
      </div>

      <div className="grid grid--2">
        <section className="panel">
          <div className="panel__head">
            <h3>Area by land-cover class</h3>
          </div>
          <div className="panel__body">
            <ClassAreaChart classes={data.classes} />
          </div>
        </section>
        <section className="panel">
          <div className="panel__head">
            <h3>Features by land-cover class</h3>
          </div>
          <div className="panel__body">
            <ClassCountChart classes={data.classes} threshold={data.fragment_threshold_m2} />
          </div>
        </section>
      </div>

      <div className="grid grid--2">
        <section className="panel">
          <div className="panel__head">
            <h3>Review workload</h3>
            <span className="muted">{review ? `${formatNumber(review.review_required)} need review` : ""}</span>
          </div>
          <div className="panel__body stack">
            <WorkloadChart counts={review?.counts} />
            <dl className="kv">
              <dt>High priority</dt>
              <dd>{num(review?.counts?.high)}</dd>
              <dt>Medium priority</dt>
              <dd>{num(review?.counts?.medium)}</dd>
              <dt>Verified (approved or edited)</dt>
              <dd>{num(review?.verified)}</dd>
              <dt>Flagged or rejected</dt>
              <dd>{num(review?.flagged)}</dd>
              <dt>Ground-truth observations</dt>
              <dd>{num(review?.ground_truth)}</dd>
              <dt>Review records</dt>
              <dd>{num(review?.total_reviews)}</dd>
              <dt>Fragments left out of the queue</dt>
              <dd>{num(review?.fragments_hidden)}</dd>
            </dl>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <h3>Candidate parcels and road access</h3>
          </div>
          <div className="panel__body stack">
            {parcels ? (
              <>
                <dl className="kv">
                  <dt>Candidate parcels</dt>
                  <dd>{formatNumber(parcels.total)}</dd>
                  <dt>Of at least {parcels.minimum_area_m2} m²</dt>
                  <dd>{formatNumber(parcels.at_or_above_minimum)}</dd>
                  <dt>Fragments under {parcels.fragment_threshold_m2} m²</dt>
                  <dd>{formatNumber(parcels.fragments)}</dd>
                  <dt>Total area</dt>
                  <dd>{formatArea(parcels.area_m2)}</dd>
                  <dt>With road access</dt>
                  <dd>{formatNumber(parcels.road_access)}</dd>
                  <dt>Without road access</dt>
                  <dd>{formatNumber(parcels.no_road_access)}</dd>
                  {parcels.road_access_unknown ? (
                    <>
                      <dt>Road access not determined</dt>
                      <dd>{formatNumber(parcels.road_access_unknown)}</dd>
                    </>
                  ) : null}
                </dl>
                <p className="uncertainty__note">{parcels.road_access_basis}.</p>
              </>
            ) : (
              <Unavailable>No candidate parcel layer for this source</Unavailable>
            )}
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel__head">
          <h3>Candidate plots</h3>
        </div>
        <div className="panel__body stack">
          {data.plots ? (
            <>
              <dl className="kv">
                <dt>Candidate plots</dt>
                <dd>{formatNumber(data.plots.count)}</dd>
                <dt>Total area</dt>
                <dd>{formatArea(data.plots.area_m2)}</dd>
                <dt>Plot size, 10th / median / 90th percentile</dt>
                <dd>
                  {formatArea(data.plots.p10_area_m2)} / {formatArea(data.plots.median_area_m2)} /{" "}
                  {formatArea(data.plots.p90_area_m2)}
                </dd>
                <dt>Holding exactly one building of at least 5 m²</dt>
                <dd>
                  {formatNumber(data.plots.one_building_seed_rule)}
                  {isNumber(data.plots.one_building_share_seed_rule)
                    ? ` (${formatPercent(data.plots.one_building_share_seed_rule, 0)})`
                    : ""}
                </dd>
                <dt>Holding exactly one detected building feature (any size)</dt>
                <dd>
                  {formatNumber(data.plots.one_building_all)}
                  {isNumber(data.plots.one_building_share_all) ? ` (${formatPercent(data.plots.one_building_share_all, 0)})` : ""}
                </dd>
                <dt>Flagged: building covers under 5% of the plot</dt>
                <dd>{formatNumber(data.plots.low_coverage)}</dd>
                <dt>With a road within reach</dt>
                <dd>{formatNumber(data.plots.road_access)}</dd>
              </dl>
              <p className="uncertainty__note">{data.plots.review_reason}. Method: morphological tessellation.</p>

              <h4>Against the reference layer</h4>
              {data.plots.reference_check?.reference_features ? (
                <>
                  <dl className="kv">
                    <dt>Reference features in this area</dt>
                    <dd>{formatNumber(data.plots.reference_check.reference_features)}</dd>
                    <dt>Plots holding exactly one reference feature</dt>
                    <dd>{formatNumber(data.plots.reference_check.plots_with_one)}</dd>
                    <dt>Plots holding several</dt>
                    <dd>{formatNumber(data.plots.reference_check.plots_with_several)}</dd>
                    <dt>Plots holding none</dt>
                    <dd>{formatNumber(data.plots.reference_check.plots_with_none)}</dd>
                    <dt>Reference features with a plot to themselves</dt>
                    <dd>{formatNumber(data.plots.reference_check.reference_with_own_plot)}</dd>
                    <dt>Reference features outside every plot</dt>
                    <dd>{formatNumber(data.plots.reference_check.reference_outside_plots)}</dd>
                  </dl>
                  <p className="uncertainty__note">
                    {data.plots.reference_check.basis} Layers: {data.plots.reference_check.datasets.join(", ")}.
                  </p>
                </>
              ) : (
                <Unavailable>
                  {data.plots.reference_check?.message ||
                    "No existing GIS layer to compare with. Add one under Datasets, Existing maps and land records."}
                </Unavailable>
              )}
            </>
          ) : (
            <Unavailable>No candidate plots for this source. They are made from a processing job&apos;s buildings.</Unavailable>
          )}
        </div>
      </section>

      <div className="grid grid--2">
        <section className="panel">
          <div className="panel__head">
            <h3>Model-derived uncertainty</h3>
          </div>
          <div className="panel__body stack">
            {data.uncertainty.available ? (
              <dl className="kv">
                <dt>Features with confidence</dt>
                <dd>{formatNumber(data.uncertainty.features_with_confidence)}</dd>
                <dt>Mean confidence</dt>
                <dd>{formatNumber(data.uncertainty.mean_confidence, 3)}</dd>
                <dt>Mean entropy</dt>
                <dd>{formatNumber(data.uncertainty.mean_entropy, 3)} nats</dd>
              </dl>
            ) : (
              <div className="notice">
                <Icon name="info" size={16} />
                <p>{data.uncertainty.message}</p>
              </div>
            )}
            <p className="uncertainty__note">
              Uncertainty ranks features for review. It is not a measure of accuracy.
            </p>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <h3>Processing</h3>
          </div>
          <div className="panel__body">
            <dl className="kv">
              <dt>Status</dt>
              <dd>{data.processing.label}</dd>
              <dt>Jobs on record</dt>
              <dd>{formatNumber(data.processing.total_jobs)}</dd>
              {Object.entries(data.processing.counts).map(([status, count]) => (
                <div key={status} style={{ display: "contents" }}>
                  <dt>{status.charAt(0) + status.slice(1).toLowerCase()}</dt>
                  <dd>{formatNumber(count)}</dd>
                </div>
              ))}
              <dt>Assigned area</dt>
              <dd>{area(data.assignment_area_m2)}</dd>
              <dt>Data extent</dt>
              <dd>{area(data.data_extent_area_m2)}</dd>
              <dt>Data extent as share of assigned area</dt>
              <dd>{isNumber(data.data_share_of_assignment) ? formatPercent(data.data_share_of_assignment) : <Unavailable />}</dd>
            </dl>
          </div>
        </section>
      </div>

      <DisclaimerBanner />
    </main>
  );
}
