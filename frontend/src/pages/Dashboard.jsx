import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import DisclaimerBanner from "../components/DisclaimerBanner";
import Icon from "../components/Icon";
import KPISection from "../components/KPISection";
import MapWorkbench from "../components/MapWorkbench";
import ReviewQueue from "../components/ReviewQueue";
import { ErrorState } from "../components/States";
import { useWorkspace } from "../context/WorkspaceContext";
import { errorMessage, getAnalytics } from "../lib/api";

export default function Dashboard() {
  const { source, assignment, notes } = useWorkspace();
  const [analytics, setAnalytics] = useState(null);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectRequest, setSelectRequest] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  const loadAnalytics = useCallback(async () => {
    setError("");
    try {
      setAnalytics(await getAnalytics(source));
    } catch (err) {
      setError(errorMessage(err, "The key figures could not be loaded."));
    }
  }, [source]);

  useEffect(() => {
    setAnalytics(null);
    loadAnalytics();
  }, [loadAnalytics]);

  const handleReviewed = useCallback(() => {
    setRefreshKey((value) => value + 1);
    loadAnalytics();
  }, [loadAnalytics]);

  const openFromQueue = useCallback((item) => {
    setSelectRequest({ id: item.feature_id, bbox: item.bbox, nonce: Date.now() });
    document.getElementById("dashboard-map")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>
            {assignment?.village ? `${assignment.village} survey` : "Survey dashboard"}
          </h1>
          <p>
            AI-generated features for your assigned area, the checks run on them, and what is waiting
            for your verification.
          </p>
        </div>
        <div className="page-head__actions">
          <Link to="/survey/processing" className="btn btn--secondary">
            <Icon name="upload" size={16} /> Process imagery
          </Link>
          <Link to="/survey/review" className="btn btn--primary">
            <Icon name="check" size={16} /> Review features
          </Link>
        </div>
      </div>

      <DisclaimerBanner />

      {!assignment ? (
        // A new account has no assignment until an administrator adds its e-mail
        // to the registry; nothing is created on the surveyor's behalf.
        <div className="notice notice--warn" role="note">
          <Icon name="alert" size={18} />
          <p>
            <strong>No work area yet. Add one to begin.</strong>{" "}
            {notes?.[0] || "No survey assignment is registered for this account."}{" "}
            <Link to="/survey">Add a work area</Link>
          </p>
        </div>
      ) : null}

      {assignment?.is_demo && notes?.length ? (
        <div className="notice notice--warn" role="note">
          <Icon name="alert" size={18} />
          <p>
            <strong>Demo assignment.</strong> {notes.slice(1, 3).join(" ")}
          </p>
        </div>
      ) : null}

      {error ? (
        <div className="panel">
          <ErrorState compact title="Key figures unavailable" detail={error} onRetry={loadAnalytics} />
        </div>
      ) : (
        <KPISection analytics={analytics} />
      )}

      <div id="dashboard-map">
        <MapWorkbench
          source={source}
          height={640}
          selectRequest={selectRequest}
          onReviewed={handleReviewed}
          onSelectionChange={setSelectedId}
        />
      </div>

      <ReviewQueue
        source={source}
        selectedId={selectedId}
        onOpen={openFromQueue}
        refreshKey={refreshKey}
        pageSize={8}
      />
    </main>
  );
}
