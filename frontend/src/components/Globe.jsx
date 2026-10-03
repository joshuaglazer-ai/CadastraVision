import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Stars } from "@react-three/drei";
import * as THREE from "three";
import { SURVEY_SITE, facingRotation, lonLatToXYZ, paintEarthTexture } from "../lib/earth";
import Icon from "./Icon";

const RADIUS = 2;

// NASA Blue Marble / Black Marble derived textures as distributed with the
// three.js examples (see public/textures/earth/README.md).
const TEXTURES = {
  day: "/textures/earth/earth_atmos_2048.jpg",
  night: "/textures/earth/earth_lights_2048.png",
  clouds: "/textures/earth/earth_clouds_1024.png",
};

// The sun sits behind and above the globe, so the side facing the viewer is
// night: city lights carry the picture and daylight only grazes the limb.
const SUN_DIRECTION = new THREE.Vector3(-0.3, 0.42, -0.86).normalize();

const EARTH_VERTEX = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vViewNormal;
  varying vec3 vView;
  void main() {
    vUv = uv;
    vWorldNormal = normalize(mat3(modelMatrix) * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vViewNormal = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

const EARTH_FRAGMENT = /* glsl */ `
  uniform sampler2D uDay;
  uniform sampler2D uNight;
  uniform vec3 uSun;
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vViewNormal;
  varying vec3 vView;
  void main() {
    float daylight = smoothstep(0.02, 0.62, dot(normalize(vWorldNormal), uSun));
    vec3 day = texture2D(uDay, vUv).rgb;
    vec3 night = texture2D(uNight, vUv).rgb;

    // Moonlit terrain on the night side, full colour where the sun reaches.
    vec3 moonlit = day * vec3(0.09, 0.13, 0.2);
    vec3 colour = mix(moonlit, day * 1.05, daylight);

    // City lights, warm, only where it is dark.
    float lights = pow(max(max(night.r, night.g), night.b), 1.35);
    colour += vec3(1.0, 0.74, 0.38) * lights * 2.3 * (1.0 - daylight);

    // Blue scattering towards the limb.
    float rim = pow(1.0 - abs(dot(normalize(vViewNormal), normalize(vView))), 2.6);
    colour += vec3(0.16, 0.55, 0.95) * rim * 0.55;

    gl_FragColor = vec4(colour, 1.0);
    #include <colorspace_fragment>
  }
`;

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

/**
 * Loads the photographic textures. Until they arrive (or if they cannot be
 * loaded) the globe is drawn with the painted fallback texture, so the page
 * never shows an empty sphere.
 */
function useEarthTextures() {
  const fallback = useMemo(() => {
    const day = new THREE.CanvasTexture(paintEarthTexture(1024));
    day.colorSpace = THREE.SRGBColorSpace;
    const night = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
    night.needsUpdate = true;
    return { day, night, clouds: null };
  }, []);
  const [textures, setTextures] = useState(fallback);

  useEffect(() => {
    let active = true;
    const loader = new THREE.TextureLoader();
    const load = (url, srgb) =>
      new Promise((resolve, reject) => {
        loader.load(
          url,
          (texture) => {
            if (srgb) texture.colorSpace = THREE.SRGBColorSpace;
            texture.anisotropy = 4;
            resolve(texture);
          },
          undefined,
          reject
        );
      });
    Promise.all([load(TEXTURES.day, true), load(TEXTURES.night, true), load(TEXTURES.clouds, true)])
      .then(([day, night, clouds]) => {
        if (active) setTextures({ day, night, clouds });
        else [day, night, clouds].forEach((texture) => texture.dispose());
      })
      .catch(() => {
        // Keep the painted fallback; the globe is decoration, not data.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(
    () => () => {
      fallback.day.dispose();
      fallback.night.dispose();
    },
    [fallback]
  );
  useEffect(
    () => () => {
      if (textures === fallback) return;
      textures.day.dispose();
      textures.night.dispose();
      textures.clouds?.dispose();
    },
    [textures, fallback]
  );

  return textures;
}

/** Thin orbit lines around the globe, fixed relative to the viewer. */
function OrbitRings() {
  const rings = useMemo(
    () =>
      [
        { radius: RADIUS * 1.28, tilt: [1.22, 0.18, 0.32], opacity: 0.22 },
        { radius: RADIUS * 1.42, tilt: [1.38, -0.42, -0.2], opacity: 0.14 },
        { radius: RADIUS * 1.12, tilt: [0.28, 0.9, 0.12], opacity: 0.1 },
      ].map((ring) => {
        const points = new THREE.EllipseCurve(0, 0, ring.radius, ring.radius, 0, Math.PI * 2)
          .getPoints(160)
          .flatMap((point) => [point.x, point.y, 0]);
        return { ...ring, positions: new Float32Array(points) };
      }),
    []
  );
  return rings.map((ring, index) => (
    <lineLoop key={index} rotation={ring.tilt}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[ring.positions, 3]} />
      </bufferGeometry>
      <lineBasicMaterial color="#5fc8ee" transparent opacity={ring.opacity} depthWrite={false} />
    </lineLoop>
  ));
}

function Earth({ interactive, label }) {
  const group = useRef();
  const clouds = useRef();
  const pulse = useRef();
  const pointer = useRef({ x: 0, y: 0 });
  const reducedMotion = usePrefersReducedMotion();
  const textures = useEarthTextures();

  const site = useMemo(
    () => lonLatToXYZ(SURVEY_SITE.position[0], SURVEY_SITE.position[1], RADIUS * 1.004),
    []
  );
  const siteVector = useMemo(() => new THREE.Vector3(...site), [site]);
  const siteWorld = useMemo(() => new THREE.Vector3(), []);
  const toCamera = useMemo(() => new THREE.Vector3(), []);
  const projected = useMemo(() => new THREE.Vector3(), []);
  // Orient the reticle flat on the surface at the site.
  const siteQuaternion = useMemo(
    () => new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), siteVector.clone().normalize()),
    [siteVector]
  );

  const earthMaterial = useRef();
  const earthUniforms = useMemo(
    () => ({
      uDay: { value: textures.day },
      uNight: { value: textures.night },
      uSun: { value: SUN_DIRECTION },
    }),
    // The first textures only; later ones are set on the material below.
    []
  );
  useEffect(() => {
    const uniforms = earthMaterial.current?.uniforms;
    if (!uniforms) return;
    uniforms.uDay.value = textures.day;
    uniforms.uNight.value = textures.night;
  }, [textures]);

  const atmosphereUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color("#4fb8f0") },
      uPower: { value: 3.4 },
      uStrength: { value: 0.75 },
      uInner: { value: 0 },
    }),
    []
  );
  const haloUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color("#1f8fe0") },
      uPower: { value: 2.6 },
      uStrength: { value: 1.6 },
      uInner: { value: 1 },
    }),
    []
  );

  useEffect(() => {
    if (!interactive) return undefined;
    const onMove = (event) => {
      pointer.current.x = (event.clientX / window.innerWidth) * 2 - 1;
      pointer.current.y = (event.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, [interactive]);

  // The survey site faces the viewer, a little left of centre.
  const homeRotation = useMemo(() => facingRotation(SURVEY_SITE.position[0] - 10), []);

  useFrame((state) => {
    if (!group.current) return;
    const t = state.clock.elapsedTime;
    // A slow sway around India instead of a full spin keeps the site in view.
    const sway = reducedMotion ? 0 : Math.sin(t * 0.09) * 0.32;
    group.current.rotation.y = homeRotation + sway;
    const targetX = 0.42 + pointer.current.y * 0.05;
    const targetZ = -pointer.current.x * 0.035;
    group.current.rotation.x += (targetX - group.current.rotation.x) * 0.04;
    group.current.rotation.z += (targetZ - group.current.rotation.z) * 0.04;

    if (clouds.current && !reducedMotion) clouds.current.rotation.y = t * 0.006;

    if (pulse.current && !reducedMotion) {
      const phase = (t % 2.8) / 2.8;
      pulse.current.scale.setScalar(1 + phase * 2.4);
      pulse.current.material.opacity = 0.8 * (1 - phase);
    }

    // Keep the DOM label on the site; hide it when the site turns away.
    if (label?.current) {
      siteWorld.copy(siteVector).applyMatrix4(group.current.matrixWorld);
      projected.copy(siteWorld).project(state.camera);
      const x = ((projected.x + 1) / 2) * state.size.width;
      const y = ((1 - projected.y) / 2) * state.size.height;
      toCamera.copy(state.camera.position).sub(siteWorld).normalize();
      const facing = siteWorld.normalize().dot(toCamera);
      label.current.style.transform = `translate(${x}px, ${y}px)`;
      label.current.style.opacity = String(Math.max(0, Math.min(1, (facing - 0.15) * 4)));
    }
  });

  return (
    <group ref={group} rotation={[0.42, homeRotation, 0]}>
      <mesh>
        <sphereGeometry args={[RADIUS, 128, 128]} />
        <shaderMaterial
          ref={earthMaterial}
          vertexShader={EARTH_VERTEX}
          fragmentShader={EARTH_FRAGMENT}
          uniforms={earthUniforms}
        />
      </mesh>

      {textures.clouds ? (
        <mesh ref={clouds} scale={1.012}>
          <sphereGeometry args={[RADIUS, 96, 96]} />
          <meshBasicMaterial
            map={textures.clouds}
            transparent
            opacity={0.32}
            color="#b9d4e8"
            depthWrite={false}
          />
        </mesh>
      ) : null}

      <group position={site} quaternion={siteQuaternion}>
        <mesh>
          <circleGeometry args={[0.022, 24]} />
          <meshBasicMaterial color="#eaf6fb" />
        </mesh>
        <mesh>
          <ringGeometry args={[0.11, 0.118, 64]} />
          <meshBasicMaterial color="#9fe6f5" transparent opacity={0.85} side={THREE.DoubleSide} />
        </mesh>
        <mesh>
          <ringGeometry args={[0.2, 0.205, 64]} />
          <meshBasicMaterial color="#9fe6f5" transparent opacity={0.4} side={THREE.DoubleSide} />
        </mesh>
        <mesh ref={pulse}>
          <ringGeometry args={[0.04, 0.05, 48]} />
          <meshBasicMaterial color="#63e3ee" transparent opacity={0.7} depthWrite={false} side={THREE.DoubleSide} />
        </mesh>
      </group>

      <mesh scale={1.015}>
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
      <mesh scale={1.17}>
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
 * The Earth at night: city lights, moonlit terrain, clouds, a blue
 * atmosphere and orbit lines, with the survey region over India marked.
 * Decoration only; nothing on it is measured.
 */
export default function Globe({ interactive = true, showLabel = true, className = "" }) {
  const label = useRef(null);
  return (
    <div className={`globe ${className}`} aria-hidden="true">
      <Canvas
        camera={{ position: [0, 0, 6.6], fov: 40, near: 0.1, far: 200 }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true }}
      >
        <Stars radius={90} depth={50} count={1600} factor={2.2} saturation={0} fade speed={0.2} />
        <OrbitRings />
        <Earth interactive={interactive} label={showLabel ? label : null} />
      </Canvas>
      {showLabel ? (
        <div ref={label} className="globe-tag" style={{ opacity: 0 }}>
          <span className="globe-tag__inner">
            <span className="globe-tag__line" />
            <span className="globe-tag__body">
              <Icon name="target" size={16} />
              <span>
                <strong>India</strong>
                <small>Survey region acquired</small>
              </span>
            </span>
          </span>
        </div>
      ) : null}
    </div>
  );
}
