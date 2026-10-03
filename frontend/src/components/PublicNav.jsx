import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { PRODUCT } from "../lib/constants";
import logo from "../assets/brand/cadastra-vision-logo-on-dark.png";

export default function PublicNav() {
  const { authenticated } = useAuth();
  return (
    <header className="public__nav">
      <Link to="/" className="brand" aria-label={`${PRODUCT.name} home`}>
        <img className="brand__logo" src={logo} width={144} height={48} alt={PRODUCT.name} />
        <span className="brand__sub">{PRODUCT.tagline}</span>
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
