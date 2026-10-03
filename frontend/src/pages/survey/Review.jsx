import { useCallback, useEffect, useState } from "react";

import AuditTrail from "../../components/AuditTrail";
import Icon from "../../components/Icon";
import MapWorkbench from "../../components/MapWorkbench";
import ReviewQueue from "../../components/ReviewQueue";
import { useWorkspace } from "../../context/WorkspaceContext";
import { getAudit } from "../../lib/api";

/**
 * Review and verification: the queue on the left, the map and the selected
 * feature on the right, and the audit trail of every decision below.
 */
export default function Review() {
  const { source } = useWorkspace();
  const [selectRequest, setSelectRequest] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [events, setEvents] = useState(null);

  const loadAudit = useCallback(() => {
    getAudit({ limit: 40 })
      .then((data) => setEvents(data.events.filter((event) => event.action.startsWith("review."))))
      .catch(() => setEvents([]));
  }, []);

  useEffect(() => {
    loadAudit();
  }, [loadAudit, refreshKey]);

  const open = useCallback((item) => {
    setSelectRequest({ id: item.feature_id, bbox: item.bbox, nonce: Date.now() });
  }, []);

  const handleReviewed = useCallback(() => setRefreshKey((value) => value + 1), []);

  return (
    <main className="page page--wide">
      <div className="page-head">
        <div>
          <h1>Review and verification</h1>
          <p>
            Open a feature from the queue to see it on the map. Approve it, edit its outline, flag or
            reject it, or record what you saw on the ground.
          </p>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(320px, 420px) minmax(0, 1fr)" }}>
        <ReviewQueue
          source={source}
          selectedId={selectedId}
          onOpen={open}
          refreshKey={refreshKey}
          pageSize={12}
        />
        <MapWorkbench
          source={source}
          height={720}
          selectRequest={selectRequest}
          onReviewed={handleReviewed}
          onSelectionChange={setSelectedId}
          allow3d={false}
          layersOpen={false}
        />
      </div>

      <section className="panel">
        <div className="panel__head">
          <div className="row" style={{ gap: 9 }}>
            <Icon name="history" size={18} />
            <h3>Audit trail</h3>
          </div>
          <span className="muted">Who decided what, and when</span>
        </div>
        <div className="panel__body">
          <AuditTrail
            events={events || []}
            showEntity
            emptyText={events == null ? "Loading" : "No review decisions have been recorded yet."}
          />
        </div>
      </section>
    </main>
  );
}
