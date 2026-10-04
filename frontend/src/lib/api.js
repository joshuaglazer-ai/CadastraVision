import axios from "axios";
import { AUTH_MODE, supabase } from "./supabase";

export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const api = axios.create({ baseURL: API_URL });

// Every request carries the signed-in user's access token. The backend
// derives the surveyor from that token; no surveyor id is sent by the UI.
api.interceptors.request.use(async (config) => {
  if (AUTH_MODE !== "off" && supabase) {
    const { data } = await supabase.auth.getSession();
    const token = data?.session?.access_token;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

let onUnauthorized = null;
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401 && onUnauthorized) {
      onUnauthorized();
    }
    return Promise.reject(error);
  }
);

/** A message a surveyor can act on, from any failed request. */
export function errorMessage(error, fallback = "The request could not be completed.") {
  if (axios.isCancel?.(error) || error?.code === "ERR_CANCELED") return "";
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item?.msg || String(item)).join("; ");
  }
  if (error?.code === "ERR_NETWORK" || !error?.response) {
    return `The Cadastra Vision API at ${API_URL} is not reachable. Check that the backend is running.`;
  }
  return error?.message || fallback;
}

export function isCanceled(error) {
  return axios.isCancel?.(error) || error?.code === "ERR_CANCELED";
}

const get = async (url, params, signal) => (await api.get(url, { params, signal })).data;

function withSource(source, params = {}) {
  return source && source !== "existing" ? { ...params, source } : params;
}

// ---------------------------------------------------------------- system
export const getHealth = () => get("/health");
export const getSystemStatus = () => get("/api/system/status");

// ------------------------------------------------- surveyor / assignment
export const getMe = () => get("/api/surveyors/me");
export const getAssignedArea = () => get("/api/map/assigned-area");

// ------------------------------------------------------------ work areas
// The owner is always the signed-in account; nothing here names it.
export const listAreas = () => get("/api/assignments");
export const measureBoundary = async (boundary, signal) =>
  (await api.post("/api/assignments/measure", { boundary }, { signal })).data;
export const createWorkArea = async (body) => (await api.post("/api/assignments", body)).data;
export const updateWorkArea = async (id, body) =>
  (await api.patch(`/api/assignments/${encodeURIComponent(id)}`, body)).data;
export const deleteWorkArea = async (id) =>
  (await api.delete(`/api/assignments/${encodeURIComponent(id)}`)).data;
export const activateArea = async (id) =>
  (await api.post(`/api/assignments/${encodeURIComponent(id)}/activate`)).data;

// -------------------------------------------------------------- datasets
export const getDatasets = () => get("/api/datasets");
export const getDatasetOverlay = (datasetId) =>
  get(`/api/datasets/${encodeURIComponent(datasetId)}/overlay`);
// One record of an existing GIS layer with the AI features overlaid on it.
export const getReferenceFeature = (datasetId, index, source, signal) =>
  get(
    `/api/datasets/${encodeURIComponent(datasetId)}/features/${encodeURIComponent(index)}`,
    withSource(source),
    signal
  );

export async function uploadDataset(sourceType, file, onProgress) {
  const form = new FormData();
  form.append("source_type", sourceType);
  form.append("file", file);
  const response = await api.post("/api/datasets/upload", form, {
    onUploadProgress: (event) => {
      if (onProgress && event.total) onProgress(event.loaded / event.total);
    },
  });
  return response.data;
}

// ------------------------------------------------------------------- map
export const getMapLayers = (source) => get("/api/map/layers", withSource(source));

export const getMapParcels = (source, { bbox, zoom, limit } = {}, signal) =>
  get("/api/map/parcels", withSource(source, { bbox, zoom, limit }), signal);

export const getMapFeatures = (source, { classes, bbox, zoom, limit } = {}, signal) =>
  get("/api/map/features", withSource(source, { classes, bbox, zoom, limit }), signal);

export const getFeature = (source, uid, { geometry, reasoning } = {}, signal) =>
  get(
    `/api/map/features/${encodeURIComponent(uid)}`,
    withSource(source, { geometry, reasoning }),
    signal
  );

export const getGnssPoints = () => get("/api/map/gnss");

// ------------------------------------------------------------ processing
export const getJobs = () => get("/api/processing");
export const getJob = (jobId) => get(`/api/processing/${encodeURIComponent(jobId)}`);
export const getJobResult = (jobId) =>
  get(`/api/processing/${encodeURIComponent(jobId)}/result`);
export const getPipelineStages = () => get("/api/processing/stages");

export async function uploadGeoTIFF(file, onProgress) {
  const form = new FormData();
  form.append("file", file);
  const response = await api.post("/api/processing/upload", form, {
    onUploadProgress: (event) => {
      if (onProgress && event.total) onProgress(event.loaded / event.total);
    },
  });
  return response.data;
}

export const createJobFromDataset = async (datasetId) =>
  (await api.post(`/api/processing/from-dataset/${encodeURIComponent(datasetId)}`)).data;

// Whether the job's image overlaps the current area, and which areas it does.
export const getAreaCheck = (jobId) => get(`/api/processing/${encodeURIComponent(jobId)}/area-check`);

export const startProcessing = async (jobId, { confirmOutsideArea = false } = {}) =>
  (
    await api.post(`/api/processing/${encodeURIComponent(jobId)}/start`, null, {
      params: confirmOutsideArea ? { confirm_outside_area: true } : undefined,
    })
  ).data;

// --------------------------------------------------------------- reviews
export const getReviewQueue = (source, { group, includeFragments, limit, offset } = {}) =>
  get(
    "/api/reviews/queue",
    withSource(source, { group, include_fragments: includeFragments, limit, offset })
  );

export const getReviews = (source, params = {}) => get("/api/reviews", withSource(source, params));

export const getFeatureReviews = (source, featureId) =>
  get(`/api/reviews/feature/${encodeURIComponent(featureId)}`, withSource(source));

export const submitReview = async (source, review) =>
  (await api.post("/api/reviews", { ...review, source })).data;

export const getAudit = (params = {}) => get("/api/audit", params);

// ------------------------------------------------------------- analytics
export const getAnalytics = (source) => get("/api/analytics", withSource(source));

// --------------------------------------------------------------- terrain
export const getTerrainStatus = () => get("/api/terrain/status");
export const getTerrainGrid = (bbox, surface = "dtm", size = 128) =>
  get("/api/terrain/grid", { bbox, surface, size });
export const getTerrainBuildings = (source, bbox) =>
  get("/api/terrain/buildings", withSource(source, { bbox }));

// ---------------------------------------------------------------- export
export const getExportSummary = (source, layer, status) =>
  get("/api/export/summary", withSource(source, { layer, status }));

/** Download an export as a file. Returns the file name that was saved. */
export async function downloadExport(format, source, { layer, status, classes } = {}) {
  const response = await api.get(`/api/export/${format}`, {
    params: withSource(source, { layer, status, classes }),
    responseType: "blob",
  });
  const disposition = response.headers["content-disposition"] || "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match ? match[1] : `cadastra_vision_${layer}.${format}`;

  const url = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
  return filename;
}

/** Errors of blob requests arrive as blobs; read the JSON detail out. */
export async function blobErrorMessage(error) {
  const data = error?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      if (parsed?.detail) return String(parsed.detail);
    } catch {
      /* fall through */
    }
  }
  return errorMessage(error);
}

export default api;
