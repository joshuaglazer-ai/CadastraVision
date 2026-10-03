// Run with: npm test   (Node's built-in test runner, no extra packages)
import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_TEXT,
  areaFieldsPayload,
  areaTitle,
  parseBoundaryFile,
  polygonFromPoints,
  rectangleFromBounds,
  validateAreaFields,
} from "./workArea.js";

test("a work area needs a name and short place fields", () => {
  assert.deepEqual(validateAreaFields({ name: "North fields" }), {});
  assert.match(validateAreaFields({ name: "  " }).name, /name/);
  assert.match(validateAreaFields({ name: "x", village: "v".repeat(MAX_TEXT + 1) }).village, /at most/);
});

test("empty place fields are sent as null, text is trimmed", () => {
  assert.deepEqual(areaFieldsPayload({ name: " A ", state: "", district: " D ", taluk: undefined }), {
    name: "A",
    state: null,
    district: "D",
    taluk: null,
    village: null,
  });
});

test("clicked points become a closed polygon from three points on", () => {
  assert.equal(polygonFromPoints([[77, 28], [77.1, 28]]), null);
  const polygon = polygonFromPoints([[77, 28], [77.1, 28], [77.1, 28.1]]);
  const ring = polygon.coordinates[0];
  assert.equal(ring.length, 4);
  assert.deepEqual(ring[0], ring[3]);
});

test("the map view becomes a closed rectangle", () => {
  const polygon = rectangleFromBounds({ west: 77.6, south: 28.5, east: 77.7, north: 28.6 });
  assert.deepEqual(polygon.coordinates[0], [
    [77.6, 28.5],
    [77.7, 28.5],
    [77.7, 28.6],
    [77.6, 28.6],
    [77.6, 28.5],
  ]);
});

test("uploaded files: one polygon accepted, anything else explained", () => {
  const polygon = { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] };
  const feature = { type: "Feature", properties: {}, geometry: polygon };
  assert.equal(parseBoundaryFile(JSON.stringify(polygon)).type, "Polygon");
  assert.equal(parseBoundaryFile(JSON.stringify(feature)).type, "Feature");
  const collection = { type: "FeatureCollection", crs: { type: "name" }, features: [feature] };
  assert.deepEqual(parseBoundaryFile(JSON.stringify(collection)), collection);

  assert.throws(() => parseBoundaryFile("{not json"), /not valid JSON/);
  assert.throws(() => parseBoundaryFile("[]"), /GeoJSON object/);
  assert.throws(
    () => parseBoundaryFile(JSON.stringify({ type: "FeatureCollection", features: [feature, feature] })),
    /exactly one polygon; it contains 2/
  );
  assert.throws(
    () => parseBoundaryFile(JSON.stringify({ type: "Point", coordinates: [0, 0] })),
    /not Point/
  );
});

test("area titles fall back sensibly", () => {
  assert.equal(areaTitle({ name: "A" }), "A");
  assert.equal(areaTitle({ village: "Uplarshi" }), "Uplarshi");
  assert.equal(areaTitle({ assignment_id: "WA-1" }), "WA-1");
});
