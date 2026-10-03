import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "../context/WorkspaceContext";
import { errorMessage } from "../lib/api";
import { PRODUCT } from "../lib/constants";
import { formatArea } from "../lib/format";
import { areaTitle } from "../lib/workArea";
import Icon from "./Icon";
import { Unavailable } from "./States";

function Item({ label, children }) {
  return (
    <div className="assignment-bar__item">
      <span className="assignment-bar__label">{label}</span>
      <span className="assignment-bar__value">{children}</span>
    </div>
  );
}

const SHORT_LABEL = {
  ASSIGNED: "assigned",
  "SELF-DECLARED WORK AREA": "self-declared",
  "DEMO ASSIGNMENT": "demo",
};

/**
 * Who is surveying what, shown on every workspace page, with a switcher
 * between the account's areas. Switching reloads the whole workspace for
 * the chosen area.
 */
export default function AssignmentBar() {
  const { surveyor, assignment, areas, switchArea, switching } = useWorkspace();
  const [error, setError] = useState("");
  const selectId = useId();
  if (!surveyor) return null;

  const value = (text) => (text ? text : <Unavailable>Not set</Unavailable>);
  const items = areas?.items || [];
  const currentId = assignment?.assignment_id || "";
  const currentListed = items.some((item) => item.assignment_id === currentId);

  async function handleSwitch(event) {
    const next = event.target.value;
    if (!next || next === currentId) return;
    setError("");
    try {
      await switchArea(next);
    } catch (err) {
      setError(errorMessage(err, "The area could not be switched."));
    }
  }

  return (
    <div className="assignment-bar" aria-label="Current area">
      <Item label="Surveyor">{surveyor.name}</Item>
      <Item label="Govt surveyor ID">
        {surveyor.govt_surveyor_id ? (
          <span className="mono" title="Entered by the surveyor; not verified against a government register">
            {surveyor.govt_surveyor_id}
          </span>
        ) : (
          <Unavailable>Not entered</Unavailable>
        )}
      </Item>

      <div className="assignment-bar__item assignment-bar__switch">
        <label className="assignment-bar__label" htmlFor={selectId}>
          Area
        </label>
        {items.length || assignment ? (
          <select
            id={selectId}
            className="select select--sm"
            value={currentId}
            onChange={handleSwitch}
            disabled={switching}
            aria-describedby={error ? `${selectId}-error` : undefined}
          >
            {!currentListed && assignment ? (
              <option value={currentId}>
                {areaTitle(assignment)} ({SHORT_LABEL[assignment.label] || "current"})
              </option>
            ) : null}
            {!assignment ? <option value="">No current area</option> : null}
            {items.map((item) => (
              <option key={item.assignment_id} value={item.assignment_id}>
                {areaTitle(item)} ({SHORT_LABEL[item.label] || item.label})
              </option>
            ))}
          </select>
        ) : (
          <Link to="/survey" className="assignment-bar__value">
            No work area yet. Add one
          </Link>
        )}
        {switching ? <span className="assignment-bar__hint" role="status">Switching…</span> : null}
        {error ? (
          <span className="field__error" id={`${selectId}-error`} role="alert">
            {error}
          </span>
        ) : null}
      </div>

      {assignment ? (
        <>
          <Item label="District">{value(assignment.district)}</Item>
          <Item label="Village">{value(assignment.village)}</Item>
          <Item label="Measured area">{formatArea(assignment.area_m2)}</Item>
        </>
      ) : null}

      <div className="assignment-bar__flags">
        {assignment?.is_demo ? (
          <span className="badge badge--demo" title="This boundary is not an authoritative survey assignment.">
            <Icon name="alert" size={13} /> Demo assignment
          </span>
        ) : null}
        {assignment?.kind === "work_area" ? (
          <span className="badge badge--review" title="Drawn or uploaded by the surveyor; not an official survey assignment.">
            <Icon name="alert" size={13} /> Self-declared work area
          </span>
        ) : null}
        <span className="badge badge--ai" title={PRODUCT.disclaimer}>
          <Icon name="cpu" size={13} /> AI generated · Preliminary
        </span>
      </div>
    </div>
  );
}
