import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { MapContainer, TileLayer, useMap, useMapEvents } from "react-leaflet";

import {
  getDatasetOverlay,
  getGnssPoints,
  getMapFeatures,
  getMapParcels,
  getMapPlots,
  getTerrainGrid,
  isCanceled,
} from "../lib/api";
import { BASEMAPS, CLASS_STYLE, LANDCOVER_CLASSES, STATUS } from "../lib/constants";
import { bboxString, formatArea } from "../lib/format";

// Drawing order, bottom to top.
const DRAW_ORDER = ["field", "parcels", "plots", "water", "other", "road", "building"];

const INDIA_VIEW = { center: [22.8, 79.5], zoom: 5 };

function boundsFromBbox(bbox) {
  return L.latLngBounds([bbox[1], bbox[0]], [bbox[3], bbox[2]]);
}

/** Style of one feature: class colour, with the stroke telling the status. */
function featureStyle(feature, layerKey) {
  const properties = feature.properties || {};
  const base = CLASS_STYLE[layerKey] || CLASS_STYLE.unknown;
  const status = properties.verification_status;
  const style = {
    color: base.color,
    weight: base.weight,
    opacity: 0.95,
    fillColor: base.fill,
    fillOpacity: base.fillOpacity,
    dashArray: null,
  };

  if (status === "SURVEYOR_VERIFIED" || status === "EDITED") {
    style.color = "#3fd6a3";
    style.weight = base.weight + 1.4;
  } else if (status === "FLAGGED" || status === "REJECTED") {
    style.color = "#ff7a6b";
    style.weight = base.weight + 1.2;
    style.dashArray = STATUS.FLAGGED.dash;
    style.fillOpacity = base.fillOpacity * 0.5;
  } else if (status === "REVIEW_REQUIRED") {
    style.dashArray = STATUS.REVIEW_REQUIRED.dash;
    style.weight = base.weight + 0.6;
    if (properties.review_priority === "High") style.color = "#f2b544";
  }
  return style;
}

function tooltipText(feature) {
  const p = feature.properties || {};
  const status = STATUS[p.verification_status]?.label || "AI generated";
  return `<strong>${p.uid}</strong><br/>${p.class_name || ""} · ${formatArea(p.area_m2, "")}<br/>${status}`;
}

// ------------------------------------------------------------- basemap
export function Basemap({ basemap }) {
  const config = BASEMAPS[basemap] || BASEMAPS.satellite;
  if (!config.url) return null;
  return (
    <TileLayer
      key={basemap}
      url={config.url}
      attribution={config.attribution}
      maxZoom={22}
      maxNativeZoom={config.maxNativeZoom}
    />
  );
}

