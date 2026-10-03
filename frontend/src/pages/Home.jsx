import { Link } from "react-router-dom";
import Globe from "../components/Globe";
import Icon from "../components/Icon";
import PublicNav from "../components/PublicNav";
import { useAuth } from "../context/AuthContext";
import { PRODUCT } from "../lib/constants";

const STEPS = [
  ["Assignment", "Each surveyor opens on their own assigned area: district, taluk, village and boundary."],
  ["Datasets", "Drone orthoimagery, DSM and DTM, existing GIS, land records and GNSS points are discovered and checked."],
  ["Segmentation", "A U-Net with a ResNet34 encoder classifies every pixel as field, building, road, water or other."],
  ["GIS features", "Regions become polygons with area and perimeter measured in a projected CRS."],
  ["Uncertainty", "Confidence and entropy from the model rank each feature for review."],
  ["Verification", "The surveyor approves, edits, flags or adds ground truth. Every decision is logged."],
  ["Export", "GeoJSON that says, feature by feature, what is AI generated and what is surveyor verified."],
];

const SOURCES = [
  ["satellite", "Drone and satellite imagery", "High-resolution orthoimagery is the input to segmentation."],
  ["mountain", "DSM and DTM", "Measured terrain. Building height is DSM minus DTM, never an estimate."],
  ["shapes", "Existing maps and land records", "Authoritative layers are overlaid with AI features for parcel reasoning."],
  ["road", "GIS reference data", "Roads, utilities and Survey of India layers for context."],
  ["file", "Owner documents", "Kept alongside the parcel for the surveyor to read."],
  ["crosshair", "GNSS and field devices", "Ground positions and observations recorded as ground truth."],
];

export default function Home() {
  const { authenticated } = useAuth();
  return (
    <div className="public">
      <PublicNav />

      <section className="hero">
        <Globe className="hero__globe" />
        <div className="hero__copy fade-in">
          <h1>Survey faster. Verify everything.</h1>
          <p>
            {PRODUCT.name} turns drone imagery into GIS features a surveyor can check: buildings,
            roads, fields, water and candidate parcels, each with the model's own uncertainty.
          </p>
          <div className="principle" aria-label="Operating principle">
            <span>AI proposes</span>
            <span>GIS validates</span>
            <span>Surveyors verify</span>
          </div>
          <div className="hero__actions">
            <Link to={authenticated ? "/dashboard" : "/login"} className="btn btn--primary">
              {authenticated ? "Open dashboard" : "Sign in to your assignment"}
            </Link>
            <Link to="/about" className="btn btn--secondary">
              How it works
            </Link>
          </div>
        </div>
      </section>

      <section className="section">
        <h2>From assignment to export, in one workflow</h2>
        <p>
          The model does the tracing. The GIS does the measuring. The surveyor makes the decision,
          and the record shows who decided what.
        </p>
        <ol className="flow">
          {STEPS.map(([title, text]) => (
            <li key={title}>
              <h3>{title}</h3>
              <p>{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="section">
        <h2>Every source a survey draws on</h2>
        <p>
          Sources that are not present are shown as missing. Nothing is filled in to make a screen
          look complete.
        </p>
        <div className="cards">
          {SOURCES.map(([icon, title, text]) => (
            <article className="card" key={title}>
              <h3>
                <Icon name={icon} size={18} />
                {title}
              </h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <footer className="footer">
        {PRODUCT.name}. {PRODUCT.disclaimer}
      </footer>
    </div>
  );
}
