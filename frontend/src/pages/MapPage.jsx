import { useSearchParams } from "react-router-dom";
import MapWorkbench from "../components/MapWorkbench";
import { useWorkspace } from "../context/WorkspaceContext";
import useFillHeight from "../lib/useFillHeight";

/** The map on its own, as large as the window allows, in 2D or 3D. */
export default function MapPage() {
  const { source } = useWorkspace();
  const [params] = useSearchParams();
  const initialView = params.get("view") === "3d" ? "3d" : "2d";
  const [fillRef, height] = useFillHeight({ min: 520, bottom: 16 });

  return (
    <main className="page page--wide" style={{ paddingTop: 16, paddingBottom: 16 }}>
      <div ref={fillRef}>
        <MapWorkbench source={source} height={height} initialView={initialView} />
      </div>
    </main>
  );
}