// ------------------------------------------------------- map furniture
export function MapFurniture({ onReady }) {
  const map = useMap();

  useEffect(() => {
    // Panes keep the stacking explicit: boundary under features, the
    // selection and the edit handles above them.
    const panes = [
      ["cv-raster", 350],
      ["cv-boundary", 380],
      ["cv-reference", 390],
      ["cv-selection", 450],
      ["cv-points", 460],
      ["cv-edit", 470],
      ["cv-handles", 480],
    ];
    for (const [name, zIndex] of panes) {
      if (!map.getPane(name)) {
        map.createPane(name).style.zIndex = String(zIndex);
      }
    }
    map.getPane("cv-selection").style.pointerEvents = "none";
    map.getPane("cv-boundary").style.pointerEvents = "none";
    map.getPane("cv-raster").style.pointerEvents = "none";
    map.getPane("cv-reference").style.pointerEvents = "none";

    const zoom = L.control.zoom({ position: "bottomright" }).addTo(map);
    const scale = L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);

    // The map shares its row with the feature panel; keep Leaflet's idea of
    // its size in step when that panel opens or closes.
    let observer = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
      observer.observe(map.getContainer());
    }
    onReady?.(map);
    return () => {
      observer?.disconnect();
      zoom.remove();
      scale.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  return null;
}

function Readout() {
  const [position, setPosition] = useState(null);
  const [zoom, setZoom] = useState(null);
  const map = useMapEvents({
    mousemove: (event) => setPosition(event.latlng),
    mouseout: () => setPosition(null),
    zoomend: () => setZoom(map.getZoom()),
  });

  useEffect(() => {
    setZoom(map.getZoom());
  }, [map]);

  return (
    <div className="map-overlay map-overlay--bl map-card map-readout" style={{ left: 130, pointerEvents: "none" }}>
      {position
        ? `${position.lat.toFixed(6)}° N  ${position.lng.toFixed(6)}° E`
        : "Move the pointer over the map"}
      {zoom != null ? `   z${Number(zoom).toFixed(0)}` : ""}
    </div>
  );
}

// ---------------------------------------------------- assigned boundary
export function BoundaryLayer({ boundary, visible }) {
  const map = useMap();

  useEffect(() => {
    if (!visible || !boundary?.features?.length) return undefined;
    const properties = boundary.features[0]?.properties || {};
    const demo = Boolean(properties.is_demo);
    const selfDeclared = properties.label === "SELF-DECLARED WORK AREA";
    const layer = L.geoJSON(boundary, {
      pane: "cv-boundary",
      interactive: false,
      style: {
        color: "#5fe0e6",
        weight: 2,
        dashArray: "10 6",
        fillColor: "#5fe0e6",
        fillOpacity: 0.035,
      },
    }).addTo(map);

    const bounds = layer.getBounds();
    let label = null;
    if (bounds.isValid()) {
      label = L.tooltip({
        permanent: true,
        direction: "bottom",
        className: "cv-boundary-label",
        pane: "cv-boundary",
        offset: [0, 4],
      })
        .setLatLng([bounds.getNorth(), bounds.getCenter().lng])
        .setContent(
          demo
            ? "Assigned area · demo assignment"
            : selfDeclared
              ? "Work area · self-declared, not official"
              : "Assigned area"
        )
        .addTo(map);
    }
    return () => {
      layer.remove();
      label?.remove();
    };
  }, [map, boundary, visible]);

  return null;
}

// ------------------------------------------------------------ features
function FeatureLayers({
  source,
  catalog,
  visible,
  refreshKey,
  interactive,
  onSelect,
  onStatus,
  registry,
}) {
  const map = useMap();
  const groups = useRef({});
  const renderer = useRef(null);
  const last = useRef(null);
  const controller = useRef(null);
  const timer = useRef(null);
  const interactiveRef = useRef(interactive);
  const onSelectRef = useRef(onSelect);
  interactiveRef.current = interactive;
  onSelectRef.current = onSelect;

  const available = useMemo(() => {
    const result = {};
    for (const layer of catalog?.layers || []) result[layer.key] = layer.available;
    return result;
  }, [catalog]);

  const wanted = useMemo(
    () => DRAW_ORDER.filter((key) => visible[key] && available[key]),
    [visible, available]
  );
  const wantedKey = wanted.join(",");

  // One canvas renderer for all vector layers keeps thousands of polygons
  // responsive and lets hover / click reach whichever feature is on top.
  useEffect(() => {
    renderer.current = L.canvas({ padding: 0.3, tolerance: 3 });
    const created = groups.current;
    return () => {
      Object.values(created).forEach((group) => group.remove());
      groups.current = {};
      registry.current = new Map();
    };
  }, [map, registry]);

  useEffect(() => {
    let disposed = false;

    function setData(key, collection) {
      let group = groups.current[key];
      if (!group) {
        group = L.geoJSON(null, {
          renderer: renderer.current,
          style: (feature) => featureStyle(feature, key),
          onEachFeature: (feature, layer) => {
            layer.bindTooltip(() => tooltipText(feature), {
              sticky: true,
              className: "cv-tooltip",
              direction: "top",
              opacity: 1,
            });
            layer.on("click", (event) => {
              // While editing or picking a point the click belongs to the
              // map, so it is left to bubble.
              if (!interactiveRef.current) return;
              L.DomEvent.stopPropagation(event);
              onSelectRef.current?.(feature);
            });
            layer.on("mouseover", () => {
              if (interactiveRef.current) layer.setStyle({ weight: (layer.options.weight || 1) + 1.2 });
            });
            layer.on("mouseout", () => group.resetStyle(layer));
          },
        });
        groups.current[key] = group;
      }
      group.clearLayers();
      if (collection?.features?.length) group.addData(collection);
      if (!map.hasLayer(group)) group.addTo(map);
    }

    function restack() {
      for (const key of DRAW_ORDER) {
        const group = groups.current[key];
        if (group && map.hasLayer(group)) group.bringToFront();
      }
    }

    async function load(force) {
      const zoom = map.getZoom();
      const view = map.getBounds();
      const stamp = `${source}|${wantedKey}|${refreshKey}|${Math.floor(zoom)}`;
      if (
        !force &&
        last.current &&
        last.current.stamp === stamp &&
        !last.current.truncated &&
        last.current.bounds.contains(view)
      ) {
        return;
      }

      // Hide layers that were switched off.
      for (const key of Object.keys(groups.current)) {
        if (!wanted.includes(key)) groups.current[key].remove();
      }
      if (!wanted.length) {
        registry.current = new Map();
        last.current = null;
        onStatus?.({ loading: false, error: "", truncated: false, drawn: 0 });
        return;
      }

      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;

      const fetchBounds = view.pad(0.35);
      const params = { bbox: bboxString(fetchBounds), zoom: Math.floor(zoom) };
      const classes = wanted.filter((key) => LANDCOVER_CLASSES.includes(key));

      onStatus?.({ loading: true, error: "" });
      try {
        const [parcels, landcover, plots] = await Promise.all([
          wanted.includes("parcels") ? getMapParcels(source, params, abort.signal) : null,
          classes.length
            ? getMapFeatures(source, { ...params, classes: classes.join(",") }, abort.signal)
            : null,
          wanted.includes("plots") ? getMapPlots(source, params, abort.signal) : null,
        ]);
        if (disposed || abort.signal.aborted) return;

        const next = new Map();
        let drawn = 0;
        if (parcels) {
          setData("parcels", parcels);
          parcels.features.forEach((feature) => next.set(feature.id, feature));
          drawn += parcels.features.length;
        }
        if (plots) {
          setData("plots", plots);
          plots.features.forEach((feature) => next.set(feature.id, feature));
          drawn += plots.features.length;
        }
        if (landcover) {
          const byClass = {};
          for (const feature of landcover.features) {
            const key = feature.properties.class_key;
            (byClass[key] ||= []).push(feature);
            next.set(feature.id, feature);
          }
          for (const key of classes) {
            setData(key, { type: "FeatureCollection", features: byClass[key] || [] });
          }
          drawn += landcover.features.length;
        }
        restack();
        registry.current = next;

        const truncated = Boolean(parcels?.truncated || landcover?.truncated);
        last.current = { stamp, bounds: fetchBounds, truncated };
        onStatus?.({
          loading: false,
          error: "",
          truncated,
          drawn,
          detail: parcels?.detail || landcover?.detail,
          minArea: parcels?.min_area_m2 ?? landcover?.min_area_m2 ?? null,
        });
      } catch (error) {
        if (isCanceled(error) || disposed) return;
        onStatus?.({
          loading: false,
          error: error?.response?.data?.detail || "Map features could not be loaded.",
        });
      }
    }

    const schedule = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => load(false), 220);
    };

    load(true);
    map.on("moveend", schedule);
    return () => {
      disposed = true;
      map.off("moveend", schedule);
      window.clearTimeout(timer.current);
      controller.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, source, wantedKey, refreshKey]);

  return null;
}

