import { useState } from "react";
import { CLASS_STYLE } from "../lib/constants";
import { formatNumber } from "../lib/format";
import Icon from "./Icon";
import NoImageryState from "./NoImageryState";
import { noFeaturesInArea } from "../lib/emptyArea";

function Swatch({ layerKey }) {
  if (layerKey === "assigned_area") {
    return <span className="swatch swatch--dashed" style={{ color: "#5fe0e6" }} aria-hidden="true" />;
  }
  if (layerKey === "existing_gis") {
    return <span className="swatch swatch--dashed" style={{ color: "#f4f1c4" }} aria-hidden="true" />;
  }
  if (layerKey === "gnss") {
    return <span className="swatch swatch--point" style={{ color: "#3fd6a3", background: "#fff" }} aria-hidden="true" />;
  }
  if (layerKey === "dsm" || layerKey === "dtm") {
    return (
      <span
        className="swatch"
        style={{ color: "#9fbccf", background: "linear-gradient(90deg,#0a3a5c,#2496a0,#decd96)" }}
        aria-hidden="true"
      />
    );
  }
  const style = CLASS_STYLE[layerKey] || CLASS_STYLE.unknown;
  return (
    <span
      className="swatch"
      style={{ color: style.color, background: `${style.fill}55` }}
      aria-hidden="true"
    />
  );
}

/**
 * Layer switches. A layer whose dataset does not exist is shown disabled
 * with the reason, never hidden and never faked.
 */
export default function LayerControl({ catalog, visible, onToggle, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  const layers = catalog?.layers || [];
  // A missing file disables several rows at once (five land-cover classes);
  // its explanation is shown on the first of them only.
  const explained = new Set();
  const fallbacks = Object.values(catalog?.data_files || {}).filter(
    (file) => file && file.status === "fallback" && file.message
  );
  const groups = [];
  for (const layer of layers) {
    let group = groups.find((item) => item.name === layer.group);
    if (!group) {
      group = { name: layer.group, layers: [] };
      groups.push(group);
    }
    group.layers.push(layer);
  }

  return (
    <div className="map-card layer-control">
      <button
        type="button"
        className="layer-control__head"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="row" style={{ gap: 8 }}>
          <Icon name="layers" size={16} /> Layers
        </span>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={16} />
      </button>

      {open ? (
        <div className="layer-control__body">
          {noFeaturesInArea(catalog) ? <NoImageryState compact /> : null}
          {groups.map((group) => (
            <div key={group.name}>
              <div className="layer-group">{group.name}</div>
              {group.layers.map((layer) => {
                const disabled = !layer.available;
                let reason = null;
                if (disabled) {
                  reason = explained.has(layer.message) ? "Same reason as above" : layer.message;
                  explained.add(layer.message);
                }
                return (
                  <label
                    key={layer.key}
                    className={`layer-row ${disabled ? "layer-row--disabled" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={Boolean(visible[layer.key]) && !disabled}
                      disabled={disabled}
                      onChange={(event) => onToggle(layer.key, event.target.checked)}
                    />
                    <Swatch layerKey={layer.key} />
                    <span>{layer.label}</span>
                    <span className="layer-row__count">
                      {layer.available && layer.count != null && layer.key !== "assigned_area"
                        ? formatNumber(layer.count)
                        : ""}
                    </span>
                    {reason ? <span className="layer-row__reason">{reason}</span> : null}
                    {layer.key === "assigned_area" && layer.demo ? (
                      <span className="layer-row__reason">Demo boundary</span>
                    ) : null}
                  </label>
                );
              })}
            </div>
          ))}
          {!layers.length ? <p className="layer-row__reason">No layers</p> : null}
          {fallbacks.map((file) => (
            <p key={file.expected} className="layer-row__reason">
              {file.message}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
