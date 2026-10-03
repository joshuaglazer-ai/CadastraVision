import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { errorMessage, getMe, getSystemStatus } from "../lib/api";

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

  useEffect(() => {
    load();
  }, [load]);

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
      surveyor: profile?.surveyor ?? null,
      assignment: profile?.assignment ?? null,
      notes: profile?.notes ?? [],
      system,
      refreshSystem,
      sources: system?.sources ?? [],
      source,
      setSource,
    }),
    [loading, error, load, profile, system, refreshSystem, source, setSource]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  return context;
}
