import { useEffect, useId, useRef, useState } from "react";

import { createWorkArea, errorMessage, isCanceled, measureBoundary, updateWorkArea } from "../lib/api";
import { formatArea, formatLength, formatNumber } from "../lib/format";
import {
  PLACE_FIELDS,
  areaFieldsPayload,
  parseBoundaryFile,
  polygonFromPoints,
  rectangleFromBounds,
  validateAreaFields,
} from "../lib/workArea";
import Icon from "./Icon";
import WorkAreaMap from "./WorkAreaMap";

const METHODS = [
  { key: "draw", label: "Draw on the map", icon: "pencil" },
  { key: "upload", label: "Upload GeoJSON", icon: "upload" },
  { key: "view", label: "Use the map view", icon: "zoom-fit" },
];

const FIELD_LABELS = { name: "Name", state: "State", district: "District", taluk: "Taluk", village: "Village" };

function bboxOf(geometry) {
  if (!geometry) return null;
  const rings = geometry.type === "Polygon" ? geometry.coordinates : geometry.coordinates.flat();
  let box = [Infinity, Infinity, -Infinity, -Infinity];
  for (const ring of rings) {
    for (const [x, y] of ring) {
      box = [Math.min(box[0], x), Math.min(box[1], y), Math.max(box[2], x), Math.max(box[3], y)];
    }
  }
  return Number.isFinite(box[0]) ? box : null;
}

/**
 * Create or edit a self-declared work area. The boundary is drawn, uploaded
 * or taken from the map view; the server checks and measures it, and the
 * measurement shown is the server's.
 */
