import { NavLink, Outlet } from "react-router-dom";
import Icon from "../components/Icon";

const SECTIONS = [
  { to: "/survey", end: true, label: "Assignment", icon: "target" },
  { to: "/survey/datasets", label: "Datasets", icon: "database" },
  { to: "/survey/processing", label: "AI processing", icon: "cpu" },
  { to: "/survey/review", label: "Review", icon: "check" },
  { to: "/survey/analytics", label: "Analytics", icon: "chart" },
  { to: "/survey/export", label: "Export", icon: "download" },
];

/** Survey workspace: the sections follow the order of the work. */
export default function Survey() {
  return (
    <>
      <nav className="subnav" aria-label="Survey workspace">
        {SECTIONS.map((section) => (
          <NavLink key={section.to} to={section.to} end={section.end} className="nav-link">
            <Icon name={section.icon} size={15} />
            {section.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </>
  );
}
