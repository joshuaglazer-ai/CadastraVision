import { formatNumber } from "../lib/format";

/**
 * Model-derived confidence and entropy for one feature. When the layer was
 * produced without recording them, that is stated instead of showing zero.
 */
export default function ConfidenceIndicator({ confidence, entropy, uncertainty }) {
  const available = uncertainty ? uncertainty.available : confidence != null || entropy != null;

  if (!available) {
    return (
      <div className="uncertainty uncertainty--none">
        <p className="uncertainty__label">Model uncertainty not recorded</p>
        <p className="uncertainty__note">
          This layer was generated without per-feature confidence and entropy. New processing jobs
          record both.
        </p>
      </div>
    );
  }

  const level = uncertainty?.level || "low";
  const normalizedEntropy = uncertainty?.normalized_entropy;

  return (
    <div className={`uncertainty uncertainty--${level}`}>
      <p className="uncertainty__label">{uncertainty?.label || "Model-derived uncertainty"}</p>
      <div className="meter" role="img" aria-label={`AI confidence ${formatNumber(confidence, 2, "not recorded")}`}>
        <div className="meter__row">
          <span>AI confidence</span>
          <strong>{confidence != null ? formatNumber(confidence, 2) : "Not recorded"}</strong>
        </div>
        <div className="meter__track">
          <div className="meter__fill" style={{ width: `${Math.round((confidence ?? 0) * 100)}%` }} />
        </div>
      </div>
      <div className="meter" role="img" aria-label={`Entropy ${formatNumber(entropy, 2, "not recorded")}`}>
        <div className="meter__row">
          <span>Entropy</span>
          <strong>{entropy != null ? `${formatNumber(entropy, 2)} nats` : "Not recorded"}</strong>
        </div>
        <div className="meter__track">
          <div
            className="meter__fill meter__fill--entropy"
            style={{ width: `${Math.round((normalizedEntropy ?? 0) * 100)}%` }}
          />
        </div>
      </div>
      <p className="uncertainty__note">
        Model-derived uncertainty. It ranks features for review; it is not proof that a feature is
        wrong.
      </p>
    </div>
  );
}
