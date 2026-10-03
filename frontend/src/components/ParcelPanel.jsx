import { CLASS_STYLE } from "../lib/constants";
import { formatArea, formatLength, formatNumber, formatPercent, isNumber } from "../lib/format";
import { Unavailable } from "./States";

/**
 * Parcel reasoning: what the AI features say about the land inside a
 * candidate parcel. Shown inside the feature panel for parcels.
 */
export default function ParcelPanel({ properties, reasoning }) {
  const roadAccess = properties.road_access_candidate;
  const distance = properties.nearest_road_distance_m;

  return (
    <>
      <section>
        <h4>Road access</h4>
        <dl className="kv">
          <dt>Road access</dt>
          <dd>
            {roadAccess === true ? (
              "Candidate"
            ) : roadAccess === false ? (
              "Not detected"
            ) : (
              <Unavailable>No road features detected</Unavailable>
            )}
          </dd>
          <dt>Nearest road</dt>
          <dd>{isNumber(distance) ? formatLength(distance) : <Unavailable />}</dd>
        </dl>
      </section>

      <ParcelReasoning reasoning={reasoning} />
    </>
  );
}

/**
 * Land cover the AI found inside a parcel boundary. Used for candidate
 * parcels and for records of an existing GIS layer.
 */
export function ParcelReasoning({ reasoning, title = "Inside the parcel boundary" }) {
  return (
    <section>
      <h4>{title}</h4>
      {!reasoning ? (
        <Unavailable>Spatial reasoning not loaded</Unavailable>
      ) : !reasoning.available ? (
        <Unavailable>{reasoning.message || "Spatial reasoning unavailable"}</Unavailable>
      ) : (
        <>
          <dl className="kv">
            <dt>Buildings</dt>
            <dd>
              {formatNumber(reasoning.building_count)}
              {reasoning.building_fragment_count
                ? ` (+${formatNumber(reasoning.building_fragment_count)} fragments)`
                : ""}
            </dd>
            <dt>Building area</dt>
            <dd>{formatArea(reasoning.building_area_m2)}</dd>
            <dt>Area within outer boundary</dt>
            <dd>{formatArea(reasoning.envelope_area_m2)}</dd>
            {isNumber(reasoning.interior_hole_count) ? (
              <>
                <dt>Interior holes</dt>
                <dd>{formatNumber(reasoning.interior_hole_count)}</dd>
              </>
            ) : null}
          </dl>

          {reasoning.composition?.length ? (
            <>
              <div
                className="composition"
                role="img"
                aria-label="Land-cover composition inside the parcel boundary"
              >
                {reasoning.composition.map((entry) => (
                  <span
                    key={entry.class_key}
                    title={`${entry.class_name}: ${formatArea(entry.area_m2)}`}
                    style={{
                      width: `${Math.max((entry.share || 0) * 100, 0.5)}%`,
                      background: (CLASS_STYLE[entry.class_key] || CLASS_STYLE.unknown).color,
                    }}
                  />
                ))}
              </div>
              <dl className="kv">
                {reasoning.composition.map((entry) => (
                  <div key={entry.class_key} style={{ display: "contents" }}>
                    <dt>
                      <span
                        className="dot"
                        style={{
                          display: "inline-block",
                          marginRight: 7,
                          background: (CLASS_STYLE[entry.class_key] || CLASS_STYLE.unknown).color,
                        }}
                      />
                      {entry.class_name}
                    </dt>
                    <dd>
                      {formatArea(entry.area_m2)} · {formatPercent(entry.share)}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <p className="muted" style={{ fontSize: "0.9rem" }}>
              No AI features fall inside this boundary.
            </p>
          )}
          <p className="uncertainty__note">
            {reasoning.scenario_label}. {reasoning.method}.
          </p>
        </>
      )}
    </section>
  );
}
