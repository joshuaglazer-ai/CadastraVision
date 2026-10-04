// Run with: npm test   (Node's built-in test runner, no extra packages)
import assert from "node:assert/strict";
import test from "node:test";

import { NO_IMAGERY, noFeaturesInArea, queueEmptyMessage } from "./emptyArea.js";

test("an area with no features at all says no imagery has been processed", () => {
  const data = { features_in_area: 0, items: [], counts: { high: 0, medium: 0, low: 0 } };
  const message = queueEmptyMessage(data, "high");
  assert.equal(message.kind, "no-imagery");
  assert.equal(message.title, NO_IMAGERY.title);
  assert.match(message.detail, /uploaded/);
  assert.doesNotMatch(message.title, /high priority/);
  assert.equal(noFeaturesInArea(data), true);
});

test("features that exist but none at high priority keep the existing message", () => {
  const data = { features_in_area: 120, items: [], counts: { high: 0, medium: 80, low: 40 } };
  const message = queueEmptyMessage(data, "high");
  assert.equal(message.kind, "group-empty");
  assert.equal(message.title, "Nothing at high priority");
  assert.match(message.detail, /low model confidence/);
  assert.equal(noFeaturesInArea(data), false);
});

test("no empty state while items are listed, loading, or the count is unknown", () => {
  assert.equal(queueEmptyMessage({ features_in_area: 5, items: [{}] }, "high"), null);
  assert.equal(queueEmptyMessage(null, "high"), null);
  // An older server without the count: never claim there is no imagery.
  assert.equal(noFeaturesInArea({ items: [] }), false);
  assert.equal(queueEmptyMessage({ items: [] }, "medium").kind, "group-empty");
});
