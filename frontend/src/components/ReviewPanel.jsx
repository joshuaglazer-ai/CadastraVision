import { useEffect, useState } from "react";
import { errorMessage, submitReview } from "../lib/api";
import { OBSERVED_CLASSES, REVIEW_ACTIONS } from "../lib/constants";
import { formatArea, formatCoordinate } from "../lib/format";
import Icon from "./Icon";

const MAX_EDIT_VERTICES = 300;

function exteriorVertices(geometry) {
  if (!geometry || geometry.type !== "Polygon") return null;
  return (geometry.coordinates?.[0]?.length || 1) - 1;
}

/**
 * The surveyor's decision on one feature: approve, edit the outline, flag,
 * reject, or add ground truth. The surveyor is taken from the session on
 * the server; nothing here sends an identity.
 */
export default function ReviewPanel({
  source,
  feature,
  edit,
  onStartEdit,
  onCancelEdit,
  picking,
  pickedPoint,
  onTogglePick,
  onClearPick,
  onSubmitted,
}) {
  const [mode, setMode] = useState(null); // null | flag | reject | add_ground_truth | approve
  const [comment, setComment] = useState("");
  const [truth, setTruth] = useState({ observed_class: "", latitude: "", longitude: "", accuracy_m: "", device: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const uid = feature?.id;

  useEffect(() => {
    setMode(null);
    setComment("");
    setError("");
    setDone("");
    setTruth({ observed_class: "", latitude: "", longitude: "", accuracy_m: "", device: "" });
  }, [uid]);

  // A point picked on the map fills the GNSS position.
  useEffect(() => {
    if (pickedPoint) {
      setTruth((current) => ({
        ...current,
        latitude: formatCoordinate(pickedPoint.lat, 7),
        longitude: formatCoordinate(pickedPoint.lng, 7),
      }));
    }
  }, [pickedPoint]);

  if (!feature) return null;

  const geometry = feature.geometry;
  const vertexCount = exteriorVertices(geometry);
  const canEdit = vertexCount != null && vertexCount <= MAX_EDIT_VERTICES;
  const editReason =
    geometry?.type !== "Polygon"
      ? "This feature has several separate parts and cannot be edited as one outline here."
      : `This outline has ${vertexCount?.toLocaleString()} vertices, too many to adjust by hand. Flag it for re-survey or subdivision instead.`;

  async function send(action, extra = {}) {
    setBusy(true);
    setError("");
    setDone("");
    try {
      const review = await submitReview(source, {
        feature_id: uid,
        action,
        comment: comment.trim(),
        ...extra,
      });
      const label = REVIEW_ACTIONS.find((item) => item.key === action)?.done || "Saved";
      setDone(`${label}. Recorded as ${review.review_id}.`);
      setMode(null);
      setComment("");
      onClearPick?.();
      onSubmitted?.(review);
    } catch (err) {
      setError(errorMessage(err, "The review could not be saved."));
    } finally {
      setBusy(false);
    }
  }

  function submitTruth(event) {
    event.preventDefault();
    const payload = {};
    if (truth.observed_class) payload.observed_class = truth.observed_class;
    if (truth.latitude !== "" || truth.longitude !== "") {
      payload.latitude = truth.latitude;
      payload.longitude = truth.longitude;
    }
    if (truth.accuracy_m !== "") payload.accuracy_m = truth.accuracy_m;
    if (truth.device) payload.device = truth.device;
    if (comment.trim()) payload.observation = comment.trim();
    send("add_ground_truth", { ground_truth: payload });
  }

  // ---- editing the outline on the map ---------------------------------
  if (edit) {
    return (
      <section aria-label="Edit outline">
        <h4>Edit outline</h4>
        <div className="notice">
          <Icon name="pencil" size={16} />
          <p>
            Drag a white handle to move a corner. Click the dashed line to add a corner. Right-click
            a handle to remove it. Interior holes are kept as they are.
          </p>
        </div>
        <dl className="kv">
          <dt>Corners</dt>
          <dd>{(edit.geometry.coordinates[0].length - 1).toLocaleString()}</dd>
          <dt>Outline changed</dt>
          <dd>{edit.changed ? "Yes" : "Not yet"}</dd>
          <dt>Original area</dt>
          <dd>{formatArea(feature.properties.area_m2)}</dd>
        </dl>
        <div className="field">
          <label htmlFor="edit-comment">Reason for the change</label>
          <textarea
            id="edit-comment"
            className="textarea"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="For example: boundary moved to the fence line seen on site"
          />
        </div>
        {error ? (
          <div className="notice notice--error" role="alert">
            <Icon name="alert" size={16} />
            <p>{error}</p>
          </div>
        ) : null}
        <div className="row">
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy || !edit.changed}
            onClick={() => send("edit", { edited_geometry: edit.geometry })}
          >
            <Icon name="check" size={16} /> {busy ? "Saving" : "Save edited outline"}
          </button>
          <button type="button" className="btn btn--secondary" disabled={busy} onClick={onCancelEdit}>
            Cancel
          </button>
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Surveyor review">
      <h4>Surveyor review</h4>

      {done ? (
        <div className="notice notice--ok" role="status">
          <Icon name="check" size={16} />
          <p>{done}</p>
        </div>
      ) : null}
      {error ? (
        <div className="notice notice--error" role="alert">
          <Icon name="alert" size={16} />
          <p>{error}</p>
        </div>
      ) : null}

      {mode === null ? (
        <div className="review-actions">
          <button type="button" className="btn btn--approve" disabled={busy} onClick={() => setMode("approve")}>
            <Icon name="check" size={16} /> Approve
          </button>
          <button
            type="button"
            className="btn btn--secondary"
            disabled={busy || !canEdit}
            title={canEdit ? "Adjust the outline on the map" : editReason}
            onClick={onStartEdit}
          >
            <Icon name="pencil" size={16} /> Edit outline
          </button>
          <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => setMode("flag")}>
            <Icon name="flag" size={16} /> Flag
          </button>
          <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => setMode("reject")}>
            <Icon name="x" size={16} /> Reject
          </button>
          <button
            type="button"
            className="btn btn--secondary btn--wide"
            disabled={busy}
            onClick={() => setMode("add_ground_truth")}
          >
            <Icon name="crosshair" size={16} /> Add ground truth
          </button>
          {!canEdit ? <p className="field__hint" style={{ gridColumn: "1 / -1" }}>{editReason}</p> : null}
        </div>
      ) : null}

      {mode === "approve" || mode === "flag" || mode === "reject" ? (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            send(mode);
          }}
        >
          <div className="field">
            <label htmlFor="review-comment">
              {mode === "approve" ? "Comment (optional)" : "Reason (required)"}
            </label>
            <textarea
              id="review-comment"
              className="textarea"
              value={comment}
              required={mode !== "approve"}
              onChange={(event) => setComment(event.target.value)}
              placeholder={
                mode === "approve"
                  ? "For example: checked against the field visit"
                  : mode === "flag"
                    ? "What needs another look?"
                    : "Why is this feature wrong?"
              }
            />
          </div>
          <div className="row">
            <button
              type="submit"
              className={`btn ${mode === "approve" ? "btn--approve" : mode === "reject" ? "btn--danger" : "btn--primary"}`}
              disabled={busy || (mode !== "approve" && !comment.trim())}
            >
              {busy
                ? "Saving"
                : mode === "approve"
                  ? "Approve feature"
                  : mode === "flag"
                    ? "Flag feature"
                    : "Reject feature"}
            </button>
            <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {mode === "add_ground_truth" ? (
        <form className="stack" onSubmit={submitTruth}>
          <div className="field">
            <label htmlFor="truth-class">Observed on the ground</label>
            <select
              id="truth-class"
              className="select"
              value={truth.observed_class}
              onChange={(event) => setTruth({ ...truth, observed_class: event.target.value })}
            >
              <option value="">Choose a class</option>
              {OBSERVED_CLASSES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <span className="field__hint">
              The model predicted {feature.properties.class_name}. Ground truth is stored separately
              from the prediction.
            </span>
          </div>

          <div className="grid grid--2" style={{ gap: 10 }}>
            <div className="field">
              <label htmlFor="truth-lat">GNSS latitude</label>
              <input
                id="truth-lat"
                className="input mono"
                inputMode="decimal"
                value={truth.latitude}
                onChange={(event) => setTruth({ ...truth, latitude: event.target.value })}
                placeholder="28.559000"
              />
            </div>
            <div className="field">
              <label htmlFor="truth-lon">GNSS longitude</label>
              <input
                id="truth-lon"
                className="input mono"
                inputMode="decimal"
                value={truth.longitude}
                onChange={(event) => setTruth({ ...truth, longitude: event.target.value })}
                placeholder="77.625400"
              />
            </div>
          </div>
          <div className="row">
            <button
              type="button"
              className={`btn btn--secondary btn--sm ${picking ? "is-active" : ""}`}
              onClick={onTogglePick}
            >
              <Icon name="target" size={15} /> {picking ? "Click the map to set the point" : "Pick the point on the map"}
            </button>
          </div>

          <div className="grid grid--2" style={{ gap: 10 }}>
            <div className="field">
              <label htmlFor="truth-accuracy">Accuracy (m)</label>
              <input
                id="truth-accuracy"
                className="input"
                inputMode="decimal"
                value={truth.accuracy_m}
                onChange={(event) => setTruth({ ...truth, accuracy_m: event.target.value })}
                placeholder="0.03"
              />
            </div>
            <div className="field">
              <label htmlFor="truth-device">Device</label>
              <input
                id="truth-device"
                className="input"
                value={truth.device}
                onChange={(event) => setTruth({ ...truth, device: event.target.value })}
                placeholder="GNSS rover"
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="truth-note">Observation</label>
            <textarea
              id="truth-note"
              className="textarea"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="What you saw on site"
            />
          </div>

          <div className="row">
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy ? "Saving" : "Add ground truth"}
            </button>
            <button
              type="button"
              className="btn btn--secondary"
              disabled={busy}
              onClick={() => {
                setMode(null);
                onClearPick?.();
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
