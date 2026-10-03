// Run with: npm test   (Node's built-in test runner, no extra packages)
import assert from "node:assert/strict";
import test from "node:test";

import {
  UNAVAILABLE,
  bboxString,
  formatArea,
  formatBytes,
  formatCoordinate,
  formatLength,
  formatNumber,
  formatPercent,
  isNumber,
  titleCase,
} from "./format.js";

test("missing values are shown as unavailable, never as zero", () => {
  for (const value of [null, undefined, NaN, Infinity, "12", {}]) {
    assert.equal(formatNumber(value), UNAVAILABLE);
    assert.equal(formatArea(value), UNAVAILABLE);
    assert.equal(formatLength(value), UNAVAILABLE);
    assert.equal(formatPercent(value), UNAVAILABLE);
  }
  assert.equal(UNAVAILABLE, "Data unavailable");
  assert.equal(formatNumber(null, 0, "Awaiting dataset"), "Awaiting dataset");
  assert.equal(isNumber(0), true);
  assert.equal(formatNumber(0), "0");
});

test("areas switch from square metres to hectares", () => {
  assert.equal(formatArea(0.0245), "0.025 m²");
  assert.equal(formatArea(36.04), "36.0 m²");
  assert.equal(formatArea(730.4), "730 m²");
  assert.equal(formatArea(47150.8), "4.72 ha");
  assert.equal(formatArea(10853406.76), "1,085.3 ha");
});

test("lengths", () => {
  assert.equal(formatLength(0.94), "0.94 m");
  assert.equal(formatLength(140.0001), "140.0 m");
  assert.equal(formatLength(1400), "1.40 km");
});

test("a small non-zero share is not rounded down to 0%", () => {
  assert.equal(formatPercent(0.0164), "1.6%");
  assert.equal(formatPercent(0.0004), "<0.1%");
  assert.equal(formatPercent(0), "0.0%");
  assert.equal(formatPercent(1), "100.0%");
  assert.equal(formatPercent(0.915, 0), "92%");
});

test("file sizes use decimal units", () => {
  assert.equal(formatBytes(97928571), "97.9 MB");
  assert.equal(formatBytes(4096), "4 kB");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2.5e9), "2.50 GB");
  assert.equal(formatBytes(undefined), "");
});

test("numbers use Indian digit grouping", () => {
  assert.equal(formatNumber(1234567), "12,34,567");
  assert.equal(formatNumber(5363), "5,363");
  assert.equal(formatNumber(0.9983, 2), "1.00");
});

test("coordinates and labels", () => {
  assert.equal(formatCoordinate(77.6254), "77.625400");
  assert.equal(formatCoordinate(null), "");
  assert.equal(titleCase("SURVEYOR_VERIFIED"), "Surveyor Verified");
  assert.equal(titleCase("review required"), "Review Required");
  assert.equal(titleCase(""), "");
});

test("map bounds become a west,south,east,north query string", () => {
  const bounds = {
    getWest: () => 77.6,
    getSouth: () => 28.55,
    getEast: () => 77.65,
    getNorth: () => 28.57,
  };
  assert.equal(bboxString(bounds), "77.6000000,28.5500000,77.6500000,28.5700000");
});
