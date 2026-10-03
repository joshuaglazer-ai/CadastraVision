import Icon from "./Icon";
import { PRIORITY, STATUS } from "../lib/constants";

/** Verification status with icon and text (never colour alone). */
export default function StatusBadge({ status, size = "md" }) {
  const meta = STATUS[status] || { label: status || "Unknown", tone: "muted", icon: "info" };
  return (
    <span className={`badge badge--${meta.tone} badge--${size}`}>
      <Icon name={meta.icon} size={size === "sm" ? 12 : 14} />
      {meta.label}
    </span>
  );
}

export function PriorityBadge({ priority }) {
  const meta = PRIORITY[priority] || { label: priority || "Unknown", tone: "muted" };
  return (
    <span className={`badge badge--${meta.tone} badge--sm`}>
      <span className={`priority-mark priority-mark--${String(priority).toLowerCase()}`} aria-hidden="true" />
      {meta.label} priority
    </span>
  );
}

export function Tag({ tone = "muted", icon, children }) {
  return (
    <span className={`badge badge--${tone} badge--sm`}>
      {icon ? <Icon name={icon} size={12} /> : null}
      {children}
    </span>
  );
}

/** The standing label for anything the model produced. */
export function PreliminaryLabel({ verified = false }) {
  if (verified) {
    return (
      <span className="badge badge--verified badge--md">
        <Icon name="shield" size={14} /> Surveyor verified
      </span>
    );
  }
  return (
    <span className="badge badge--ai badge--md" title="Requires surveyor verification. Not a legal cadastral ownership record.">
      <Icon name="cpu" size={14} /> AI generated · Preliminary
    </span>
  );
}
