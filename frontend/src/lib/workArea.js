// Helpers for the work-area form. Free of React and Leaflet so they can be
// tested with Node's test runner. The server repeats every check and is the
// only one that measures area.

export const MAX_TEXT = 120;
export const PLACE_FIELDS = ["state", "district", "taluk", "village"];
const POLYGON_TYPES = ["Polygon", "MultiPolygon"];

/** Field errors for the name and place fields; empty when valid. */
export function validateAreaFields(fields) {
  const errors = {};
  if (!String(fields.name || "").trim()) errors.name = "Give the work area a name.";
  for (const key of ["name", ...PLACE_FIELDS]) {
    if (String(fields[key] || "").trim().length > MAX_TEXT) {
      errors[key] = `Use at most ${MAX_TEXT} characters.`;
    }
  }
  return errors;
}

/** The trimmed fields as sent to the API; empty place fields become null. */
export function areaFieldsPayload(fields) {
  const payload = { name: String(fields.name || "").trim() };
  for (const key of PLACE_FIELDS) {
    const value = String(fields[key] || "").trim();
    payload[key] = value || null;
  }
  return payload;
}

/** A closed Polygon from clicked points ([lng, lat] pairs); null if under 3. */
export function polygonFromPoints(points) {
  if (!Array.isArray(points) || points.length < 3) return null;
  const ring = points.map(([lng, lat]) => [Number(lng.toFixed(8)), Number(lat.toFixed(8))]);
  ring.push([...ring[0]]);
  return { type: "Polygon", coordinates: [ring] };
}

/** A rectangle Polygon from map bounds { west, south, east, north }. */
export function rectangleFromBounds({ west, south, east, north }) {
  const round = (value) => Number(value.toFixed(8));
  const [w, s, e, n] = [west, south, east, north].map(round);
  return {
    type: "Polygon",
    coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
  };
}

/**
 * Parse an uploaded GeoJSON file. Returns the object to send to the server
 * (which reprojects a declared CRS and measures it). Throws an Error with a
 * message a surveyor can act on.
 */
export function parseBoundaryFile(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("The file is not valid JSON. Choose a .geojson or .json file.");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("The file does not contain a GeoJSON object.");
  }
  if (data.type === "FeatureCollection") {
    const features = Array.isArray(data.features) ? data.features : [];
    const polygons = features.filter((feature) => POLYGON_TYPES.includes(feature?.geometry?.type));
    if (polygons.length !== 1) {
      throw new Error(
        `The file must contain exactly one polygon; it contains ${polygons.length}. ` +
          "Keep only the boundary of the work area in it."
      );
    }
    return data;
  }
  const geometry = data.type === "Feature" ? data.geometry : data;
  if (!POLYGON_TYPES.includes(geometry?.type)) {
    throw new Error(
      `The file must contain a Polygon or MultiPolygon, not ${geometry?.type || "an unknown type"}.`
    );
  }
  return data;
}

/** The list label of an area, as the server gave it. */
export function areaTitle(area) {
  return area?.name || area?.village || area?.assignment_id || "Unnamed area";
}
