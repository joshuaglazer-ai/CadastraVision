import { useSearchParams } from "react-router-dom";
import MapWorkbench from "../components/MapWorkbench";
import { useWorkspace } from "../context/WorkspaceContext";

/** The map on its own, as large as the window allows, in 2D or 3D. */
export default function MapPage() {
  const { source } = useWorkspace();
  const [params] = useSearchParams();
  const initialView = params.get("view") === "3d" ? "3d" : "2d";

  return (
    <main className="page page--wide" style={{ paddingTop: 14, paddingBottom: 14 }}>
      <MapWorkbench source={source} height="calc(100vh - 150px)" initialView={initialView} />
    </main>
  );
}
