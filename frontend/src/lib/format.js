const UNAVAILABLE = "Data unavailable";

export function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatNumber(value, digits = 0, fallback = UNAVAILABLE) {
  if (!isNumber(value)) return fallback;
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Area with a sensible unit: m² below one hectare, ha above. */
export function formatArea(squareMetres, fallback = UNAVAILABLE) {
  if (!isNumber(squareMetres)) return fallback;
  if (squareMetres >= 10000) {
    return `${formatNumber(squareMetres / 10000, squareMetres >= 1e6 ? 1 : 2)} ha`;
  }
  if (squareMetres >= 100) return `${formatNumber(squareMetres, 0)} m²`;
  if (squareMetres >= 1) return `${formatNumber(squareMetres, 1)} m²`;
  return `${formatNumber(squareMetres, 3)} m²`;
}

export function formatLength(metres, fallback = UNAVAILABLE) {
  if (!isNumber(metres)) return fallback;
  if (metres >= 1000) return `${formatNumber(metres / 1000, 2)} km`;
  if (metres >= 10) return `${formatNumber(metres, 1)} m`;
  return `${formatNumber(metres, 2)} m`;
}

export function formatPercent(fraction, digits = 1, fallback = UNAVAILABLE) {
  if (!isNumber(fraction)) return fallback;
  const percent = fraction * 100;
  // A small non-zero share must not be shown as "0%".
  const floor = 10 ** -digits;
  if (percent > 0 && percent < floor) return `<${formatNumber(floor, digits)}%`;
  return `${formatNumber(percent, digits)}%`;
}

export function formatBytes(bytes, fallback = "") {
  if (!isNumber(bytes)) return fallback;
  // Decimal units, as file managers and download pages report them.
  if (bytes >= 1e9) return `${formatNumber(bytes / 1e9, 2)} GB`;
  if (bytes >= 1e6) return `${formatNumber(bytes / 1e6, 1)} MB`;
  if (bytes >= 1e3) return `${formatNumber(bytes / 1e3, 0)} kB`;
  return `${bytes} B`;
}

export function formatDate(value, withTime = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function formatCoordinate(value, digits = 6) {
  return isNumber(value) ? value.toFixed(digits) : "";
}

export function titleCase(text) {
  if (!text) return "";
  return String(text)
    .toLowerCase()
    .replace(/(^|[\s_-])([a-z])/g, (_, gap, letter) => (gap ? " " : "") + letter.toUpperCase());
}

export function bboxString(bounds) {
  // Leaflet LatLngBounds -> "minLon,minLat,maxLon,maxLat"
  return [
    bounds.getWest().toFixed(7),
    bounds.getSouth().toFixed(7),
    bounds.getEast().toFixed(7),
    bounds.getNorth().toFixed(7),
  ].join(",");
}

export { UNAVAILABLE };
