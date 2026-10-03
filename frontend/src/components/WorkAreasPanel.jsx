import { useEffect, useState } from "react";

import { useWorkspace } from "../context/WorkspaceContext";
import { deleteWorkArea, errorMessage, getAssignedArea } from "../lib/api";
import { formatArea } from "../lib/format";
import { areaTitle } from "../lib/workArea";
import Icon from "./Icon";
import { EmptyState, ErrorState, LoadingState } from "./States";
import { Tag } from "./StatusBadge";
import WorkAreaEditor from "./WorkAreaEditor";

const LABEL_TONE = {
  ASSIGNED: "verified",
  "DEMO ASSIGNMENT": "demo",
  "SELF-DECLARED WORK AREA": "review",
};

export function AreaLabel({ label }) {
  if (!label) return null;
  const text = label === "SELF-DECLARED WORK AREA" ? "Self-declared" : label === "ASSIGNED" ? "Assigned" : "Demo";
  return (
    <Tag tone={LABEL_TONE[label] || "muted"} icon={label === "ASSIGNED" ? "shield" : "alert"}>
      {text}
    </Tag>
  );
}

/**
 * The account's areas: registry assignments (read-only) and its own work
 * areas, with Activate / Edit / Delete and the form to add one.
 */
export default function WorkAreasPanel() {
  const { areas, reloadAreas, switchArea, switching, assignment } = useWorkspace();
  const [mode, setMode] = useState(null); // null | { type: "add" } | { type: "edit", area }
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");
  const [context, setContext] = useState(null);

  // The current boundary is drawn under the editor for reference.
  useEffect(() => {
    if (!mode) return undefined;
    let active = true;
    getAssignedArea()
      .then((data) => active && setContext(data))
      .catch(() => active && setContext(null));
    return () => {
      active = false;
    };
  }, [mode]);

  async function activate(item) {
    setBusy(item.assignment_id);
    setActionError("");
    setMessage("");
    try {
      await switchArea(item.assignment_id);
    } catch (err) {
      setActionError(errorMessage(err, "The area could not be made current."));
    } finally {
      setBusy("");
    }
  }

  async function remove(item) {
    const confirmed = window.confirm(
      `Delete the work area "${areaTitle(item)}"? Its boundary is removed. Reviews and processing jobs already recorded keep their records.`
    );
    if (!confirmed) return;
    setBusy(item.assignment_id);
    setActionError("");
    setMessage("");
    try {
      await deleteWorkArea(item.assignment_id);
      setMessage(`"${areaTitle(item)}" was deleted.`);
      await reloadAreas();
    } catch (err) {
      setActionError(errorMessage(err, "The work area could not be deleted."));
    } finally {
      setBusy("");
    }
  }

  async function saved(area) {
    const editing = mode?.type === "edit";
    setMode(null);
    setMessage(editing ? `"${areaTitle(area)}" was saved.` : `"${areaTitle(area)}" was added and is now your current area.`);
    await reloadAreas();
  }

  const items = areas.items || [];

  return (
    <section className="panel" aria-labelledby="work-areas-title">
      <div className="panel__head">
        <h3 id="work-areas-title">Work areas</h3>
        {!mode ? (
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => setMode({ type: "add" })}>
            <Icon name="plus" size={15} /> Add work area
          </button>
        ) : null}
      </div>
      <div className="panel__body">
        {message ? (
          <div className="notice notice--ok" role="status">
            <Icon name="check" size={16} />
            <p>{message}</p>
          </div>
        ) : null}
        {actionError ? (
          <div className="notice notice--error" role="alert">
            <Icon name="alert" size={16} />
            <p>{actionError}</p>
          </div>
        ) : null}

        {mode ? (
          <>
            <h4 className="work-areas__form-title">
              {mode.type === "edit" ? `Edit "${areaTitle(mode.area)}"` : "Add a work area"}
            </h4>
            <WorkAreaEditor
              area={mode.type === "edit" ? mode.area : null}
              contextBoundary={context}
              contextBbox={assignment?.bbox}
              onSaved={saved}
              onCancel={() => setMode(null)}
            />
          </>
        ) : areas.loading && !items.length ? (
          <LoadingState compact label="Loading your work areas" />
        ) : areas.error && !items.length ? (
          <ErrorState compact title="Work areas unavailable" detail={areas.error} onRetry={reloadAreas} />
        ) : !items.length ? (
          <EmptyState
            icon="target"
            title="No work area yet. Add one to begin."
            detail={
              assignment?.is_demo
                ? "Until you add one, the demo assignment is shown, labelled DEMO. It is not your area."
                : "Draw the boundary on the map, upload it as GeoJSON, or use the map view."
            }
            action={
              <button type="button" className="btn btn--primary btn--sm" onClick={() => setMode({ type: "add" })}>
                <Icon name="plus" size={15} /> Add work area
              </button>
            }
          />
        ) : (
          <ul className="work-areas" aria-busy={switching || Boolean(busy)}>
            {items.map((item) => (
              <li key={item.assignment_id} className={`work-areas__item ${item.is_current ? "is-current" : ""}`}>
                <div className="work-areas__main">
                  <div className="row" style={{ gap: 8 }}>
                    <strong>{areaTitle(item)}</strong>
                    <AreaLabel label={item.label} />
                    {item.is_current ? <Tag tone="info" icon="check">Current</Tag> : null}
                  </div>
                  <span className="muted">
                    {[item.village, item.taluk, item.district, item.state].filter(Boolean).join(", ") || "No place given"}
                    {" · "}
                    {formatArea(item.area_m2)} measured
                    {" · "}
                    <span className="mono">{item.assignment_id}</span>
                  </span>
                </div>
                <div className="work-areas__actions">
                  {!item.is_current ? (
                    <button
                      type="button"
                      className="btn btn--secondary btn--sm"
                      onClick={() => activate(item)}
                      disabled={switching || Boolean(busy)}
                    >
                      {busy === item.assignment_id ? "Switching" : "Activate"}
                    </button>
                  ) : null}
                  {item.editable ? (
                    <>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setMode({ type: "edit", area: item })}
                        disabled={Boolean(busy)}
                        aria-label={`Edit ${areaTitle(item)}`}
                      >
                        <Icon name="pencil" size={15} /> Edit
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm btn--danger"
                        onClick={() => remove(item)}
                        disabled={Boolean(busy)}
                        aria-label={`Delete ${areaTitle(item)}`}
                      >
                        <Icon name="x" size={15} /> Delete
                      </button>
                    </>
                  ) : (
                    <span className="field__hint">From the assignment registry; read-only</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {areas.error && items.length ? (
          <p className="field__error" role="alert">
            {areas.error}{" "}
            <button type="button" className="link-btn" onClick={reloadAreas}>
              Try again
            </button>
          </p>
        ) : null}
      </div>
    </section>
  );
}
