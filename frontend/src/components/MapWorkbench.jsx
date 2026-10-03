import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  errorMessage,
  getAssignedArea,
  getFeature,
  getMapLayers,
  getReferenceFeature,
  isCanceled,
} from "../lib/api";
import { BASEMAPS } from "../lib/constants";
import { formatNumber, formatPercent } from "../lib/format";
import FeaturePanel from "./FeaturePanel";
import Icon from "./Icon";
import LayerControl from "./LayerControl";
import MapView from "./MapView";
import ReferencePanel from "./ReferencePanel";
import { ErrorState, LoadingState } from "./States";
import ThreeDParcelView from "./ThreeDParcelView";

function bboxArea(bbox) {
  return bbox ? Math.max(bbox[2] - bbox[0], 0) * Math.max(bbox[3] - bbox[1], 0) : 0;
}

/**
 * The GIS workbench: map, layer switches, 2D / 3D toggle, and the panel for
 * the selected feature with its review actions. Used on the dashboard, the
 * map page and the review page.
 */
export default function MapWorkbench({
  source,
  height = 620,
  selectRequest = null,
  onReviewed,
  onSelectionChange,
  initialView = "2d",
  allow3d = true,
  layersOpen = true,
}) {
  const [catalog, setCatalog] = useState(null);
  const [boundary, setBoundary] = useState(null);
  const [load, setLoad] = useState({ loading: true, error: "" });
  const [visible, setVisible] = useState({});
  const [basemap, setBasemap] = useState("satellite");
  const [view, setView] = useState(initialView);

  const [selection, setSelection] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState({ loading: false, error: "" });
  const [refreshKey, setRefreshKey] = useState(0);
  const [focus, setFocus] = useState(null);
  const [mapStatus, setMapStatus] = useState({ loading: false, error: "" });

  const [edit, setEdit] = useState(null); // { base, geometry, changed }
  const [picking, setPicking] = useState(false);
  const [pickedPoint, setPickedPoint] = useState(null);

  // Existing GIS records are inspected in their own mode, because they lie
  // under the AI features and both cannot take the same click.
  const [inspect, setInspect] = useState("ai");
  const [reference, setReference] = useState(null); // { dataset, index, properties, geometry }
  const [referenceDetail, setReferenceDetail] = useState(null);
  const [referenceState, setReferenceState] = useState({ loading: false, error: "" });

  const detailRequest = useRef(null);
  const referenceRequest = useRef(null);
  const [attempt, setAttempt] = useState(0);

  // ---- catalogue and assigned boundary --------------------------------
  useEffect(() => {
    let active = true;
    setLoad({ loading: true, error: "" });
    Promise.all([getMapLayers(source), getAssignedArea()])
      .then(([layers, area]) => {
        if (!active) return;
        setCatalog(layers);
        setBoundary(area);
        setVisible((current) => {
          const next = {};
          for (const layer of layers.layers) {
            next[layer.key] =
              layer.key in current ? current[layer.key] && layer.available : layer.available && layer.default_on;
          }
          return next;
        });
        setLoad({ loading: false, error: "" });
      })
      .catch((error) => {
        if (active) setLoad({ loading: false, error: errorMessage(error, "The map layers could not be loaded.") });
      });
    return () => {
      active = false;
    };
  }, [source, attempt]);

  // A different source means different features: drop the selection.
  useEffect(() => {
    setSelection(null);
    setDetail(null);
    setEdit(null);
    setPicking(false);
    setPickedPoint(null);
    setReferenceDetail(null);
  }, [source]);

  // ---- selection detail -------------------------------------------------
  const loadDetail = useCallback(
    async (uid) => {
      detailRequest.current?.abort();
      const controller = new AbortController();
      detailRequest.current = controller;
      setDetailState({ loading: true, error: "" });
      try {
        const data = await getFeature(source, uid, { geometry: "overview" }, controller.signal);
        setDetail(data);
        setDetailState({ loading: false, error: "" });
        return data;
      } catch (error) {
        if (isCanceled(error)) return null;
        setDetailState({ loading: false, error: errorMessage(error) });
        return null;
      }
    },
    [source]
  );

  const loadReference = useCallback(
    async (datasetId, index) => {
      referenceRequest.current?.abort();
      const controller = new AbortController();
      referenceRequest.current = controller;
      setReferenceState({ loading: true, error: "" });
      try {
        const data = await getReferenceFeature(datasetId, index, source, controller.signal);
        setReferenceDetail(data);
        setReferenceState({ loading: false, error: "" });
      } catch (error) {
        if (isCanceled(error)) return;
        setReferenceState({ loading: false, error: errorMessage(error) });
      }
    },
    [source]
  );

  const clearReference = useCallback(() => {
    referenceRequest.current?.abort();
    setReference(null);
    setReferenceDetail(null);
    setReferenceState({ loading: false, error: "" });
  }, []);

  const selectReference = useCallback(
    ({ dataset, feature }) => {
      setSelection(null);
      setDetail(null);
      setEdit(null);
      onSelectionChange?.(null);
      setReference({
        dataset,
        index: feature.id,
        properties: feature.properties || {},
        geometry: feature.geometry,
      });
      setReferenceDetail(null);
      loadReference(dataset.dataset_id, feature.id);
    },
    [loadReference, onSelectionChange]
  );

  // The same record against another source: reload its overlay.
  useEffect(() => {
    if (reference) loadReference(reference.dataset.dataset_id, reference.index);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const select = useCallback(
    (feature) => {
      clearReference();
      setEdit(null);
      setPicking(false);
      setPickedPoint(null);
      if (!feature) {
        setSelection(null);
        setDetail(null);
        onSelectionChange?.(null);
        return;
      }
      setSelection({ id: feature.id, properties: feature.properties, bbox: feature.bbox, geometry: feature.geometry });
      setDetail(null);
      onSelectionChange?.(feature.id);
      loadDetail(feature.id);
    },
    [loadDetail, onSelectionChange, clearReference]
  );

  // Selection requested from outside (for example the review queue).
  useEffect(() => {
    if (!selectRequest?.id) return;
    clearReference();
    setInspect("ai");
    setView("2d");
    setEdit(null);
    setPicking(false);
    setPickedPoint(null);
    setSelection({ id: selectRequest.id, properties: {}, bbox: selectRequest.bbox, geometry: null });
    setDetail(null);
    if (selectRequest.bbox) {
      setFocus({ bbox: selectRequest.bbox, nonce: `${selectRequest.id}-${selectRequest.nonce}` });
    }
    loadDetail(selectRequest.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectRequest?.id, selectRequest?.nonce]);

  const zoomTo = useCallback((bbox, options = {}) => {
    if (bbox) setFocus({ bbox, nonce: `${bbox.join(",")}-${Date.now()}`, ...options });
  }, []);

  // ---- review callbacks ---------------------------------------------------
  const handleSubmitted = useCallback(
    (review) => {
      setEdit(null);
      setPicking(false);
      setPickedPoint(null);
      setRefreshKey((value) => value + 1);
      if (selection?.id) loadDetail(selection.id);
      onReviewed?.(review);
    },
    [selection?.id, loadDetail, onReviewed]
  );

  const startEdit = useCallback(() => {
    if (!detail?.geometry || detail.geometry.type !== "Polygon") return;
    setPicking(false);
    setEdit({ base: detail.geometry, geometry: detail.geometry, changed: false });
    if (detail.bbox) zoomTo(detail.bbox, { padding: [90, 90] });
  }, [detail, zoomTo]);

  const handleEditChange = useCallback((geometry) => {
    setEdit((current) => (current ? { ...current, geometry, changed: true } : current));
  }, []);

  const handlePick = useCallback((latlng) => {
    setPickedPoint({ lat: latlng.lat, lng: latlng.lng });
    setPicking(false);
  }, []);

  const toggleLayer = useCallback((key, value) => {
    setVisible((current) => ({ ...current, [key]: value }));
  }, []);

  const handleStatus = useCallback((status) => {
    setMapStatus((current) => ({ ...current, ...status }));
  }, []);

  // ---- derived ------------------------------------------------------------
  const selectedGeometry = reference
    ? reference.geometry
    : detail?.geometry || selection?.geometry || null;
  const referenceLayer = catalog?.layers?.find((layer) => layer.key === "existing_gis");
  const referenceVisible = Boolean(referenceLayer?.available && visible.existing_gis);
  const inspectMode = referenceVisible ? inspect : "ai";
  const coverage = useMemo(() => {
    const assigned = bboxArea(catalog?.assignment_bbox);
    const data = bboxArea(catalog?.data_bbox);
    return assigned > 0 && data > 0 ? data / assigned : null;
  }, [catalog]);
  const hasData = Boolean(catalog?.data_bbox);
  const showPanel = Boolean(selection) && view === "2d";
  const showReferencePanel = Boolean(reference) && !selection && view === "2d";

  if (load.loading && !catalog) {
    return (
      <div className="workbench" style={{ height }}>
        <LoadingState
          label="Loading spatial layers"
          detail="The first load after a start indexes the layer files and can take about half a minute."
        />
      </div>
    );
  }
  if (load.error && !catalog) {
    return (
      <div className="workbench" style={{ height }}>
        <ErrorState title="The map could not be loaded" detail={load.error} onRetry={() => setAttempt((n) => n + 1)} />
      </div>
    );
  }

  return (
    <div
      className={`workbench ${showPanel || showReferencePanel ? "workbench--with-panel" : ""}`}
      style={{ height }}
    >
      <div className="map-stage">
        {view === "3d" ? (
          <ThreeDParcelView source={source} catalog={catalog} boundary={boundary} />
        ) : (
          <>
            <MapView
              source={source}
              catalog={catalog}
              visible={visible}
              basemap={basemap}
              boundary={boundary}
              initialBbox={catalog?.assignment_bbox || catalog?.data_bbox}
              focus={focus}
              selectedGeometry={selectedGeometry}
              refreshKey={refreshKey}
              editGeometry={edit?.base || null}
              onEditChange={handleEditChange}
              picking={picking}
              pickedPoint={pickedPoint}
              onPick={handlePick}
              onSelect={select}
              inspect={inspectMode}
              onReferenceSelect={selectReference}
              onStatus={handleStatus}
            />

            <div className="map-overlay map-overlay--tl">
              <LayerControl catalog={catalog} visible={visible} onToggle={toggleLayer} defaultOpen={layersOpen} />
            </div>
          </>
        )}

        <div className="map-overlay map-overlay--tr map-toolbar">
          {allow3d ? (
            <div className="btn-group map-card" role="group" aria-label="Map view">
              <button type="button" className={`btn ${view === "2d" ? "is-active" : ""}`} onClick={() => setView("2d")}>
                <Icon name="map" size={15} /> 2D GIS
              </button>
              <button type="button" className={`btn ${view === "3d" ? "is-active" : ""}`} onClick={() => setView("3d")}>
                <Icon name="box" size={15} /> 3D terrain
              </button>
            </div>
          ) : null}

          {view === "2d" ? (
            <>
              <div className="btn-group map-card" role="group" aria-label="Basemap">
                {Object.entries(BASEMAPS).map(([key, config]) => (
                  <button
                    key={key}
                    type="button"
                    className={`btn ${basemap === key ? "is-active" : ""}`}
                    onClick={() => setBasemap(key)}
                  >
                    {config.label}
                  </button>
                ))}
              </div>
              {referenceVisible ? (
                <div className="btn-group map-card" role="group" aria-label="Click selects">
                  <button
                    type="button"
                    className={`btn ${inspectMode === "ai" ? "is-active" : ""}`}
                    aria-pressed={inspectMode === "ai"}
                    onClick={() => {
                      setInspect("ai");
                      clearReference();
                    }}
                  >
                    <Icon name="cpu" size={15} /> Select AI features
                  </button>
                  <button
                    type="button"
                    className={`btn ${inspectMode === "reference" ? "is-active" : ""}`}
                    aria-pressed={inspectMode === "reference"}
                    onClick={() => {
                      setInspect("reference");
                      select(null);
                    }}
                  >
                    <Icon name="shapes" size={15} /> Select existing GIS
                  </button>
                </div>
              ) : null}
              <div className="btn-group map-card" role="group" aria-label="Zoom to">
                <button
                  type="button"
                  className="btn"
                  disabled={!catalog?.assignment_bbox}
                  onClick={() => zoomTo(catalog.assignment_bbox, { padding: [28, 28] })}
                >
                  <Icon name="zoom-fit" size={15} /> Assigned area
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={!hasData}
                  onClick={() => zoomTo(catalog.data_bbox, { padding: [40, 40] })}
                >
                  <Icon name="target" size={15} /> Mapped data
                </button>
              </div>
            </>
          ) : null}
        </div>

        {view === "2d" ? (
          <div className="map-overlay map-overlay--bc">
            {mapStatus.error ? (
              <div className="map-card map-chip" role="alert" style={{ color: "#ffd0ca" }}>
                <Icon name="alert" size={15} /> {mapStatus.error}
              </div>
            ) : mapStatus.loading ? (
              <div className="map-card map-chip" role="status">
                <span className="spinner" style={{ width: 14, height: 14 }} /> Loading features for this view
              </div>
            ) : picking ? (
              <div className="map-card map-chip" role="status">
                <Icon name="target" size={15} /> Click the map to place the ground-truth point
              </div>
            ) : edit ? (
              <div className="map-card map-chip" role="status">
                <Icon name="pencil" size={15} /> Editing the outline of <strong>{selection?.id}</strong>
              </div>
            ) : inspectMode === "reference" && !reference ? (
              <div className="map-card map-chip" role="status">
                <Icon name="shapes" size={15} /> Click a parcel of the existing GIS layer to see the AI features inside it
              </div>
            ) : coverage != null && coverage < 0.25 && !selection && !reference ? (
              <div className="map-card map-chip">
                Mapped data covers <strong>{formatPercent(coverage)}</strong> of the assigned area
                <button type="button" className="btn btn--secondary" onClick={() => zoomTo(catalog.data_bbox, { padding: [40, 40] })}>
                  Zoom to data
                </button>
              </div>
            ) : mapStatus.truncated ? (
              <div className="map-card map-chip" role="status">
                <Icon name="info" size={15} /> Showing the {formatNumber(mapStatus.drawn)} largest features in view. Zoom in to see the rest.
              </div>
            ) : !hasData ? (
              <div className="map-card map-chip" role="status">
                <Icon name="info" size={15} /> No AI layers for this source yet. Data unavailable.
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {showPanel ? (
        <FeaturePanel
          source={source}
          selection={selection}
          detail={detail}
          loading={detailState.loading}
          error={detailState.error}
          onRetry={() => loadDetail(selection.id)}
          onClose={() => select(null)}
          onZoom={() => zoomTo(detail?.bbox || selection?.bbox)}
          review={{
            edit,
            onStartEdit: startEdit,
            onCancelEdit: () => setEdit(null),
            picking,
            pickedPoint,
            onTogglePick: () => setPicking((value) => !value),
            onClearPick: () => {
              setPicking(false);
              setPickedPoint(null);
            },
            onSubmitted: handleSubmitted,
          }}
        />
      ) : null}

      {showReferencePanel ? (
        <ReferencePanel
          selection={reference}
          detail={referenceDetail}
          loading={referenceState.loading}
          error={referenceState.error}
          onRetry={() => loadReference(reference.dataset.dataset_id, reference.index)}
          onClose={clearReference}
          onZoom={() => zoomTo(referenceDetail?.bbox)}
        />
      ) : null}
    </div>
  );
}
