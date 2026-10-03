import { useEffect, useMemo, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import * as THREE from "three";

import {
  errorMessage,
  getMapFeatures,
  getMapParcels,
  getTerrainBuildings,
  getTerrainGrid,
  getTerrainStatus,
} from "../lib/api";
import { formatNumber } from "../lib/format";
import Icon from "./Icon";
import { ErrorState, LoadingState } from "./States";

const REQUIRED_MESSAGE = "DSM/DTM data required for measured terrain and building height.";
const SCENE_SIZE = 60; // scene units across the longer side of the extent
const MAX_FLAT_SHAPES = 1500;

/** Local metric frame centred on a bounding box (east, north in metres). */
function makeFrame(bbox) {
  const lon0 = (bbox[0] + bbox[2]) / 2;
  const lat0 = (bbox[1] + bbox[3]) / 2;
  const mPerLon = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const mPerLat = 110574;
  const width = (bbox[2] - bbox[0]) * mPerLon;
  const height = (bbox[3] - bbox[1]) * mPerLat;
  const unit = Math.max(width, height) / SCENE_SIZE || 1; // metres per scene unit
  return {
    width: width / unit,
    height: height / unit,
    unit,
    toScene: (lon, lat) => [((lon - lon0) * mPerLon) / unit, ((lat - lat0) * mPerLat) / unit],
  };
}

function polygonsOf(geometry) {
  if (!geometry) return [];
  return geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates || [];
}

/** THREE.Shape objects (x east, y north) for the outer rings of a geometry. */
function shapesFor(geometry, frame) {
  const shapes = [];
  for (const polygon of polygonsOf(geometry)) {
    const ring = polygon?.[0];
    if (!ring || ring.length < 4) continue;
    const shape = new THREE.Shape();
    ring.forEach(([lon, lat], index) => {
      const [x, y] = frame.toScene(lon, lat);
      if (index === 0) shape.moveTo(x, y);
      else shape.lineTo(x, y);
    });
    shape.closePath();
    shapes.push(shape);
  }
  return shapes;
}

function elevationColour(t) {
  const low = new THREE.Color("#0d4a78");
  const mid = new THREE.Color("#2e9aa6");
  const high = new THREE.Color("#e6dcae");
  return t < 0.5 ? low.clone().lerp(mid, t * 2) : mid.clone().lerp(high, (t - 0.5) * 2);
}

// ------------------------------------------------------- measured terrain
function TerrainSurface({ grid, frame, exaggeration, base, opacity = 1, wireframe = false }) {
  const geometry = useMemo(() => {
    const [west, south, east, north] = grid.bounds;
    const [x0, y0] = frame.toScene(west, south);
    const [x1, y1] = frame.toScene(east, north);
    const plane = new THREE.PlaneGeometry(x1 - x0, y1 - y0, grid.cols - 1, grid.rows - 1);
    const position = plane.attributes.position;
    const colours = new Float32Array(position.count * 3);
    const span = grid.max - grid.min || 1;
    for (let row = 0; row < grid.rows; row++) {
      for (let col = 0; col < grid.cols; col++) {
        const index = row * grid.cols + col;
        const value = grid.values[row][col];
        const elevation = value == null ? grid.min : value;
        position.setZ(index, ((elevation - base) * exaggeration) / frame.unit);
        const colour = elevationColour((elevation - grid.min) / span);
        colours[index * 3] = colour.r;
        colours[index * 3 + 1] = colour.g;
        colours[index * 3 + 2] = colour.b;
      }
    }
    plane.translate((x0 + x1) / 2, (y0 + y1) / 2, 0);
    plane.setAttribute("color", new THREE.BufferAttribute(colours, 3));
    plane.computeVertexNormals();
    return plane;
  }, [grid, frame, exaggeration, base]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  return (
    <mesh geometry={geometry} rotation={[-Math.PI / 2, 0, 0]}>
      <meshStandardMaterial
        vertexColors
        roughness={0.9}
        metalness={0.02}
        transparent={opacity < 1}
        opacity={opacity}
        wireframe={wireframe}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

function MeasuredBuildings({ buildings, frame, exaggeration, base }) {
  const items = useMemo(
    () =>
      buildings
        .filter((building) => building.height_m > 0.2)
        .map((building) => {
          const shapes = shapesFor(building.geometry, frame);
          if (!shapes.length) return null;
          const geometry = new THREE.ExtrudeGeometry(shapes, {
            depth: (building.height_m * exaggeration) / frame.unit,
            bevelEnabled: false,
            curveSegments: 1,
          });
          return {
            uid: building.uid,
            geometry,
            ground: ((building.ground_elevation_m - base) * exaggeration) / frame.unit,
          };
        })
        .filter(Boolean),
    [buildings, frame, exaggeration, base]
  );

  useEffect(() => () => items.forEach((item) => item.geometry.dispose()), [items]);

  return (
    <group>
      {items.map((item) => (
        <mesh
          key={item.uid}
          geometry={item.geometry}
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, item.ground, 0]}
        >
          <meshStandardMaterial color="#ffb454" roughness={0.7} metalness={0.05} />
        </mesh>
      ))}
    </group>
  );
}

// ------------------------------------------------- planimetric (no height)
function FlatFootprints({ features, frame, colour, opacity, lift }) {
  const geometry = useMemo(() => {
    const shapes = [];
    for (const feature of features.slice(0, MAX_FLAT_SHAPES)) {
      shapes.push(...shapesFor(feature.geometry, frame));
    }
    return shapes.length ? new THREE.ShapeGeometry(shapes) : null;
  }, [features, frame]);

  useEffect(() => () => geometry?.dispose(), [geometry]);
  if (!geometry) return null;

  return (
    <mesh geometry={geometry} rotation={[-Math.PI / 2, 0, 0]} position={[0, lift, 0]}>
      <meshBasicMaterial color={colour} transparent opacity={opacity} side={THREE.DoubleSide} />
    </mesh>
  );
}

function Outlines({ geometries, frame, colour, lift, dashed = false }) {
  const lines = useMemo(() => {
    const result = [];
    for (const geometry of geometries) {
      for (const polygon of polygonsOf(geometry)) {
        const ring = polygon?.[0];
        if (!ring || ring.length < 4) continue;
        const points = ring.map(([lon, lat]) => {
          const [x, y] = frame.toScene(lon, lat);
          return new THREE.Vector3(x, lift, -y);
        });
        const buffer = new THREE.BufferGeometry().setFromPoints(points);
        const line = new THREE.Line(
          buffer,
          dashed
            ? new THREE.LineDashedMaterial({ color: colour, dashSize: 0.9, gapSize: 0.6 })
            : new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.9 })
        );
        if (dashed) line.computeLineDistances();
        result.push(line);
      }
    }
    return result;
  }, [geometries, frame, colour, lift, dashed]);

  useEffect(
    () => () =>
      lines.forEach((line) => {
        line.geometry.dispose();
        line.material.dispose();
      }),
    [lines]
  );

  return (
    <group>
      {lines.map((line, index) => (
        <primitive key={index} object={line} />
      ))}
    </group>
  );
}

