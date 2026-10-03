import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useWorkspace } from "../context/WorkspaceContext";
import { PRODUCT } from "../lib/constants";
import BrandMark from "./BrandMark";
import Icon from "./Icon";

function initials(name) {
  return String(name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export default function Navbar() {
  const navigate = useNavigate();
  const { signOut, mode } = useAuth();
  const { surveyor, sources, source, setSource } = useWorkspace();

  async function handleSignOut() {
    await signOut();
    navigate("/login", { replace: true });
  }

  const available = sources.filter((item) => item.available || item.source === source);

  return (
    <header className="navbar">
      <Link to="/dashboard" className="brand" aria-label={`${PRODUCT.name} dashboard`}>
        <BrandMark />
        <span>
          <span className="brand__name">{PRODUCT.name}</span>
          <span className="brand__sub">{PRODUCT.tagline}</span>
        </span>
      </Link>

      <nav className="nav-links" aria-label="Main">
        <NavLink to="/dashboard" className="nav-link">
          <Icon name="grid" size={16} />
          <span>Dashboard</span>
        </NavLink>
        <NavLink to="/map" className="nav-link">
          <Icon name="map" size={16} />
          <span>Map</span>
        </NavLink>
        <NavLink to="/survey" className="nav-link">
          <Icon name="layers" size={16} />
          <span>Survey workspace</span>
        </NavLink>
      </nav>

      <div className="navbar__right">
        {available.length > 1 ? (
          <label className="source-select">
            <span className="muted">Layers</span>
            <select
              className="select"
              value={source}
              onChange={(event) => setSource(event.target.value)}
              aria-label="Layer source shown on the map"
            >
              {available.map((item) => (
                <option key={item.source} value={item.source}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {surveyor ? (
          <div className="user-chip" title={surveyor.email}>
            <span className="user-chip__avatar" aria-hidden="true">
              {initials(surveyor.name)}
            </span>
            <span className="user-chip__text">
              <span className="user-chip__name">{surveyor.name}</span>
              <span className="user-chip__id">{surveyor.surveyor_id}</span>
            </span>
          </div>
        ) : null}

        {mode !== "off" ? (
          <button type="button" className="btn btn--ghost btn--sm" onClick={handleSignOut}>
            <Icon name="logout" size={16} />
            Sign out
          </button>
        ) : null}
      </div>
    </header>
  );
}
