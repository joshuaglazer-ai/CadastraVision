// Which empty state to show. "Nothing has been processed here" and "features
// were checked and none needs attention" are different facts and must not
// share a message. Kept free of React so it can be tested with Node.

export const NO_IMAGERY = {
  title: "No imagery has been processed for this area yet",
  detail:
    "Features come only from drone imagery uploaded and processed for this area; the background satellite map is not analysed.",
  action: "Go to AI processing",
  to: "/survey/processing",
};

export const QUEUE_EMPTY = {
  high: ["Nothing at high priority", "No feature currently has low model confidence, high entropy or a serious geometry issue."],
  medium: ["Nothing at medium priority", "No feature currently needs a second look."],
  low: ["No low-priority features", "There are no remaining features to check."],
  verified: ["Nothing verified yet", "Approve or edit a feature and it will be listed here."],
  flagged: ["Nothing flagged", "Features you flag or reject are listed here."],
};

/** True only when the server reports zero features in the current area and source. */
export function noFeaturesInArea(data) {
  return Boolean(data) && data.features_in_area === 0;
}

/**
 * The review queue's empty message: the no-imagery state when the area has no
 * features at all, otherwise the group's own message. Null when items exist.
 */
export function queueEmptyMessage(data, group) {
  if (!data || (data.items && data.items.length)) return null;
  if (noFeaturesInArea(data)) return { kind: "no-imagery", title: NO_IMAGERY.title, detail: NO_IMAGERY.detail };
  const [title, detail] = QUEUE_EMPTY[group] || QUEUE_EMPTY.high;
  return { kind: "group-empty", title, detail };
}