// ----------------------------------------------------------- selection
function SelectionLayer({ geometry }) {
  const map = useMap();

  useEffect(() => {
    if (!geometry) return undefined;
    const glow = L.geoJSON(geometry, {
      pane: "cv-selection",
      interactive: false,
      style: { color: "#5fe0e6", weight: 8, opacity: 0.28, fill: false },
    }).addTo(map);
    const line = L.geoJSON(geometry, {
      pane: "cv-selection",
      interactive: false,
      style: { color: "#ffffff", weight: 2.4, opacity: 1, fillColor: "#5fe0e6", fillOpacity: 0.16 },
    }).addTo(map);
    return () => {
      glow.remove();
      line.remove();
    };
  }, [map, geometry]);

  return null;
}

// ---------------------------------------------------- reference layers
/** Even-odd test of a lon/lat point against a Polygon / MultiPolygon. */
function geometryContains(geometry, lng, lat) {
  if (!geometry) return false;
  const polygons =
    geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon"
        ? geometry.coordinates
        : [];
  const inRing = (ring) => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  };
  return polygons.some(
    (rings) => rings.length && inRing(rings[0]) && !rings.slice(1).some((hole) => inRing(hole))
  );
}

function ringArea(ring) {
  let sum = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    sum += (ring[j][0] - ring[i][0]) * (ring[j][1] + ring[i][1]);
  }
  return Math.abs(sum / 2);
}