export default function WorkAreaEditor({ area, contextBoundary, contextBbox, onSaved, onCancel }) {
  const editingExisting = Boolean(area);
  const ids = { name: useId(), state: useId(), district: useId(), taluk: useId(), village: useId(), file: useId() };
  const refs = { name: useRef(null), state: useRef(null), district: useRef(null), taluk: useRef(null), village: useRef(null) };
  const statusId = useId();
  const map = useRef(null);
  const fileInput = useRef(null);

  const [fields, setFields] = useState(() => ({
    name: area?.name || "",
    state: area?.state || "",
    district: area?.district || "",
    taluk: area?.taluk || "",
    village: area?.village || "",
  }));
  const [fieldErrors, setFieldErrors] = useState({});
  const [method, setMethod] = useState(area?.origin === "uploaded" ? "upload" : "draw");
  const [origin, setOrigin] = useState(area?.origin || "drawn");

  // `boundary` is what will be sent; `editStart` is the outline the vertex
  // editor started from (kept stable while the surveyor drags vertices).
  const [boundary, setBoundary] = useState(area?.geometry || null);
  const [boundaryChanged, setBoundaryChanged] = useState(false);
  const [editStart, setEditStart] = useState(area?.geometry?.type === "Polygon" ? area.geometry : null);
  const [preview, setPreview] = useState(area?.geometry?.type === "MultiPolygon" ? area.geometry : null);
  const [rawUpload, setRawUpload] = useState(false);
  const [rectangle, setRectangle] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [points, setPoints] = useState([]);
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState("");

  const [measure, setMeasure] = useState({ loading: false, error: "", data: null });
  const [measureNonce, setMeasureNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [focus, setFocus] = useState(() => {
    const bbox = bboxOf(area?.geometry) || contextBbox;
    return bbox ? { bbox, nonce: 1 } : null;
  });

  // Ask the server to check and measure the boundary whenever it changes.
  useEffect(() => {
    if (!boundary) {
      setMeasure({ loading: false, error: "", data: null });
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setMeasure((current) => ({ ...current, loading: true, error: "" }));
      try {
        const data = await measureBoundary(boundary, controller.signal);
        setMeasure({ loading: false, error: "", data });
        if (rawUpload) {
          // The server has reprojected the file to longitude/latitude.
          setRawUpload(false);
          setBoundary(data.geometry);
          setEditStart(data.geometry.type === "Polygon" ? data.geometry : null);
          setPreview(data.geometry.type === "Polygon" ? null : data.geometry);
          setFocus({ bbox: bboxOf(data.geometry), nonce: Date.now() });
        }
      } catch (err) {
        if (isCanceled(err)) return;
        setMeasure({ loading: false, error: errorMessage(err, "The boundary could not be checked."), data: null });
      }
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [boundary, rawUpload, measureNonce]);

  function applyBoundary(next, { nextOrigin, isRectangle = false, raw = false }) {
    setBoundary(next);
    setBoundaryChanged(true);
    setOrigin(nextOrigin);
    setRectangle(isRectangle);
    setRawUpload(raw);
    setSaveError("");
    if (!raw) {
      setEditStart(next?.type === "Polygon" ? next : null);
      setPreview(next && next.type !== "Polygon" ? next : null);
    } else {
      setEditStart(null);
      setPreview(null);
    }
  }

  function chooseMethod(next) {
    setMethod(next);
    setDrawing(false);
    setPoints([]);
    setFileError("");
  }

  // ---- draw
  function startDrawing() {
    setDrawing(true);
    setPoints([]);
    setBoundary(null);
    setEditStart(null);
    setPreview(null);
    setMeasure({ loading: false, error: "", data: null });
  }

  function finishDrawing() {
    const polygon = polygonFromPoints(points);
    if (!polygon) return;
    setDrawing(false);
    setPoints([]);
    applyBoundary(polygon, { nextOrigin: "drawn" });
  }

  // ---- upload
  async function handleFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setFileName(file.name);
    setFileError("");
    try {
      const parsed = parseBoundaryFile(await file.text());
      applyBoundary(parsed, { nextOrigin: "uploaded", raw: true });
    } catch (err) {
      setFileError(err.message);
    }
  }

  // ---- map view
  function takeMapView() {
    const bounds = map.current?.getBounds();
    if (!bounds) return;
    applyBoundary(
      rectangleFromBounds({
        west: bounds.getWest(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        north: bounds.getNorth(),
      }),
      { nextOrigin: "drawn", isRectangle: true }
    );
  }

  function handleEdit(next) {
    setBoundary(next);
    setBoundaryChanged(true);
    setSaveError("");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const errors = validateAreaFields(fields);
    setFieldErrors(errors);
    const first = ["name", ...PLACE_FIELDS].find((key) => errors[key]);
    if (first) {
      refs[first].current?.focus();
      return;
    }
    if (!boundary) {
      setSaveError("Give the work area a boundary: draw it, upload a GeoJSON file or use the map view.");
      return;
    }
    if (measure.loading || rawUpload) {
      setSaveError("The boundary is still being checked. Wait a moment and save again.");
      return;
    }
    if (measure.error) {
      setSaveError(`Correct the boundary first: ${measure.error}`);
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      const body = { ...areaFieldsPayload(fields) };
      if (!editingExisting || boundaryChanged) {
        body.boundary = boundary;
        body.origin = origin;
      }
      const saved = editingExisting
        ? await updateWorkArea(area.assignment_id, body)
        : await createWorkArea({ ...body, activate: true });
      await onSaved?.(saved);
    } catch (err) {
      setSaveError(errorMessage(err, "The work area could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  const measured = measure.data;

  return (
    <form className="work-area-editor" onSubmit={handleSubmit} noValidate aria-describedby={statusId}>
      <div className="work-area-editor__map">
        <div className="segmented" role="tablist" aria-label="How to give the boundary">
          {METHODS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={method === item.key}
              className={`segmented__item ${method === item.key ? "is-active" : ""}`}
              onClick={() => chooseMethod(item.key)}
            >
              <Icon name={item.icon} size={15} /> {item.label}
            </button>
          ))}
        </div>

        <div className="work-area-editor__tools" aria-live="polite">
          {method === "draw" ? (
            drawing ? (
              <>
                <span className="field__hint">
                  Click the map to place each corner ({points.length} placed). Finish when the outline is complete.
                </span>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPoints((p) => p.slice(0, -1))} disabled={!points.length}>
                  <Icon name="undo" size={15} /> Undo corner
                </button>
                <button type="button" className="btn btn--primary btn--sm" onClick={finishDrawing} disabled={points.length < 3}>
                  <Icon name="check" size={15} /> Finish outline
                </button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setDrawing(false); setPoints([]); }}>
                  Cancel drawing
                </button>
              </>
            ) : (
              <>
                <span className="field__hint">
                  {boundary
                    ? "Drag a corner to move it, click an edge to add one, right-click a corner to remove it."
                    : "Zoom to the area, then start drawing."}
                </span>
                <button type="button" className="btn btn--secondary btn--sm" onClick={startDrawing}>
                  <Icon name="pencil" size={15} /> {boundary ? "Draw again" : "Start drawing"}
                </button>
              </>
            )
          ) : null}

          {method === "upload" ? (
            <>
              <input
                ref={fileInput}
                id={ids.file}
                type="file"
                className="sr-only"
                accept=".geojson,.json,application/geo+json,application/json"
                onChange={handleFile}
              />
              <button type="button" className="btn btn--secondary btn--sm" onClick={() => fileInput.current?.click()}>
                <Icon name="upload" size={15} /> Choose a GeoJSON file
              </button>
              <span className="field__hint">
                {fileName ? `Selected: ${fileName}. ` : ""}One polygon; a declared CRS is reprojected by the server.
              </span>
            </>
          ) : null}

          {method === "view" ? (
            <>
              <button type="button" className="btn btn--secondary btn--sm" onClick={takeMapView}>
                <Icon name="zoom-fit" size={15} /> Use the current map view
              </button>
              <span className="field__hint">Takes the visible map as a rough rectangle. Adjust its corners afterwards.</span>
            </>
          ) : null}
        </div>

        {fileError ? (
          <div className="notice notice--error" role="alert">
            <Icon name="alert" size={16} />
            <p>{fileError}</p>
          </div>
        ) : null}

        <WorkAreaMap
          contextBoundary={contextBoundary}
          drawing={drawing}
          points={points}
          onAddPoint={(point) => setPoints((current) => [...current, point])}
          editGeometry={editStart}
          onEditChange={handleEdit}
          previewGeometry={preview}
          focus={focus}
          onReady={(instance) => {
            map.current = instance;
          }}
        />

        <div className="work-area-editor__measure" id={statusId} role="status" aria-live="polite">
          {!boundary ? (
            <span className="muted">No boundary yet.</span>
          ) : measure.loading || rawUpload ? (
            <span className="muted">Checking the boundary on the server…</span>
          ) : measure.error ? (
            <span className="field__error">
              {measure.error}{" "}
              <button type="button" className="link-btn" onClick={() => setMeasureNonce((n) => n + 1)}>
                Check again
              </button>
            </span>
          ) : measured ? (
            <span>
              <strong>Measured by the server:</strong> {formatArea(measured.area_m2)}, perimeter{" "}
              {formatLength(measured.perimeter_m)}, {formatNumber(measured.vertices)} vertices, in {measured.metric_crs}.
              {rectangle || measured.notes?.length ? (
                <span className="badge badge--review badge--sm" style={{ marginLeft: 8 }}>
                  Rough rectangle, not a surveyed outline
                </span>
              ) : null}
            </span>
          ) : null}
        </div>
      </div>

      <div className="work-area-editor__fields">
        {["name", ...PLACE_FIELDS].map((key) => (
          <div className="field" key={key}>
            <label htmlFor={ids[key]}>
              {FIELD_LABELS[key]}
              {key === "name" ? " (required)" : ""}
            </label>
            <input
              ref={refs[key]}
              id={ids[key]}
              className="input"
              type="text"
              value={fields[key]}
              onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))}
              aria-invalid={Boolean(fieldErrors[key])}
              aria-describedby={fieldErrors[key] ? `${ids[key]}-error` : undefined}
              autoComplete="off"
            />
            {fieldErrors[key] ? (
              <span className="field__error" id={`${ids[key]}-error`}>
                {fieldErrors[key]}
              </span>
            ) : null}
          </div>
        ))}

        <p className="field__hint">
          A work area you add is labelled <strong>self-declared</strong>. It is not an official survey assignment.
        </p>

        {saveError ? (
          <div className="notice notice--error" role="alert">
            <Icon name="alert" size={16} />
            <p>{saveError}</p>
          </div>
        ) : null}

        <div className="row">
          <button type="submit" className="btn btn--primary" disabled={saving}>
            {saving ? "Saving" : editingExisting ? "Save changes" : "Add work area"}
          </button>
          <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    </form>
  );
}