/**
 * 3D view of the survey area.
 *
 * With a DTM / DSM it shows the measured surface, and with both it
 * extrudes buildings to their measured height (DSM − DTM). Without them it
 * shows the footprints flat and says that elevation data is required.
 * No height is ever derived from footprint area.
 */
export default function ThreeDParcelView({ source, catalog, boundary }) {
  const [state, setState] = useState({ loading: true, error: "" });
  const [status, setStatus] = useState(null);
  const [dtm, setDtm] = useState(null);
  const [dsm, setDsm] = useState(null);
  const [heights, setHeights] = useState(null);
  const [buildings, setBuildings] = useState([]);
  const [parcels, setParcels] = useState([]);
  const [exaggeration, setExaggeration] = useState(1);
  const [showSurface, setShowSurface] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const bbox = catalog?.data_bbox || catalog?.assignment_bbox || null;
  const bboxKey = bbox ? bbox.join(",") : "";

  useEffect(() => {
    if (!bbox) {
      setState({ loading: false, error: "" });
      return undefined;
    }
    let active = true;
    setState({ loading: true, error: "" });

    (async () => {
      try {
        const terrain = await getTerrainStatus();
        if (!active) return;
        setStatus(terrain);

        const requests = [
          getMapFeatures(source, { classes: "Building", bbox: bboxKey, zoom: 19 }),
          getMapParcels(source, { bbox: bboxKey, zoom: 18 }),
          terrain.dtm.available ? getTerrainGrid(bboxKey, "dtm", 128) : null,
          terrain.dsm.available ? getTerrainGrid(bboxKey, "dsm", 128) : null,
          terrain.heights_available ? getTerrainBuildings(source, bboxKey) : null,
        ].map((request) => (request ? request.catch((error) => ({ __error: errorMessage(error) })) : null));

        const [buildingData, parcelData, dtmGrid, dsmGrid, heightData] = await Promise.all(requests);
        if (!active) return;
        setBuildings(buildingData?.features || []);
        setParcels(parcelData?.features || []);
        setDtm(dtmGrid && !dtmGrid.__error ? dtmGrid : null);
        setDsm(dsmGrid && !dsmGrid.__error ? dsmGrid : null);
        setHeights(heightData && !heightData.__error && heightData.available ? heightData : null);
        setState({ loading: false, error: "" });
      } catch (error) {
        if (active) setState({ loading: false, error: errorMessage(error) });
      }
    })();

    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, bboxKey, attempt]);

  const frame = useMemo(() => (bbox ? makeFrame(bbox) : null), [bboxKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!bbox) {
    return (
      <div className="terrain">
        <LoadingState label="No mapped area to show" detail="There are no layers for this source yet." />
      </div>
    );
  }
  if (state.loading) {
    return (
      <div className="terrain">
        <LoadingState label="Loading the 3D view" />
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="terrain">
        <ErrorState title="The 3D view could not be loaded" detail={state.error} onRetry={() => setAttempt((n) => n + 1)} />
      </div>
    );
  }

  const ground = dtm || dsm;
  const base = ground ? ground.min : 0;
  const boundaryGeometries = (boundary?.features || []).map((feature) => feature.geometry);
  const measured = Boolean(heights);
  const relief = ground ? ground.max - ground.min : 0;

  return (
    <div className="terrain">
      <Canvas
        camera={{ position: [0, 46, 58], fov: 40, near: 0.1, far: 800 }}
        dpr={[1, 1.75]}
        gl={{ antialias: true, alpha: true }}
      >
        <ambientLight intensity={1.1} />
        <directionalLight position={[40, 70, 30]} intensity={2.2} color="#eaf6fb" />
        <directionalLight position={[-30, 20, -40]} intensity={0.6} color="#1ba3d8" />

        {ground ? (
          <>
            <TerrainSurface grid={dtm || dsm} frame={frame} exaggeration={exaggeration} base={base} />
            {dtm && dsm && showSurface ? (
              <TerrainSurface grid={dsm} frame={frame} exaggeration={exaggeration} base={base} opacity={0.5} wireframe />
            ) : null}
            {measured ? (
              <MeasuredBuildings
                buildings={heights.buildings}
                frame={frame}
                exaggeration={exaggeration}
                base={base}
              />
            ) : null}
          </>
        ) : (
          <>
            <Grid
              args={[SCENE_SIZE * 2, SCENE_SIZE * 2]}
              cellSize={2}
              cellThickness={0.5}
              cellColor="#1c5f86"
              sectionSize={10}
              sectionThickness={0.9}
              sectionColor="#2f88b5"
              fadeDistance={160}
              fadeStrength={1.2}
              infiniteGrid
            />
            <FlatFootprints features={buildings} frame={frame} colour="#ffb454" opacity={0.85} lift={0.03} />
            <Outlines
              geometries={parcels.map((feature) => feature.geometry)}
              frame={frame}
              colour="#5fe0e6"
              lift={0.02}
            />
          </>
        )}

        <Outlines geometries={boundaryGeometries} frame={frame} colour="#5fe0e6" lift={0.05} dashed />

        <OrbitControls
          enableDamping
          dampingFactor={0.07}
          minDistance={8}
          maxDistance={260}
          maxPolarAngle={Math.PI * 0.49}
          target={[0, 0, 0]}
        />
      </Canvas>

      <div className="map-card terrain__legend">
        <div className="row" style={{ gap: 8 }}>
          <Icon name="mountain" size={16} />
          <strong>{ground ? "Measured terrain" : "Planimetric view"}</strong>
        </div>
        {ground ? (
          <dl className="kv" style={{ marginTop: 8, minWidth: 230 }}>
            <dt>Surface</dt>
            <dd>{dtm ? `DTM · ${dtm.dataset}` : `DSM · ${dsm.dataset}`}</dd>
            <dt>Elevation</dt>
            <dd>
              {formatNumber(ground.min, 1)} to {formatNumber(ground.max, 1)} m
            </dd>
            <dt>Relief</dt>
            <dd>{formatNumber(relief, 1)} m</dd>
            <dt>Buildings with height</dt>
            <dd>{measured ? formatNumber(heights.measured) : "None"}</dd>
          </dl>
        ) : (
          <p className="muted" style={{ marginTop: 6, maxWidth: 250 }}>
            {formatNumber(buildings.length)} building footprints and {formatNumber(parcels.length)} candidate
            parcels, drawn flat. Nothing here has height.
          </p>
        )}
        {ground ? (
          <div className="row" style={{ marginTop: 10, gap: 8 }}>
            <span className="muted">Vertical scale</span>
            <div className="btn-group" role="group" aria-label="Vertical exaggeration">
              {[1, 2, 5].map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`btn ${exaggeration === value ? "is-active" : ""}`}
                  onClick={() => setExaggeration(value)}
                >
                  {value}×
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {dtm && dsm ? (
          <label className="check" style={{ marginTop: 10 }}>
            <input type="checkbox" checked={showSurface} onChange={(event) => setShowSurface(event.target.checked)} />
            Show DSM surface
          </label>
        ) : null}
      </div>

      {!measured ? (
        <div className="notice notice--warn terrain__notice" role="status">
          <Icon name="alert" size={18} />
          <p>
            <strong>{status?.message || REQUIRED_MESSAGE}</strong> Building height is measured as DSM
            elevation minus DTM elevation. Add the rasters under Survey workspace → Datasets; nothing is
            estimated from footprint area.
          </p>
        </div>
      ) : (
        <div className="notice notice--ok terrain__notice" role="status">
          <Icon name="check" size={18} />
          <p>
            Building heights are measured: {heights.method}. Source rasters: {heights.dsm} and {heights.dtm}.
            {exaggeration > 1 ? ` Drawn with ${exaggeration}× vertical exaggeration.` : ""}
          </p>
        </div>
      )}
    </div>
  );
}
