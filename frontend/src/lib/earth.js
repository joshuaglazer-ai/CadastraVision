// Geometry and the fallback texture for the globe.
//
// The outlines in assets/land-outline.json are coarse (1-2 degrees) and
// decorative. They paint the texture shown until the photographic Earth
// textures have loaded; nothing is measured from them.

import outline from "../assets/land-outline.json";

const DEG = Math.PI / 180;

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

// The survey site marked on the globe. Real coordinates [lon, lat].
export const SURVEY_SITE = { name: "Uplarshi, Uttar Pradesh", position: [77.6254, 28.559] };
