import PublicNav from "../components/PublicNav";
import { PRODUCT } from "../lib/constants";

const STEPS = [
  [
    "Reads the image.",
    "Upload a georeferenced drone image. The app checks its coordinate system, resolution and coverage before anything runs.",
  ],
  [
    "Draws the first draft.",
    "A segmentation model, village or urban as the surveyor chooses, marks buildings, roads, fields, water and other land.",
  ],
  [
    "Turns pixels into a map.",
    "Shapes are cleaned, repaired and measured in metres. Open land becomes candidate parcels, and each detected building gets a candidate plot.",
  ],
  [
    "Shows where to look.",
    "Every shape carries the model's confidence and a stated reason when it needs review, so the surveyor starts with the doubtful ones.",
  ],
  [
    "Records the decision.",
    "The surveyor approves, corrects or rejects each shape and can add ground truth. Every action is logged, and the export says who verified what.",
  ],
];

export default function About() {
  return (
    <div className="public">
      <PublicNav />

      <section className="section section--lead">
        <h1>About {PRODUCT.name}</h1>
        <div className="prose">
          <p>
            {PRODUCT.name} is a surveyor&apos;s assistant for land mapping, and the name of the team that
            built it. It reads a drone image, draws the first draft of the map, and leaves every decision
            to the surveyor.
          </p>
        </div>
      </section>

      <section className="section">
        <h2>The problem</h2>
        <div className="prose">
          <p>
            Today a cadastral map starts with a person tracing buildings, roads and plot edges from drone
            imagery by hand, then checking them on the ground. It is slow, and dense settlements with
            touching roofs and narrow lanes make it slower.
          </p>
        </div>
      </section>

      <section className="section">
        <h2>How {PRODUCT.name} solves it</h2>
        <ol className="flow flow--5">
          {STEPS.map(([title, text]) => (
            <li key={title}>
              <h3>{title}</h3>
              <p>{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="section">
        <h2>What it is not</h2>
        <div className="prose">
          <p>
            Nothing here is a legal record. Candidate parcels and plots are proposals: plot lines are
            worked out from the detected buildings and roads, not read from walls or documents. The
            model&apos;s accuracy changes with place, season and image quality, which is why every result
            waits for a surveyor.
          </p>
        </div>
      </section>

      <section className="section">
        <h2>What we have measured (4 October 2026)</h2>
        <div className="prose">
          <ul>
            <li>Village buildings: IoU 0.92 to 0.94 on held-out SVAMITVA tiles.</li>
            <li>A full village image of 1 GB: 1,273 valid features in about 14 minutes on a laptop.</li>
            <li>City buildings in Bhopal: IoU 0.70 with the village model, 0.82 after fine-tuning.</li>
            <li>Candidate plots in Bhopal: 54% match exactly one hand-drawn building on our test area.</li>
          </ul>
          <p>Details and limits are in the project README.</p>
        </div>
      </section>

      <section className="section">
        <h2>Data and credits</h2>
        <div className="prose">
          <p>
            The village model was trained on SVAMITVA drone imagery. The urban model was fine-tuned on
            UAVPal, drone imagery of Bhopal (DANS Data Station, doi:10.17026/dans-z55-6gt4, CC BY-NC-SA
            4.0). The background map is for orientation only; the model never reads it.
          </p>
        </div>
      </section>

      <section className="section">
        <h2>The team</h2>
        <div className="prose">
          <p>
            Built for Smart India Hackathon 2026, problem statement SIH26012: AI-based automated urban
            parcel mapping and cadastral feature extraction using drone imagery (Department of Land
            Resources).
          </p>
        </div>
      </section>

      <footer className="footer">
        {PRODUCT.name}. {PRODUCT.disclaimer}
      </footer>
    </div>
  );
}
