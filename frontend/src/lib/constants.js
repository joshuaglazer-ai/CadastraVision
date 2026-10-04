export const PRODUCT = {
  name: "Cadastra Vision",
  tagline: "AI + GIS Surveyor Assistance Platform",
  principle: "AI proposes. GIS validates. Surveyors verify.",
  disclaimer:
    "AI-assisted spatial features require surveyor verification and are not legal cadastral ownership records.",
};

// Map colours per land-cover class. Each class also has its own line
// style on the map so that colour is never the only cue.
export const CLASS_STYLE = {
  parcels: { label: "Candidate parcels", color: "#5fe0e6", fill: "#5fe0e6", fillOpacity: 0.08, weight: 1.4 },
  // A geometric proposal around a building: thin outline, faint fill.
  plots: { label: "Candidate plots", color: "#d6a6ff", fill: "#d6a6ff", fillOpacity: 0.06, weight: 1.2 },
  building: { label: "Buildings", color: "#ffb454", fill: "#ffb454", fillOpacity: 0.42, weight: 1 },
  road: { label: "Roads", color: "#c6d2dc", fill: "#c6d2dc", fillOpacity: 0.34, weight: 1 },
  field: { label: "Fields", color: "#86d98f", fill: "#86d98f", fillOpacity: 0.22, weight: 1 },
  water: { label: "Water", color: "#58b9ff", fill: "#58b9ff", fillOpacity: 0.5, weight: 1 },
  other: { label: "Other", color: "#e79ab0", fill: "#e79ab0", fillOpacity: 0.36, weight: 1 },
  unknown: { label: "Unclassified", color: "#9fbccf", fill: "#9fbccf", fillOpacity: 0.25, weight: 1 },
};

export const LANDCOVER_CLASSES = ["building", "road", "field", "water", "other"];

// Verification status: label, tone, icon and map stroke. The dash pattern
// distinguishes states without relying on colour.
export const STATUS = {
  AI_GENERATED: { label: "AI generated", tone: "ai", icon: "cpu", dash: null },
  REVIEW_REQUIRED: { label: "Review required", tone: "review", icon: "alert", dash: "6 4" },
  SURVEYOR_REVIEWED: { label: "Ground truth recorded", tone: "info", icon: "crosshair", dash: null },
  SURVEYOR_VERIFIED: { label: "Surveyor verified", tone: "verified", icon: "check", dash: null },
  EDITED: { label: "Verified · edited", tone: "verified", icon: "pencil", dash: null },
  FLAGGED: { label: "Flagged", tone: "flagged", icon: "flag", dash: "2 4" },
  REJECTED: { label: "Rejected", tone: "flagged", icon: "x", dash: "2 4" },
};

export const PRIORITY = {
  High: { label: "High", tone: "flagged" },
  Medium: { label: "Medium", tone: "review" },
  Low: { label: "Low", tone: "muted" },
};

export const REVIEW_ACTIONS = [
  { key: "approve", label: "Approve", done: "Approved", icon: "check" },
  { key: "edit", label: "Edit outline", done: "Edit saved", icon: "pencil" },
  { key: "flag", label: "Flag", done: "Flagged", icon: "flag" },
  { key: "reject", label: "Reject", done: "Rejected", icon: "x" },
  { key: "add_ground_truth", label: "Add ground truth", done: "Ground truth added", icon: "crosshair" },
];

export const OBSERVED_CLASSES = ["Field", "Building", "Road", "Water", "Other", "Background"];

export const BASEMAPS = {
  satellite: {
    label: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution:
      "Imagery &copy; Esri, Maxar, Earthstar Geographics and the GIS user community",
    maxNativeZoom: 18,
  },
  streets: {
    label: "Streets",
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap contributors",
    maxNativeZoom: 19,
  },
  none: { label: "No basemap", url: null, attribution: "", maxNativeZoom: 19 },
};
