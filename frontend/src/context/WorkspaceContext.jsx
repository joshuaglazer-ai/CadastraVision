import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { activateArea as postActivate, errorMessage, getMe, getSystemStatus, listAreas } from "../lib/api";

const WorkspaceContext = createContext(null);

const SOURCE_KEY = "cadastra.source";

/**
 * The signed-in surveyor, their assignment, and the layer source being
 * viewed ("existing" layers or the output of one processing job).
 */
export function WorkspaceProvider({ children }) {
  const [profile, setProfile] = useState(null);
  const [system, setSystem] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // The registry assignments and own work areas this account can switch between.
  const [areas, setAreas] = useState({ items: [], loading: true, error: "" });
  const [switching, setSwitching] = useState(false);
  const [source, setSourceState] = useState(
    () => window.sessionStorage.getItem(SOURCE_KEY) || "existing"
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [me, status] = await Promise.all([getMe(), getSystemStatus()]);
      setProfile(me);
      setSystem(status);
      // A job source remembered from an earlier session may no longer exist.
      const known = new Set((status.sources || []).map((item) => item.source));
      setSourceState((current) => (known.has(current) ? current : "existing"));
    } catch (err) {
      setError(errorMessage(err, "The surveyor profile could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAreas = useCallback(async () => {
    setAreas((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await listAreas();
      setAreas({ items: data.items || [], loading: false, error: "", limits: data.limits });
      return data;
    } catch (err) {
      setAreas((current) => ({
        ...current,
        loading: false,
        error: errorMessage(err, "Your work areas could not be listed."),
      }));
      return null;
    }
  }, []);

  useEffect(() => {
    load();
    loadAreas();
  }, [load, loadAreas]);

  /** Reload the profile and the area list, e.g. after an area changed. */
  const reloadAreas = useCallback(async () => {
    await Promise.all([load(), loadAreas()]);
  }, [load, loadAreas]);

  /**
   * Make an area current. The server records the choice; the profile is then
   * reloaded, and every page keyed on the area id remounts and refetches its
   * map, figures, queue and analytics for the new area. Throws on failure.
   */
  const switchArea = useCallback(
    async (id) => {
      setSwitching(true);
      try {
        await postActivate(id);
        await Promise.all([load(), loadAreas()]);
      } finally {
        setSwitching(false);
      }
    },
    [load, loadAreas]
  );

  const refreshSystem = useCallback(async () => {
    try {
      const status = await getSystemStatus();
      setSystem(status);
      return status;
    } catch {
      return null;
    }
  }, []);

  const setSource = useCallback((next) => {
    window.sessionStorage.setItem(SOURCE_KEY, next);
    setSourceState(next);
  }, []);

  const value = useMemo(
    () => ({
      loading,
      error,
      reload: load,
      areas,
      reloadAreas,
      switchArea,
      switching,
      surveyor: profile?.surveyor ?? null,
      assignment: profile?.assignment ?? null,
      notes: profile?.notes ?? [],
      system,
      refreshSystem,
      sources: system?.sources ?? [],
      source,
      setSource,
    }),
    [loading, error, load, areas, reloadAreas, switchArea, switching, profile, system, refreshSystem, source, setSource]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  return context;
}
