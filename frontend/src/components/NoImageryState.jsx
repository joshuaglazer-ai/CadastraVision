import { Link } from "react-router-dom";
import { NO_IMAGERY } from "../lib/emptyArea";
import Icon from "./Icon";

/**
 * Shown where features would appear when the current area has none at all,
 * so an empty list is not mistaken for features that were checked and passed.
 */
export default function NoImageryState({ compact = false }) {
  return (
    <div className={`state state--empty no-imagery ${compact ? "state--compact" : ""}`} role="status">
      <Icon name="satellite" size={compact ? 18 : 24} />
      <div>
        <p className="state__title">{NO_IMAGERY.title}</p>
        <p className="state__detail">{NO_IMAGERY.detail}</p>
        <div className="state__action">
          <Link to={NO_IMAGERY.to} className="btn btn--primary btn--sm">
            <Icon name="cpu" size={15} /> {NO_IMAGERY.action}
          </Link>
        </div>
      </div>
    </div>
  );
}
