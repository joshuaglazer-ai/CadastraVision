import { useEffect, useRef } from "react";
import L from "leaflet";
import { MapContainer, useMap } from "react-leaflet";

import { Basemap, BoundaryLayer, MapFurniture, VertexEditor } from "./MapView";

const INDIA_VIEW = { center: [22.8, 79.5], zoom: 5 };

/** Click-to-place drawing: every click adds a corner; the parent closes it. */
function PolygonDrawer({ active, points, onAddPoint }) {
  const map = useMap();
  const onAddRef = useRef(onAddPoint);
  onAddRef.current = onAddPoint;

  useEffect(() => {
    if (!active) return undefined;
    const container = map.getContainer();
    container.style.cursor = "crosshair";
    map.doubleClickZoom.disable();
    const handler = (event) => onAddRef.current?.([event.latlng.lng, event.latlng.lat]);
    map.on("click", handler);
    return () => {
      container.style.cursor = "";
      map.doubleClickZoom.enable();
      map.off("click", handler);
    };
  }, [map, active]);

  useEffect(() => {
    if (!points.length) return undefined;
    const latlngs = points.map(([lng, lat]) => [lat, lng]);
    const group = L.layerGroup().addTo(map);
    L.polyline(latlngs, { pane: "cv-edit", color: "#ffffff", weight: 2, dashArray: "6 5", interactive: false }).addTo(group);
    if (points.length >= 3) {
      // The closing edge, shown faintly until the outline is finished.
      L.polyline([latlngs[latlngs.length - 1], latlngs[0]], {
        pane: "cv-edit",
        color: "#5fe0e6",
        weight: 1.5,
        dashArray: "2 6",
        interactive: false,
      }).addTo(group);
    }
    latlngs.forEach((latlng, index) =>
      L.circleMarker(latlng, {
        pane: "cv-handles",
        radius: index === 0 ? 6 : 4.5,
        color: "#0b6f94",
        weight: 2,
        fillColor: "#ffffff",
        fillOpacity: 1,
        interactive: false,
      }).addTo(group)
    );
    return () => group.remove();
  }, [map, points]);

  return null;
}

/** A boundary shown as is (e.g. a multi-part upload, which cannot be edited). */
function PreviewLayer({ geometry }) {
  const map = useMap();
  useEffect(() => {
    if (!geometry) return undefined;
    const layer = L.geoJSON(geometry, {
      pane: "cv-edit",
      interactive: false,
      style: { color: "#ffffff", weight: 2, dashArray: "6 5", fillColor: "#5fe0e6", fillOpacity: 0.14 },
    }).addTo(map);
    return () => layer.remove();
  }, [map, geometry]);
  return null;
}

/** Moves the map to a box whenever `focus.nonce` changes. */
function Focus({ focus }) {
  const map = useMap();
  useEffect(() => {
    if (!focus?.bbox) return;
    const [minx, miny, maxx, maxy] = focus.bbox;
    const bounds = L.latLngBounds([miny, minx], [maxy, maxx]);
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 19, animate: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, focus?.nonce]);
  return null;
}

/**
 * The map inside the work-area form: the current area for reference, the
 * outline being drawn, and the existing vertex editor for adjusting it.
 */
export default function WorkAreaMap({
  basemap = "satellite",
  contextBoundary,
  drawing,
  points,
  onAddPoint,
  editGeometry,
  onEditChange,
  previewGeometry,
  focus,
  onReady,
}) {
  return (
    <div className="map-stage work-area-map">
      <MapContainer
        className="gis-map"
        center={INDIA_VIEW.center}
        zoom={INDIA_VIEW.zoom}
        minZoom={3}
        maxZoom={22}
        zoomControl={false}
        scrollWheelZoom
        zoomSnap={0.5}
      >
        <MapFurniture onReady={onReady} />
        <Basemap basemap={basemap} />
        <BoundaryLayer boundary={contextBoundary} visible />
        <Focus focus={focus} />
        <PolygonDrawer active={drawing} points={points} onAddPoint={onAddPoint} />
        <PreviewLayer geometry={previewGeometry} />
        <VertexEditor geometry={drawing ? null : editGeometry} onChange={onEditChange} />
      </MapContainer>
    </div>
  );
}
