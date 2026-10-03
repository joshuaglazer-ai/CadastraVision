import { Link } from "react-router-dom";
import PublicNav from "../components/PublicNav";
import { useAuth } from "../context/AuthContext";
import { PRODUCT } from "../lib/constants";

export default function About() {
  const { authenticated } = useAuth();
  return (
    <div className="public">
      <PublicNav />

      <section className="section" style={{ borderTop: 0, background: "transparent", paddingTop: 34 }}>
        <h1>About {PRODUCT.name}</h1>
        <div className="prose" style={{ marginTop: 18 }}>
          <p>
            {PRODUCT.name} is a survey-assistance platform built for Smart India Hackathon problem
            statement 12. It helps a land surveyor get from drone imagery to a checked set of GIS
            features for an assigned village.
          </p>
          <p>
            <strong>{PRODUCT.principle}</strong> The model suggests where features are. Geometry and
            topology checks test those suggestions. A surveyor accepts, corrects or rejects each one.
          </p>
        </div>
      </section>

      <section className="section">
        <h2>What the model is</h2>
        <div className="prose" style={{ marginTop: 14 }}>
          <p>
            A U-Net with a ResNet34 encoder, trained on SVAMITVA drone orthoimagery. It reads 8-bit
            RGB tiles of 512 pixels and labels each pixel as one of six classes: background, field,
            building, road, water or other.
          </p>
          <p>
            It can be run on new survey imagery of a similar kind. How well it does there depends on
            the place, the sensor, the resolution, the season and the light. That is why every result
            carries the model's confidence and entropy, and why nothing is final until a surveyor has
            looked at it.
          </p>
        </div>
      </section>

      <section className="section">
        <h2>What the output is, and is not</h2>
        <div className="prose" style={{ marginTop: 14 }}>
          <ul>
            <li>
              <strong>AI generated features are preliminary.</strong> They are labelled as such on the
              map, in every panel and in every export.
            </li>
            <li>
              <strong>A candidate parcel is a spatial candidate.</strong> It is a contiguous region
              the model saw as one field. It is not a cadastral parcel and says nothing about who owns
              the land.
            </li>
            <li>
              <strong>Uncertainty ranks, it does not judge.</strong> Low confidence is a reason to
              look at a feature first. It is not proof that the feature is wrong.
            </li>
            <li>
              <strong>Height is measured or absent.</strong> Building height is DSM elevation minus
              DTM elevation. Without both rasters the 3D view shows flat footprints.
            </li>
            <li>
              <strong>Missing data is shown as missing.</strong> The application does not invent
              counts, areas, terrain or assignments.
            </li>
          </ul>
        </div>
      </section>

      <section className="section">
        <h2>Where surveyors work</h2>
        <div className="prose" style={{ marginTop: 14 }}>
          <ul>
            <li><strong>Dashboard:</strong> the assignment, key figures, the map and the review queue.</li>
            <li><strong>Map:</strong> the full 2D GIS map with layer switches, and the 3D terrain view.</li>
            <li><strong>Datasets:</strong> every data source for the assignment and what is missing.</li>
            <li><strong>Processing:</strong> upload a GeoTIFF and run the model, stage by stage.</li>
            <li><strong>Review:</strong> approve, edit, flag or reject features and add ground truth.</li>
            <li><strong>Analytics and export:</strong> totals from the data, and GIS-ready files.</li>
          </ul>
          <p>
            <Link to={authenticated ? "/dashboard" : "/login"} className="btn btn--primary" style={{ marginTop: 6 }}>
              {authenticated ? "Open dashboard" : "Sign in"}
            </Link>
          </p>
        </div>
      </section>

      <footer className="footer">
        {PRODUCT.name}. {PRODUCT.disclaimer}
      </footer>
    </div>
  );
}
