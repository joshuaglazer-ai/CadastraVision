import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { PRODUCT } from "../lib/constants";
import BrandMark from "./BrandMark";

export default function PublicNav() {
  const { authenticated } = useAuth();
  return (
    <header className="public__nav">
      <Link to="/" className="brand">
        <BrandMark size={32} />
        <span>
          <span className="brand__name">{PRODUCT.name}</span>
          <span className="brand__sub">{PRODUCT.tagline}</span>
        </span>
      </Link>
      <nav className="row" aria-label="Site">
        <Link to="/about" className="btn btn--ghost">
          About
        </Link>
        <Link to={authenticated ? "/dashboard" : "/login"} className="btn btn--secondary">
          {authenticated ? "Open dashboard" : "Sign in"}
        </Link>
      </nav>
    </header>
  );
}
