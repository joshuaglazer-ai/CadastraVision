import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Stars } from "@react-three/drei";
import * as THREE from "three";
import {
  NETWORK_NODES,
  SURVEY_SITE,
  arcPoints,
  facingRotation,
  graticule,
  landPoints,
  lonLatToXYZ,
  paintEarthTexture,
} from "../lib/earth";

const RADIUS = 2;

const ATMOSPHERE_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

// Glow from the angle between the surface and the viewer. With uInner = 0
// it is a rim light on the globe itself (brightest at the limb); with
// uInner = 1 it is the halo shell outside the globe, which fades outward.
const ATMOSPHERE_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uPower;
  uniform float uStrength;
  uniform float uInner;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    float facing = abs(dot(vNormal, vView));
    float glow = pow(mix(1.0 - facing, facing, uInner), uPower);
    gl_FragColor = vec4(uColor, glow * uStrength);
  }
`;

function usePrefersReducedMotion() {
  return useMemo(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    []
  );
}

function Earth({ interactive }) {
  const group = useRef();
  const pulse = useRef();
  const pointer = useRef({ x: 0, y: 0 });
  const reducedMotion = usePrefersReducedMotion();

  const texture = useMemo(() => {
    const map = new THREE.CanvasTexture(paintEarthTexture(2048));
    map.colorSpace = THREE.SRGBColorSpace;
    map.anisotropy = 4;
    return map;
  }, []);

  const land = useMemo(() => landPoints(16000, RADIUS * 1.004), []);
  const grid = useMemo(() => graticule(15, RADIUS * 1.002), []);

  const site = useMemo(
    () => lonLatToXYZ(SURVEY_SITE.position[0], SURVEY_SITE.position[1], RADIUS * 1.01),
    []
  );
  const nodes = useMemo(
    () => NETWORK_NODES.map((node) => lonLatToXYZ(node.position[0], node.position[1], RADIUS * 1.008)),
    []
  );
  const arcs = useMemo(
    () => NETWORK_NODES.map((node) => arcPoints(SURVEY_SITE.position, node.position, RADIUS * 1.008, 0.05, 32)),
    []
  );

  const atmosphereUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color("#63e3ee") },
      uPower: { value: 3.2 },
      uStrength: { value: 0.9 },
      uInner: { value: 0 },
    }),
    []
  );
  const haloUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color("#2aa9dc") },
      uPower: { value: 2.4 },
      uStrength: { value: 1.5 },
      uInner: { value: 1 },
    }),
    []
  );

  useEffect(() => () => texture.dispose(), [texture]);

  useEffect(() => {
    if (!interactive) return undefined;
    const onMove = (event) => {
      pointer.current.x = (event.clientX / window.innerWidth) * 2 - 1;
      pointer.current.y = (event.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, [interactive]);

  useFrame((state, delta) => {
    if (!group.current) return;
    if (!reducedMotion) {
      group.current.rotation.y += delta * 0.035;
    }
    // Gentle parallax: the globe leans a little toward the pointer.
    const targetX = 0.32 + pointer.current.y * 0.06;
    const targetZ = -pointer.current.x * 0.04;
    group.current.rotation.x += (targetX - group.current.rotation.x) * 0.04;
    group.current.rotation.z += (targetZ - group.current.rotation.z) * 0.04;

    if (pulse.current && !reducedMotion) {
      const t = (state.clock.elapsedTime % 2.6) / 2.6;
      pulse.current.scale.setScalar(1 + t * 3.2);
      pulse.current.material.opacity = 0.75 * (1 - t);
    }
  });

  // The survey site faces the viewer when the page opens.
  const initialRotation = useMemo(() => facingRotation(SURVEY_SITE.position[0] - 12), []);

  return (
    <group ref={group} rotation={[0.32, initialRotation, 0]}>
      <mesh>
        <sphereGeometry args={[RADIUS, 96, 96]} />
        <meshStandardMaterial
          map={texture}
          roughness={0.62}
          metalness={0.12}
          emissive="#06223a"
          emissiveIntensity={0.55}
        />
      </mesh>

      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[grid, 3]} />
        </bufferGeometry>
        <lineBasicMaterial color="#7fe9ee" transparent opacity={0.13} />
      </lineSegments>

      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[land, 3]} />
        </bufferGeometry>
        <pointsMaterial color="#9af3f5" size={0.02} sizeAttenuation transparent opacity={0.85} />
      </points>

      {arcs.map((positions, index) => (
        <line key={index}>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[positions, 3]} />
          </bufferGeometry>
          <lineBasicMaterial color="#63e3ee" transparent opacity={0.32} />
        </line>
      ))}

      {nodes.map((position, index) => (
        <mesh key={index} position={position}>
          <sphereGeometry args={[0.014, 10, 10]} />
          <meshBasicMaterial color="#eaf6fb" />
        </mesh>
      ))}

      <mesh position={site}>
        <sphereGeometry args={[0.026, 14, 14]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
      <mesh ref={pulse} position={site}>
        <sphereGeometry args={[0.03, 14, 14]} />
        <meshBasicMaterial color="#63e3ee" transparent opacity={0.7} depthWrite={false} />
      </mesh>

      <mesh scale={1.012}>
        <sphereGeometry args={[RADIUS, 64, 64]} />
        <shaderMaterial
          vertexShader={ATMOSPHERE_VERTEX}
          fragmentShader={ATMOSPHERE_FRAGMENT}
          uniforms={atmosphereUniforms}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <mesh scale={1.16}>
        <sphereGeometry args={[RADIUS, 64, 64]} />
        <shaderMaterial
          vertexShader={ATMOSPHERE_VERTEX}
          fragmentShader={ATMOSPHERE_FRAGMENT}
          uniforms={haloUniforms}
          transparent
          depthWrite={false}
          side={THREE.BackSide}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
  );
}

/**
 * The Earth: ocean-shaded sphere, latitude / longitude grid, land picked
 * out in points, a survey network over India and a soft atmosphere.
 */
export default function Globe({ interactive = true, className = "" }) {
  return (
    <div className={`globe ${className}`} aria-hidden="true">
      <Canvas
        camera={{ position: [0, 0, 6.4], fov: 40, near: 0.1, far: 200 }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true }}
      >
        <ambientLight intensity={0.85} />
        <directionalLight position={[-4, 3, 6]} intensity={2.1} color="#dff6ff" />
        <directionalLight position={[5, -2, -4]} intensity={0.5} color="#1ba3d8" />
        <Stars radius={90} depth={50} count={2200} factor={2.6} saturation={0} fade speed={0.25} />
        <Earth interactive={interactive} />
      </Canvas>
    </div>
  );
}
