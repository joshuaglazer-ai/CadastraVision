import { useCallback, useEffect, useState } from "react";
import { errorMessage, getReviewQueue } from "../lib/api";
import { formatArea, formatDate, formatNumber } from "../lib/format";
import Icon from "./Icon";
import { EmptyState, ErrorState, LoadingState } from "./States";
import StatusBadge from "./StatusBadge";
import NoImageryState from "./NoImageryState";
import { queueEmptyMessage } from "../lib/emptyArea";

const GROUPS = [
  { key: "high", label: "High priority" },
  { key: "medium", label: "Medium priority" },
  { key: "low", label: "Low priority" },
  { key: "verified", label: "Verified" },
  { key: "flagged", label: "Flagged" },
];


/**
 * The review queue. Choosing an item asks the map to zoom to the feature,
 * select it and open its details.
 */
export default function ReviewQueue({ source, selectedId, onOpen, refreshKey = 0, pageSize = 25, initialGroup = "high" }) {
  const [group, setGroup] = useState(initialGroup);
  const [includeFragments, setIncludeFragments] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [state, setState] = useState({ loading: true, error: "" });

  const load = useCallback(async () => {
    setState({ loading: true, error: "" });
    try {
      const result = await getReviewQueue(source, {
        group,
        includeFragments,
        limit: pageSize,
        offset,
      });
      setData(result);
      setState({ loading: false, error: "" });
    } catch (error) {
      setState({ loading: false, error: errorMessage(error, "The review queue could not be loaded.") });
    }
  }, [source, group, includeFragments, offset, pageSize]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  useEffect(() => {
    setOffset(0);
  }, [source, group, includeFragments]);

  const counts = data?.counts || {};
  const total = data?.total ?? 0;
  const items = data?.items || [];

  return (
    <div className="panel panel--flush">
      <div className="panel__head">
        <div>
          <h3>Review queue</h3>
          <p className="muted" style={{ fontSize: "0.88rem", marginTop: 2 }}>
            Ranked by model-derived uncertainty and geometry checks. A high rank is a reason to look,
            not proof of an error.
          </p>
        </div>
        <button type="button" className="icon-btn" onClick={load} aria-label="Refresh the queue" title="Refresh">
          <Icon name="refresh" size={16} />
        </button>
      </div>

      <div className="tabs" role="tablist" aria-label="Review groups">
        {GROUPS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            className="tab"
            aria-selected={group === item.key}
            onClick={() => setGroup(item.key)}
          >
            {item.label}
            <span className="tab__count">{counts[item.key] != null ? formatNumber(counts[item.key]) : "–"}</span>
          </button>
        ))}
      </div>

      <div className="panel__body" role="tabpanel">
        {state.error ? (
          <ErrorState compact title="The queue could not be loaded" detail={state.error} onRetry={load} />
        ) : state.loading && !data ? (
          <LoadingState compact label="Loading the review queue" />
        ) : items.length === 0 ? (
          queueEmptyMessage(data, group)?.kind === "no-imagery" ? (
            <NoImageryState compact />
          ) : (
            <EmptyState
              compact
              icon="check"
              title={queueEmptyMessage(data, group)?.title}
              detail={queueEmptyMessage(data, group)?.detail}
            />
          )
        ) : (
          <ul className="queue">
            {items.map((item) => (
              <li key={`${item.layer}-${item.feature_id}`}>
                <button
                  type="button"
                  className={`queue__item ${selectedId === item.feature_id ? "is-selected" : ""}`}
                  onClick={() => onOpen?.(item)}
                  aria-label={`Open ${item.feature_id} on the map`}
                >
                  <span className="queue__id">{item.feature_id}</span>
                  <StatusBadge status={item.status} size="sm" />
                  <span className="queue__issue">{item.issue}</span>
                  <span className="queue__meta">
                    <span>{item.layer === "parcels" ? "Candidate parcel" : item.class_name}</span>
                    <span>{formatArea(item.area_m2, "")}</span>
                    <span>
                      {item.confidence != null
                        ? `Confidence ${formatNumber(item.confidence, 2)}`
                        : "Confidence not recorded"}
                    </span>
                    <span>
                      {item.entropy != null ? `Entropy ${formatNumber(item.entropy, 2)}` : "Entropy not recorded"}
                    </span>
                    {item.center ? (
                      <span className="mono">
                        {item.center[1].toFixed(5)}, {item.center[0].toFixed(5)}
                      </span>
                    ) : null}
                    {item.last_review_at ? <span>Reviewed {formatDate(item.last_review_at, true)}</span> : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="pager">
        <label className="check">
          <input
            type="checkbox"
            checked={includeFragments}
            onChange={(event) => setIncludeFragments(event.target.checked)}
          />
          Include fragments
          {data && !includeFragments && data.fragments_hidden
            ? ` (${formatNumber(data.fragments_hidden)} hidden, under 1 m²)`
            : ""}
        </label>
        <div className="row" style={{ gap: 8 }}>
          <span>
            {total ? `${formatNumber(offset + 1)}–${formatNumber(Math.min(offset + pageSize, total))} of ${formatNumber(total)}` : "0"}
          </span>
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            disabled={offset === 0 || state.loading}
            onClick={() => setOffset(Math.max(0, offset - pageSize))}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            disabled={offset + pageSize >= total || state.loading}
            onClick={() => setOffset(offset + pageSize)}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