function geometryExtent(geometry) {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.reduce((total, rings) => total + (rings.length ? ringArea(rings[0]) : 0), 0);
}

/**
 * Existing GIS / land-records layers. They are drawn without pointer events
 * (several canvas panes cannot all receive the pointer), so a record is
 * picked by testing the clicked position against the loaded polygons.
 */
function ReferenceLayers({ datasets, visible, selectable, onSelect, onStatus }) {
  const map = useMap();
  const ids = (datasets || []).map((dataset) => dataset.dataset_id).join(",");
  const loaded = useRef([]);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    loaded.current = [];
    if (!visible || !datasets?.length) return undefined;
    let disposed = false;
    const layers = [];

    datasets.forEach((dataset) => {
      getDatasetOverlay(dataset.dataset_id)
        .then((collection) => {
          if (disposed) return;
          const layer = L.geoJSON(collection, {
            pane: "cv-reference",
            interactive: false,
            style: { color: "#f4f1c4", weight: 1.6, fillOpacity: 0.04, dashArray: "3 3" },
            pointToLayer: (_feature, latlng) =>
              L.circleMarker(latlng, {
                pane: "cv-reference",
                interactive: false,
                radius: 4,
                color: "#f4f1c4",
                weight: 1.5,
                fillOpacity: 0.6,
              }),
          }).addTo(map);
          layers.push(layer);
          loaded.current.push({ dataset, features: collection.features || [] });
        })
        .catch((error) => {
          if (!disposed) {
            onStatus?.({
              error: error?.response?.data?.detail || `${dataset.name} could not be drawn.`,
            });
          }
        });
    });

    return () => {
      disposed = true;
      loaded.current = [];
      layers.forEach((layer) => layer.remove());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, ids, visible]);

  useEffect(() => {
    if (!visible || !selectable) return undefined;
    const onClick = (event) => {
      const { lng, lat } = event.latlng;
      // Where records overlap, the smallest one under the pointer is taken.
      let best = null;
      for (const { dataset, features } of loaded.current) {
        for (const feature of features) {
          if (!geometryContains(feature.geometry, lng, lat)) continue;
          const extent = geometryExtent(feature.geometry);
          if (!best || extent < best.extent) best = { dataset, feature, extent };
        }
      }
      if (best) onSelectRef.current?.({ dataset: best.dataset, feature: best.feature });
    };
    map.on("click", onClick);
    return () => map.off("click", onClick);
  }, [map, visible, selectable]);

  return null;
}

function GnssLayer({ visible, refreshKey }) {
  const map = useMap();

  useEffect(() => {
    if (!visible) return undefined;
    let disposed = false;
    let layer = null;

    getGnssPoints()
      .then((collection) => {
        if (disposed) return;
        layer = L.geoJSON(collection, {
          pointToLayer: (feature, latlng) =>
            L.marker(latlng, {
              pane: "cv-points",
              keyboard: false,
              icon: L.divIcon({ className: "gnss-marker", iconSize: [12, 12] }),
            }),
          onEachFeature: (feature, item) => {
            const p = feature.properties || {};
            const title = p.origin === "Ground truth" ? "Ground truth" : "GNSS point";
            const lines = [
              p.observed_class ? `Observed: ${p.observed_class}` : null,
              p.feature_id ? `Feature: ${p.feature_id}` : null,
              p.name ? `Name: ${p.name}` : null,
              p.accuracy_m != null ? `Accuracy: ${p.accuracy_m} m` : null,
              p.comment || null,
            ].filter(Boolean);
            item.bindTooltip(`<strong>${title}</strong><br/>${lines.join("<br/>")}`, {
              className: "cv-tooltip",
              direction: "top",
            });
          },
        }).addTo(map);
      })
      .catch(() => {});

    return () => {
      disposed = true;
      layer?.remove();
    };
  }, [map, visible, refreshKey]);

  return null;
}

// ------------------------------------------------- elevation (DSM / DTM)
function colourRamp(t) {
  // deep blue -> teal -> sand -> white, low to high
  const stops = [
    [0, [10, 58, 92]],
    [0.35, [36, 150, 160]],
    [0.7, [222, 205, 150]],
    [1, [255, 255, 255]],
  ];
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const k = (t - t0) / (t1 - t0 || 1);
      return c0.map((value, index) => Math.round(value + (c1[index] - value) * k));
    }
  }
  return stops[stops.length - 1][1];
}

