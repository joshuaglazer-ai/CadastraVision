import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import DisclaimerBanner from "../../components/DisclaimerBanner";
import Icon from "../../components/Icon";
import { EmptyState, Unavailable } from "../../components/States";
import { Tag } from "../../components/StatusBadge";
import { useWorkspace } from "../../context/WorkspaceContext";
import { getAnalytics, getDatasets } from "../../lib/api";
import { formatArea, formatDate, formatLength, formatNumber, formatPercent, titleCase } from "../../lib/format";

const STEPS = [
  { key: "datasets", to: "/survey/datasets", label: "Datasets", icon: "database" },
  { key: "processing", to: "/survey/processing", label: "AI processing", icon: "cpu" },
  { key: "review", to: "/survey/review", label: "Review", icon: "check" },
  { key: "export", to: "/survey/export", label: "Export", icon: "download" },
];

export default function Assignment() {
  const { surveyor, assignment, notes, source, system } = useWorkspace();
  const [analytics, setAnalytics] = useState(null);
  const [datasets, setDatasets] = useState(null);

  useEffect(() => {
    let active = true;
    getAnalytics(source).then((data) => active && setAnalytics(data)).catch(() => {});
    getDatasets().then((data) => active && setDatasets(data)).catch(() => {});
    return () => {
      active = false;
    };
  }, [source]);

  const value = (text) => (text ? text : <Unavailable>Not set</Unavailable>);
  const model = system?.model;

  const progress = {
    datasets: datasets
      ? `${datasets.summary.available_categories} of ${datasets.summary.total_categories} source types available`
      : null,
    processing: analytics?.processing?.label,
    review: analytics?.review
      ? `${formatNumber(analytics.review.review_required)} features need review, ${formatNumber(analytics.review.verified)} verified`
      : null,
    export: analytics?.review ? `${formatNumber(analytics.review.verified)} verified features ready` : null,
  };

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Assignment</h1>
          <p>Who is surveying, where, and how far the work has got.</p>
        </div>
        <div className="page-head__actions">
          <Link to="/map" className="btn btn--secondary">
            <Icon name="map" size={16} /> Open the map
          </Link>
        </div>
      </div>

      <div className="grid grid--2">
        <section className="panel">
          <div className="panel__head">
            <h3>Surveyor</h3>
            {surveyor?.is_dev_session ? <Tag tone="demo">Dev session</Tag> : <Tag tone="verified" icon="shield">Signed in</Tag>}
          </div>
          <div className="panel__body">
            <dl className="kv">
              <dt>Name</dt>
              <dd>{surveyor?.name}</dd>
              <dt>Surveyor ID</dt>
              <dd className="mono">{surveyor?.surveyor_id}</dd>
              <dt>Account</dt>
              <dd>{surveyor?.email}</dd>
              <dt>Department</dt>
              <dd>{value(surveyor?.department)}</dd>
            </dl>
            <p className="uncertainty__note" style={{ marginTop: 12 }}>
              Your identity comes from your sign-in. It is attached to every review and export you make.
            </p>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <h3>Assigned area</h3>
            {assignment ? (
              assignment.is_demo ? <Tag tone="demo" icon="alert">Demo assignment</Tag> : <Tag tone="verified">Assigned</Tag>
            ) : null}
          </div>
          <div className="panel__body">
            {assignment ? (
              <dl className="kv">
                <dt>Assignment ID</dt>
                <dd className="mono">{assignment.assignment_id}</dd>
                <dt>Status</dt>
                <dd>{titleCase(assignment.assignment_status)}</dd>
                <dt>State</dt>
                <dd>{value(assignment.state)}</dd>
                <dt>District</dt>
                <dd>{value(assignment.district)}</dd>
                <dt>Taluk</dt>
                <dd>{value(assignment.taluk)}</dd>
                <dt>Village</dt>
                <dd>{value(assignment.village)}</dd>
                <dt>Survey type</dt>
                <dd>{value(assignment.survey_type)}</dd>
                <dt>Assigned on</dt>
                <dd>{assignment.assigned_date ? formatDate(assignment.assigned_date) : <Unavailable>Not set</Unavailable>}</dd>
                <dt>Boundary area (measured)</dt>
                <dd>{formatArea(assignment.area_m2)}</dd>
                <dt>Boundary perimeter</dt>
                <dd>{formatLength(assignment.perimeter_m)}</dd>
                <dt>Declared area</dt>
                <dd>
                  {assignment.declared_area_ha != null
                    ? `${formatNumber(assignment.declared_area_ha, 1)} ha`
                    : <Unavailable>Not declared</Unavailable>}
                </dd>
                <dt>Measured in</dt>
                <dd>{assignment.metric_crs || <Unavailable />}</dd>
              </dl>
            ) : (
              <EmptyState
                compact
                icon="target"
                title="No assignment for this account"
                detail="Requires administrator input: add this account's e-mail to an assignment in the registry."
              />
            )}
          </div>
        </section>
      </div>

      {notes?.length ? (
        <section className="panel">
          <div className="panel__head">
            <h3>Things to know about this assignment</h3>
          </div>
          <div className="panel__body">
            <ul className="reason-list">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel__head">
          <h3>Progress</h3>
        </div>
        <div className="panel__body">
          <div className="grid grid--2">
            {STEPS.map((step) => (
              <Link key={step.key} to={step.to} className="dataset-card" style={{ color: "inherit", textDecoration: "none" }}>
                <div className="row" style={{ gap: 9 }}>
                  <Icon name={step.icon} size={18} />
                  <strong>{step.label}</strong>
                </div>
                <span className="muted">{progress[step.key] || <Unavailable />}</span>
              </Link>
            ))}
          </div>
        </div>
      </section>

      <div className="grid grid--2">
        <section className="panel">
          <div className="panel__head">
            <h3>Coverage</h3>
          </div>
          <div className="panel__body">
            <dl className="kv">
              <dt>Mapped by AI features</dt>
              <dd>{formatArea(analytics?.mapped_area_m2)}</dd>
              <dt>Extent of the mapped data</dt>
              <dd>{formatArea(analytics?.data_extent_area_m2)}</dd>
              <dt>Share of the assigned area</dt>
              <dd>{formatPercent(analytics?.data_share_of_assignment)}</dd>
            </dl>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <h3>Model</h3>
            {model ? (
              model.checkpoint_present && model.runtime_available ? (
                <Tag tone="verified" icon="check">Ready</Tag>
              ) : (
                <Tag tone="review" icon="alert">Not ready</Tag>
              )
            ) : null}
          </div>
          <div className="panel__body">
            {model ? (
              <>
                <dl className="kv">
                  <dt>Architecture</dt>
                  <dd>{model.architecture} / {model.encoder}</dd>
                  <dt>Classes</dt>
                  <dd>{model.classes.map((item) => item.name).join(", ")}</dd>
                  <dt>Checkpoint</dt>
                  <dd>{model.checkpoint_present ? model.checkpoint_file : <Unavailable>Not found on the server</Unavailable>}</dd>
                  <dt>Training data</dt>
                  <dd>{model.training_data}</dd>
                </dl>
                {model.runtime_error ? (
                  <p className="field__error" style={{ marginTop: 10 }}>{model.runtime_error}</p>
                ) : null}
              </>
            ) : (
              <Unavailable />
            )}
          </div>
        </section>
      </div>

      <DisclaimerBanner />
    </main>
  );
}
