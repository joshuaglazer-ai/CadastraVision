import { useWorkspace } from "../context/WorkspaceContext";
import { PRODUCT } from "../lib/constants";
import { formatArea, titleCase } from "../lib/format";
import Icon from "./Icon";
import { Unavailable } from "./States";

function Item({ label, children }) {
  return (
    <div className="assignment-bar__item">
      <span className="assignment-bar__label">{label}</span>
      <span className="assignment-bar__value">{children}</span>
    </div>
  );
}

/** Who is surveying what: shown on every workspace page. */
export default function AssignmentBar() {
  const { surveyor, assignment } = useWorkspace();
  if (!surveyor) return null;

  const value = (text) => (text ? text : <Unavailable>Not set</Unavailable>);

  return (
    <div className="assignment-bar" aria-label="Assignment">
      <Item label="Surveyor">{surveyor.name}</Item>
      <Item label="Surveyor ID">
        <span className="mono">{surveyor.surveyor_id}</span>
      </Item>
      {assignment ? (
        <>
          <Item label="District">{value(assignment.district)}</Item>
          <Item label="Taluk">{value(assignment.taluk)}</Item>
          <Item label="Village">{value(assignment.village)}</Item>
          <Item label="Assignment">
            <span className="mono">{assignment.assignment_id}</span>
          </Item>
          <Item label="Assigned area">{formatArea(assignment.area_m2)}</Item>
          <Item label="Status">{titleCase(assignment.assignment_status)}</Item>
        </>
      ) : (
        <Item label="Assignment">
          <Unavailable>Requires administrator input</Unavailable>
        </Item>
      )}

      <div className="assignment-bar__flags">
        {assignment?.is_demo ? (
          <span
            className="badge badge--demo"
            title="This boundary is not an authoritative survey assignment."
          >
            <Icon name="alert" size={13} /> Demo assignment
          </span>
        ) : null}
        <span className="badge badge--ai" title={PRODUCT.disclaimer}>
          <Icon name="cpu" size={13} /> AI generated · Preliminary
        </span>
      </div>
    </div>
  );
}