function ElevationOverlay({ surface, visible, extent }) {
  const map = useMap();
  const extentKey = extent ? extent.join(",") : "";

  useEffect(() => {
    if (!visible || !extent) return undefined;
    let disposed = false;
    let overlay = null;

    getTerrainGrid(extent.join(","), surface, 192)
      .then((grid) => {
        if (disposed) return;
        const canvas = document.createElement("canvas");
        canvas.width = grid.cols;
        canvas.height = grid.rows;
        const ctx = canvas.getContext("2d");
        const image = ctx.createImageData(grid.cols, grid.rows);
        const span = grid.max - grid.min || 1;
        for (let row = 0; row < grid.rows; row++) {
          for (let col = 0; col < grid.cols; col++) {
            const value = grid.values[row][col];
            const offset = (row * grid.cols + col) * 4;
            if (value == null) {
              image.data[offset + 3] = 0;
              continue;
            }
            const [r, g, b] = colourRamp((value - grid.min) / span);
            image.data[offset] = r;
            image.data[offset + 1] = g;
            image.data[offset + 2] = b;
            image.data[offset + 3] = 215;
          }
        }
        ctx.putImageData(image, 0, 0);
        overlay = L.imageOverlay(canvas.toDataURL(), boundsFromBbox(grid.bounds), {
          pane: "cv-raster",
          opacity: 0.8,
          interactive: false,
        }).addTo(map);
      })
      .catch(() => {});

    return () => {
      disposed = true;
      overlay?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, surface, visible, extentKey]);

  return null;
}

// --------------------------------------------------------------- focus
function FocusController({ focus, initialBbox }) {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (fitted.current || !initialBbox) return;
    fitted.current = true;
    map.fitBounds(boundsFromBbox(initialBbox), { padding: [28, 28], animate: false });
  }, [map, initialBbox]);

  useEffect(() => {
    if (!focus?.bbox) return;
    const bounds = boundsFromBbox(focus.bbox);
    if (!bounds.isValid()) return;
    fitted.current = true;
    map.flyToBounds(bounds, {
      padding: focus.padding || [70, 70],
      maxZoom: focus.maxZoom || 21,
      duration: 0.7,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, focus?.nonce]);

  return null;
}

// ------------------------------------------------------- vertex editor
export function VertexEditor({ geometry, onChange }) {
  const map = useMap();
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // `geometry` is the outline the editing session started from. The parent
  // keeps it unchanged while editing; edits are reported through onChange.
  useEffect(() => {
    if (!geometry || geometry.type !== "Polygon") return undefined;

    const rings = geometry.coordinates;
    let vertices = rings[0].slice(0, -1).map(([lng, lat]) => L.latLng(lat, lng));
    const holes = rings.slice(1);

    const outline = L.polygon(vertices, {
      pane: "cv-edit",
      color: "#ffffff",
      weight: 2,
      dashArray: "6 5",
      fillColor: "#5fe0e6",
      fillOpacity: 0.14,
      bubblingMouseEvents: false,
    }).addTo(map);
    const handles = L.layerGroup().addTo(map);

    const emit = () => {
      const exterior = vertices.map((point) => [
        Number(point.lng.toFixed(8)),
        Number(point.lat.toFixed(8)),
      ]);
      exterior.push(exterior[0]);
      onChangeRef.current?.({ type: "Polygon", coordinates: [exterior, ...holes] });
    };

    const rebuild = () => {
      handles.clearLayers();
      vertices.forEach((point, index) => {
        const marker = L.marker(point, {
          pane: "cv-handles",
          draggable: true,
          keyboard: false,
          icon: L.divIcon({ className: "vertex-handle", iconSize: [12, 12] }),
          title: "Drag to move. Right-click to remove.",
        });
        marker.on("drag", () => {
          vertices[index] = marker.getLatLng();
          outline.setLatLngs(vertices);
        });
        marker.on("dragend", emit);
        marker.on("contextmenu", (event) => {
          L.DomEvent.stop(event);
          if (vertices.length <= 3) return;
          vertices.splice(index, 1);
          outline.setLatLngs(vertices);
          rebuild();
          emit();
        });
        handles.addLayer(marker);
      });
    };

    // Click on an edge to add a vertex there.
    outline.on("click", (event) => {
      L.DomEvent.stop(event);
      const point = map.latLngToLayerPoint(event.latlng);
      let best = { distance: Infinity, index: 0 };
      for (let i = 0; i < vertices.length; i++) {
        const a = map.latLngToLayerPoint(vertices[i]);
        const b = map.latLngToLayerPoint(vertices[(i + 1) % vertices.length]);
        const distance = L.LineUtil.pointToSegmentDistance(point, a, b);
        if (distance < best.distance) best = { distance, index: i };
      }
      if (best.distance > 12) return;
      vertices.splice(best.index + 1, 0, event.latlng);
      outline.setLatLngs(vertices);
      rebuild();
      emit();
    });

    rebuild();
    return () => {
      outline.remove();
      handles.remove();
    };
  }, [map, geometry]);

  return null;
}

function PointPicker({ active, point, onPick }) {
  const map = useMap();

  useEffect(() => {
    if (!active) return undefined;
    const container = map.getContainer();
    container.style.cursor = "crosshair";
    const handler = (event) => onPick?.(event.latlng);
    map.on("click", handler);
    return () => {
      container.style.cursor = "";
      map.off("click", handler);
    };
  }, [map, active, onPick]);

  useEffect(() => {
    if (!point) return undefined;
    const marker = L.circleMarker([point.lat, point.lng], {
      pane: "cv-points",
      radius: 7,
      color: "#ffffff",
      weight: 2,
      fillColor: "#3fd6a3",
      fillOpacity: 0.9,
      interactive: false,
    }).addTo(map);
    return () => marker.remove();
  }, [map, point]);

  return null;
}

/**
 * The 2D GIS map. All vector data comes from the API, filtered to the
 * assigned area and the current view; nothing is drawn from static shapes.
 */
export default function MapView({
  source,
  catalog,
  visible,
  basemap = "satellite",
  boundary,
  initialBbox,
  focus,
  selectedGeometry,
  refreshKey = 0,
  editGeometry,
  onEditChange,
  picking = false,
  pickedPoint,
  onPick,
  onSelect,
  inspect = "ai",
  onReferenceSelect,
  onStatus,
  onReady,
}) {
  const registry = useRef(new Map());
  const editing = Boolean(editGeometry);
  // "ai": clicks select AI features. "reference": clicks select records of
  // the existing GIS layer.
  const inspectReference = inspect === "reference" && !editing && !picking;

  const referenceLayer = catalog?.layers?.find((layer) => layer.key === "existing_gis");
  const dsm = catalog?.layers?.find((layer) => layer.key === "dsm");
  const dtm = catalog?.layers?.find((layer) => layer.key === "dtm");

  return (
    <MapContainer
      className="gis-map"
      center={INDIA_VIEW.center}
      zoom={INDIA_VIEW.zoom}
      minZoom={3}
      maxZoom={22}
      zoomControl={false}
      scrollWheelZoom
      preferCanvas
      zoomSnap={0.5}
    >
      <MapFurniture onReady={onReady} />
      <Basemap basemap={basemap} />
      <FocusController focus={focus} initialBbox={initialBbox} />
      <BoundaryLayer boundary={boundary} visible={visible.assigned_area !== false} />

      <ElevationOverlay surface="dtm" visible={Boolean(visible.dtm && dtm?.available)} extent={dtm?.extent} />
      <ElevationOverlay surface="dsm" visible={Boolean(visible.dsm && dsm?.available)} extent={dsm?.extent} />

      <FeatureLayers
        source={source}
        catalog={catalog}
        visible={visible}
        refreshKey={refreshKey}
        interactive={!editing && !picking && !inspectReference}
        onSelect={onSelect}
        onStatus={onStatus}
        registry={registry}
      />
      <ReferenceLayers
        datasets={referenceLayer?.datasets}
        visible={Boolean(visible.existing_gis && referenceLayer?.available)}
        selectable={inspectReference}
        onSelect={onReferenceSelect}
        onStatus={onStatus}
      />
      <GnssLayer visible={Boolean(visible.gnss)} refreshKey={refreshKey} />

      <SelectionLayer geometry={editing ? null : selectedGeometry} />
      <VertexEditor geometry={editGeometry} onChange={onEditChange} />
      <PointPicker active={picking} point={pickedPoint} onPick={onPick} />
      <Readout />
    </MapContainer>
  );
}
