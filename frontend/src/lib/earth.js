// Geometry for the globe: where land is, where the points go.
//
// The outlines in assets/land-outline.json are coarse (1-2 degrees) and
// decorative. They are used to place dots on land and to tint the globe
// texture; nothing is measured from them.

import outline from "../assets/land-outline.json";

const DEG = Math.PI / 180;

function prepare(entries) {
  return entries.map(({ ring }) => {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const [x, y] of ring) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    return { ring, minX, minY, maxX, maxY };
  });
}

const LAND = prepare(outline.land);
const WATER = prepare(outline.water);

function inRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function inAny(lon, lat, polygons) {
  for (const p of polygons) {
    if (lon < p.minX || lon > p.maxX || lat < p.minY || lat > p.maxY) continue;
    if (inRing(lon, lat, p.ring)) return true;
  }
  return false;
}

export function isLand(lon, lat) {
  return inAny(lon, lat, LAND) && !inAny(lon, lat, WATER);
}

/**
 * Position on a sphere for a longitude / latitude, matching the UV layout
 * of THREE.SphereGeometry so that points line up with an equirectangular
 * texture: longitude 0 faces +X, 90°E faces -Z.
 */
export function lonLatToXYZ(lon, lat, radius = 1) {
  const phi = lat * DEG;
  const lambda = lon * DEG;
  return [
    radius * Math.cos(phi) * Math.cos(lambda),
    radius * Math.sin(phi),
    -radius * Math.cos(phi) * Math.sin(lambda),
  ];
}

/** Rotation about Y that brings a longitude to face the camera on +Z. */
export function facingRotation(lon) {
  return (-90 - lon) * DEG;
}

/** Evenly spread points on the sphere (Fibonacci lattice) that fall on land. */
export function landPoints(count = 15000, radius = 1) {
  const positions = [];
  const golden = Math.PI * (1 + Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const lat = Math.asin(1 - (2 * (i + 0.5)) / count) / DEG;
    const lon = ((((golden * (i + 0.5)) / DEG) % 360) + 360) % 360 - 180;
    if (isLand(lon, lat)) {
      const [x, y, z] = lonLatToXYZ(lon, lat, radius);
      positions.push(x, y, z);
    }
  }
  return new Float32Array(positions);
}

/** Line segments of the latitude / longitude grid, every `step` degrees. */
export function graticule(step = 15, radius = 1, resolution = 3) {
  const positions = [];
  const push = (lon, lat) => positions.push(...lonLatToXYZ(lon, lat, radius));
  for (let lon = -180; lon < 180; lon += step) {
    for (let lat = -90; lat < 90; lat += resolution) {
      push(lon, lat);
      push(lon, Math.min(lat + resolution, 90));
    }
  }
  for (let lat = -90 + step; lat < 90; lat += step) {
    for (let lon = -180; lon < 180; lon += resolution) {
      push(lon, lat);
      push(lon + resolution, lat);
    }
  }
  return new Float32Array(positions);
}

/** Points of a great-circle arc between two places, lifted off the surface. */
export function arcPoints(from, to, radius = 1, lift = 0.12, segments = 48) {
  const a = lonLatToXYZ(from[0], from[1], 1);
  const b = lonLatToXYZ(to[0], to[1], 1);
  const dot = Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
  const omega = Math.acos(dot);
  const positions = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    let x;
    let y;
    let z;
    if (omega < 1e-6) {
      [x, y, z] = a;
    } else {
      const s0 = Math.sin((1 - t) * omega) / Math.sin(omega);
      const s1 = Math.sin(t * omega) / Math.sin(omega);
      x = s0 * a[0] + s1 * b[0];
      y = s0 * a[1] + s1 * b[1];
      z = s0 * a[2] + s1 * b[2];
    }
    const r = radius * (1 + lift * Math.sin(Math.PI * t));
    positions.push(x * r, y * r, z * r);
  }
  return new Float32Array(positions);
}

/**
 * Paint the globe surface: ocean shading by latitude and softly lit land.
 * Returns a canvas in equirectangular layout (2:1).
 */
export function paintEarthTexture(width = 2048) {
  const height = width / 2;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");

  const ocean = ctx.createLinearGradient(0, 0, 0, height);
  ocean.addColorStop(0, "#0a3050");
  ocean.addColorStop(0.28, "#0d4a78");
  ocean.addColorStop(0.5, "#0f5a8e");
  ocean.addColorStop(0.72, "#0d4a78");
  ocean.addColorStop(1, "#0a3050");
  ctx.fillStyle = ocean;
  ctx.fillRect(0, 0, width, height);

  const project = ([lon, lat]) => [((lon + 180) / 360) * width, ((90 - lat) / 180) * height];
  const trace = (ring) => {
    ctx.beginPath();
    ring.forEach((point, index) => {
      const [x, y] = project(point);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
  };

  ctx.lineJoin = "round";
  for (const { ring } of outline.land) {
    trace(ring);
    ctx.fillStyle = "rgba(56, 168, 190, 0.34)";
    ctx.fill();
    ctx.strokeStyle = "rgba(140, 236, 240, 0.55)";
    ctx.lineWidth = width / 900;
    ctx.stroke();
  }
  for (const { ring } of outline.water) {
    trace(ring);
    ctx.fillStyle = "#0e5486";
    ctx.fill();
    ctx.strokeStyle = "rgba(140, 236, 240, 0.4)";
    ctx.lineWidth = width / 1200;
    ctx.stroke();
  }
  return canvas;
}

// Places shown as network nodes. Real coordinates [lon, lat].
export const SURVEY_SITE = { name: "Uplarshi, Uttar Pradesh", position: [77.6254, 28.559] };

export const NETWORK_NODES = [
  { name: "Dehradun", position: [78.03, 30.32] },
  { name: "Lucknow", position: [80.95, 26.85] },
  { name: "Jaipur", position: [75.79, 26.91] },
  { name: "Bhopal", position: [77.41, 23.26] },
  { name: "Mumbai", position: [72.88, 19.08] },
  { name: "Hyderabad", position: [78.49, 17.39] },
  { name: "Bengaluru", position: [77.59, 12.97] },
  { name: "Chennai", position: [80.27, 13.08] },
  { name: "Kolkata", position: [88.36, 22.57] },
  { name: "Guwahati", position: [91.74, 26.14] },
  { name: "Ahmedabad", position: [72.57, 23.02] },
  { name: "Thiruvananthapuram", position: [76.94, 8.52] },
];
