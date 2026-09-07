import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import './styles.css';

const DATA = {
  graph: 'data/build/graph.json',
  rules: 'data/build/rules.json',
  buildings: 'data/build/buildings/manifest.json',
  roads: 'data/build/roads/manifest.json',
  signs: 'data/build/signs/manifest.json'
};

const CHUNK_M = 500;
const LOAD_RADIUS = 2;
const EYE_HEIGHT = 1.6;
const LOOK_AHEAD_M = 12;
// Damping rates per second, not per frame. A per-frame lerp factor silently changes the
// smoothing rate with frame rate; the gate measures 59.9fps but that is the vsync cap on this
// panel, not a guarantee anywhere else.
const POSITION_DAMPING = 13;
const HEADING_DAMPING = 6;
const TURN_ORDER = ['left', 'straight', 'right', 'uturn'];
const TURN_ARROW = { left: '↙', straight: '↓', right: '↘', uturn: '↺' };

async function readJson(path) {
  return JSON.parse(await window.myTestDrive.readText(path));
}

// The pipeline writes graph points and GLB vertices in one Z-up metric frame
// (x east, y north, z elevation). Three.js is Y-up, so the whole world hangs off a
// group rotated -90 degrees about X, which maps (x, y, z) -> (x, z, -y). Camera maths
// has to use the same mapping or the streets and the buildings end up in different worlds.
function toScene(x, y, z) {
  return new THREE.Vector3(x, z, -y);
}

function cellKey(x, y) {
  return `${Math.floor(x / CHUNK_M)}:${Math.floor(y / CHUNK_M)}`;
}

// GLTFLoader lowercases custom vertex attributes: _BUILDING arrives as _building.
// Reading the original name returns undefined and throws inside the load callback,
// where it surfaces as nothing at all (docs/measured-counts.md).
function attribute(geometry, name) {
  return geometry.getAttribute(name.toLowerCase()) || geometry.getAttribute(name);
}

// Surveyed surface material codes: asphalt dominates, the rest are setts and pavers.
const SURFACE_COLOUR = { 1: 0x8a8d96, 2: 0x7e828c, 4: 0xa08b6e, 5: 0x94866f, 6: 0xab9878, 14: 0x74777f };

function tintByAttribute(geometry, name, pick) {
  const source = attribute(geometry, name);
  if (!source) return false;
  const colours = new Float32Array(source.count * 3);
  const colour = new THREE.Color();
  for (let index = 0; index < source.count; index += 1) {
    pick(colour, source.getX(index));
    colours[index * 3] = colour.r;
    colours[index * 3 + 1] = colour.g;
    colours[index * 3 + 2] = colour.b;
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  return true;
}

// One colour per building inside a merged chunk mesh, so neighbours stay readable
// as separate volumes at one draw call per chunk.
function tintBuildings(geometry) {
  return tintByAttribute(geometry, '_BUILDING', (colour, id) =>
    colour.setHSL((id * 0.191) % 1, 0.22, 0.55 + 0.14 * Math.sin(id)));
}

function tintRoads(geometry) {
  return tintByAttribute(geometry, '_MATERIAL', (colour, code) =>
    colour.setHex(SURFACE_COLOUR[Math.round(code)] ?? 0x333539));
}

function tintSigns(geometry) {
  return tintByAttribute(geometry, '_SIGN', (colour, code) => {
    const value = Math.round(code);
    if (value === 205 || value === 206) colour.setHex(0xd83b3b);
    else if (value === 306) colour.setHex(0xf1f0df);
    else if (value === 301 || value === 311) colour.setHex(0x2c65b8);
    else colour.setHex(0xe8e4d5);
  });
}

function chunkIndex(chunks) {
  return new Map((chunks || []).map((chunk) => [`${chunk.cell[0]}:${chunk.cell[1]}`, chunk.file]));
}

function makeWorld(graph, rules, buildings, roads, signs) {
  return {
    graph,
    rules: rules.rules,
    edges: new Map(graph.edges.map((edge) => [edge.id, edge])),
    junctions: graph.junctions,
    buildingFiles: chunkIndex(buildings.chunks),
    roadFiles: chunkIndex(roads.surfaces),
    markingFiles: chunkIndex(roads.markings),
    signFiles: chunkIndex(signs.signs)
  };
}

// A position in this world is an edge *plus a direction of travel*: the graph keys its
// turn tables "<edgeId>:<direction>" and a two-way street has a different junction, a
// different rule and a reversed polyline depending on which way you drive it.
function polyline(edge, direction) {
  return direction === 1 ? edge.points : edge.points.slice().reverse();
}

function arrivalNode(edge, direction) {
  return `j${direction === 1 ? edge.to : edge.from}`;
}

function optionsFor(world, edge, direction) {
  const junction = world.junctions[arrivalNode(edge, direction)];
  const turns = (junction && junction.turns && junction.turns[`${edge.id}:${direction}`]) || [];
  return turns
    .filter((turn) => world.edges.has(turn.edge))
    .slice()
    .sort((a, b) => TURN_ORDER.indexOf(a.turn) - TURN_ORDER.indexOf(b.turn) || b.relative - a.relative);
}

// `relative` is the option's bearing against the approach: positive left, negative right
// (measured across the whole graph: straight spans -39.7..39.9, left 40.3..139.8,
// right -139.8..-40.3).
function bearingHint(relative) {
  const degrees = Math.round(Math.abs(relative));
  if (degrees < 5) return 'dead ahead';
  return `${degrees}° ${relative > 0 ? 'left' : 'right'}`;
}

// 80 approaches offer two options that land in the same left/straight/right bucket, and on 30
// of them both options carry the same street name too — two identical buttons, with no way for
// the driver to tell which is which. The turn table's own bearing separates every one of those
// 30 (minimum separation measured at 13.7°), so show it, but only where it is needed: an angle
// on every option would be noise on the 11,764 approaches that are already unambiguous.
function disambiguate(options) {
  const counts = new Map();
  for (const option of options) {
    const key = `${option.turn}|${option.name || ''}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return options.map((option) => (counts.get(`${option.turn}|${option.name || ''}`) > 1
    ? { ...option, hint: bearingHint(option.relative) }
    : option));
}

function ruleFor(world, edge, direction) {
  return world.rules[`${arrivalNode(edge, direction)}|${edge.id}`] || null;
}

function speedFor(edge) {
  const limit = Number.parseFloat(edge.maxspeed) || Number.parseFloat(edge.zone_maxspeed) || 30;
  return Math.min(Math.max(limit, 10), 50) / 3.6;
}

function polylineLength(points) {
  let total = 0;
  for (let index = 1; index < points.length; index += 1) {
    total += Math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1]);
  }
  return total;
}

function setPose(drive, edge, direction) {
  drive.edge = edge;
  drive.direction = direction;
  drive.points = polyline(edge, direction);
  drive.length = polylineLength(drive.points);
  drive.distance = 0;
  drive.speed = speedFor(edge);
  drive.moving = true;
}

function sample(points, distance) {
  let travelled = 0;
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1];
    const end = points[index];
    const span = Math.hypot(end[0] - start[0], end[1] - start[1]);
    if (span < 1e-6) continue;
    if (travelled + span >= distance || index === points.length - 1) {
      const ratio = THREE.MathUtils.clamp((distance - travelled) / span, 0, 1);
      return {
        position: toScene(
          THREE.MathUtils.lerp(start[0], end[0], ratio),
          THREE.MathUtils.lerp(start[1], end[1], ratio),
          THREE.MathUtils.lerp(start[2], end[2], ratio)
        ),
        forward: toScene(end[0] - start[0], end[1] - start[1], end[2] - start[2]).normalize()
      };
    }
    travelled += span;
  }
  const last = points[points.length - 1];
  return { position: toScene(last[0], last[1], last[2]), forward: new THREE.Vector3(0, 0, -1) };
}

function makeScene(canvas, world, drive, onArrive, readoutRef) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  // Measured, not guessed (tools/measure_gate.cjs, full 5km payload resident, 60Hz panel):
  // DPR 2 = 4.5Mpx = 51fps, 1.75 = 58.5, 1.5 = 60.2, and it stays at the vsync cap below
  // that. The renderer is fill-rate bound, not culling bound — 992 resident chunks still
  // cull to ~119 draw calls — so pixels are the knob that matters and 1.5 is the knee.
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#9db4b5');
  scene.fog = new THREE.Fog('#9db4b5', 320, 1800);
  const camera = new THREE.PerspectiveCamera(62, 1, 0.1, 2500);

  const worldGroup = new THREE.Group();
  worldGroup.rotation.x = -Math.PI / 2;
  scene.add(worldGroup);
  scene.add(new THREE.HemisphereLight('#eef5eb', '#38484a', 2.4));
  const sun = new THREE.DirectionalLight('#fff5d6', 2.2);
  sun.position.set(-300, 500, 250);
  scene.add(sun);

  // The chunk GLBs deliberately ship no materials and no NORMAL attribute, so the
  // renderer supplies both. flatShading derives normals in the shader; without it these
  // meshes light as pure black. Same shading contract as tools/preview_chunk.html.
  const buildingMaterial = new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true });
  const roadMaterial = new THREE.MeshLambertMaterial({
    vertexColors: true,
    flatShading: true,
    // earcut winding on ground polygons is not guaranteed to face up.
    side: THREE.DoubleSide,
    // Markings sit ~4cm above the surface, which the depth buffer cannot resolve at
    // distance. Push the road back rather than lifting the paint off the road.
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1
  });
  const markingMaterial = new THREE.MeshBasicMaterial({
    color: 0xf0ead6,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1
  });
  const signMaterial = new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true });

  const loader = new GLTFLoader();
  const loaded = new Set();
  const inflight = new Set();

  function shade(root, kind) {
    root.traverse((node) => {
      if (!node.isMesh && !node.isLine) return;
      if (kind === 'buildings' && tintBuildings(node.geometry)) node.material = buildingMaterial;
      else if (kind === 'roads' && tintRoads(node.geometry)) node.material = roadMaterial;
      else if (kind === 'markings') node.material = markingMaterial;
      else if (kind === 'signs' && tintSigns(node.geometry)) node.material = signMaterial;
    });
  }

  function resize() {
    const width = canvas.clientWidth || window.innerWidth;
    const height = canvas.clientHeight || window.innerHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  async function addGlb(file, key, folder, kind) {
    if (!file || loaded.has(key) || inflight.has(key)) return;
    inflight.add(key);
    try {
      const buffer = await window.myTestDrive.readBinary(`data/build/${folder}/${file}`);
      const gltf = await new Promise((resolve, reject) => loader.parse(buffer, '', resolve, reject));
      shade(gltf.scene, kind);
      worldGroup.add(gltf.scene);
      loaded.add(key);
    } catch (error) {
      // Left out of `loaded` so a transient read failure can be retried on the next pass.
      console.error(`Could not load ${folder}/${file}`, error);
    } finally {
      inflight.delete(key);
    }
  }

  // Chunks are 500m but the fog reaches 1800m, so loading only the cells the current
  // polyline crosses leaves the horizon empty. Keep a ring around the driver instead.
  let lastCell = null;
  function ensureCells(x, y) {
    const key = cellKey(x, y);
    if (key === lastCell) return;
    lastCell = key;
    const [cellX, cellY] = key.split(':').map(Number);
    for (let dx = -LOAD_RADIUS; dx <= LOAD_RADIUS; dx += 1) {
      for (let dy = -LOAD_RADIUS; dy <= LOAD_RADIUS; dy += 1) {
        const cell = `${cellX + dx}:${cellY + dy}`;
        addGlb(world.buildingFiles.get(cell), `building:${cell}`, 'buildings', 'buildings');
        addGlb(world.roadFiles.get(cell), `road:${cell}`, 'roads', 'roads');
        addGlb(world.markingFiles.get(cell), `marking:${cell}`, 'roads', 'markings');
        addGlb(world.signFiles.get(cell), `sign:${cell}`, 'signs', 'signs');
      }
    }
  }

  // Debug handle for the Phase 5 gate harness (tools/measure_gate.cjs). Nothing in the
  // app reads it; it exists so the gate can be re-measured on demand rather than trusted
  // from a one-off run. `loadAll` is the gate's full-payload stress case, not how the app
  // streams — shipping behaviour stays the ring in ensureCells.
  window.__mtd = {
    scene,
    camera,
    renderer,
    firstFrameEpoch: null,
    counts: () => ({ loaded: loaded.size, inflight: inflight.size }),
    async loadAll(concurrency = 24) {
      const jobs = [
        ...[...world.buildingFiles].map(([cell, file]) => [file, `building:${cell}`, 'buildings', 'buildings']),
        ...[...world.roadFiles].map(([cell, file]) => [file, `road:${cell}`, 'roads', 'roads']),
        ...[...world.markingFiles].map(([cell, file]) => [file, `marking:${cell}`, 'roads', 'markings']),
        ...[...world.signFiles].map(([cell, file]) => [file, `sign:${cell}`, 'signs', 'signs'])
      ];
      for (let index = 0; index < jobs.length; index += concurrency) {
        await Promise.all(jobs.slice(index, index + concurrency).map((job) => addGlb(...job)));
      }
      return jobs.length;
    }
  };

  const clock = new THREE.Clock();
  const lookMatrix = new THREE.Matrix4();
  const targetQuaternion = new THREE.Quaternion();
  let snapped = false;
  let frame = 0;

  function animate() {
    frame = requestAnimationFrame(animate);
    const delta = Math.min(clock.getDelta(), 0.1);

    if (drive.moving) {
      drive.distance += drive.speed * delta;
      if (drive.distance >= drive.length) {
        drive.distance = drive.length;
        drive.moving = false;
        onArrive();
      }
    }

    const { position, forward } = sample(drive.points, drive.distance);
    ensureCells(position.x, -position.z);

    const eye = position.clone();
    eye.y += EYE_HEIGHT;
    const focus = position.clone().addScaledVector(forward, LOOK_AHEAD_M);
    focus.y += EYE_HEIGHT;

    if (snapped) {
      // Exponential damping, so the rate is the same at any frame rate.
      camera.position.lerp(eye, 1 - Math.exp(-POSITION_DAMPING * delta));
      // Heading is damped as an orientation rather than set outright by lookAt. Picking a turn
      // swaps the rail instantly, and calling lookAt every frame snapped the view with it; the
      // camera now swings through the turn. It still only ever converges on the rail tangent,
      // so the camera stays rail-locked — this smooths the approach to that heading, it does
      // not let the view leave the rail.
      lookMatrix.lookAt(camera.position, focus, camera.up);
      targetQuaternion.setFromRotationMatrix(lookMatrix);
      camera.quaternion.slerp(targetQuaternion, 1 - Math.exp(-HEADING_DAMPING * delta));
    } else {
      camera.position.copy(eye);
      camera.lookAt(focus);
      snapped = true;
    }
    renderer.render(scene, camera);
    if (window.__mtd.firstFrameEpoch === null) window.__mtd.firstFrameEpoch = Date.now();

    // Written straight to the DOM: a per-frame setState would re-render the whole panel.
    if (readoutRef.current) {
      readoutRef.current.textContent = drive.moving
        ? `${Math.max(0, Math.round(drive.length - drive.distance))} m to the junction`
        : 'At the junction';
    }
  }
  animate();

  return {
    dispose() {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
      buildingMaterial.dispose();
      roadMaterial.dispose();
      markingMaterial.dispose();
      signMaterial.dispose();
      renderer.dispose();
    }
  };
}

function describe(world, drive, arrived) {
  return {
    status: 'ready',
    edgeCount: world.graph.edges.length,
    name: drive.edge.name || 'Unnamed street',
    highway: drive.edge.highway,
    length: drive.length,
    rule: ruleFor(world, drive.edge, drive.direction),
    options: disambiguate(optionsFor(world, drive.edge, drive.direction)),
    arrived
  };
}

function App() {
  const canvasRef = useRef(null);
  const readoutRef = useRef(null);
  const sceneRef = useRef(null);
  const worldRef = useRef(null);
  const driveRef = useRef(null);
  const arriveRef = useRef(() => {});
  const [view, setView] = useState({ status: 'loading' });

  function take(option) {
    const world = worldRef.current;
    const drive = driveRef.current;
    const edge = world.edges.get(option.edge);
    if (!edge) return;
    setPose(drive, edge, option.direction);
    setView(describe(world, drive, false));
  }

  function uTurn() {
    const world = worldRef.current;
    const drive = driveRef.current;
    setPose(drive, drive.edge, -drive.direction);
    setView(describe(world, drive, false));
  }

  arriveRef.current = () => {
    const world = worldRef.current;
    const drive = driveRef.current;
    const options = optionsFor(world, drive.edge, drive.direction);
    // Most graph nodes are just a polyline split with one continuation. Those are not a
    // choice, so roll straight through them and only stop where there is a real decision.
    if (options.length === 1) take(options[0]);
    else setView(describe(world, drive, true));
  };

  useEffect(() => {
    let disposed = false;
    Promise.all([readJson(DATA.graph), readJson(DATA.rules), readJson(DATA.buildings), readJson(DATA.roads), readJson(DATA.signs)])
      .then(([graph, rules, buildings, roads, signs]) => {
        if (disposed) return;
        const world = makeWorld(graph, rules, buildings, roads, signs);
        const edge = world.edges.get(graph.spawn.edge);
        if (!edge) throw new Error(`Spawn edge ${graph.spawn.edge} is missing from the graph`);
        const drive = {};
        // spawn.offset_m is measured from the first point of the edge, so we start
        // driving it forwards.
        setPose(drive, edge, 1);
        drive.distance = Math.min(graph.spawn.offset_m || 0, drive.length);
        worldRef.current = world;
        driveRef.current = drive;
        sceneRef.current = makeScene(canvasRef.current, world, drive, () => arriveRef.current(), readoutRef);
        setView(describe(world, drive, false));
      })
      .catch((error) => setView({ status: 'error', message: error.message }));
    return () => {
      disposed = true;
      sceneRef.current?.dispose();
    };
  }, []);

  const { rule, options = [], arrived } = view;

  return <main className="app-shell">
    <canvas ref={canvasRef} className="world-canvas" />
    {view.status === 'loading' && <div className="loading-overlay">Loading the Spandau driving world<span>Building the first candidate streets...</span></div>}
    {view.status === 'error' && <div className="loading-overlay error">Could not start the world<span>{view.message}</span></div>}
    {view.status === 'ready' && <>
      <header className="topbar">
        <div><span className="eyebrow">MYTESTDRIVE / SPANDAU</span><h1>Free roam</h1></div>
        <div className="status"><span className="status-dot" /> LIVE WORLD <strong>{view.edgeCount.toLocaleString('de-DE')} edges</strong></div>
      </header>
      <aside className="panel">
        <div className="panel-heading">
          <span className="eyebrow">CURRENT APPROACH</span>
          <h2>{view.name}</h2>
          <p>{view.highway} · {Math.round(view.length)} m</p>
        </div>
        <section className={`rule ${rule?.scored ? 'scored' : 'hint'}`}>
          <div className="rule-kicker">{rule?.scored ? 'SCORED RULE' : 'HINT ONLY'}</div>
          <strong>{rule?.label || 'Continue with care'}</strong>
          {rule?.conflicts?.length
            ? <p>{rule.conflicts.length} source conflict{rule.conflicts.length === 1 ? '' : 's'} · not scored</p>
            : <p>{rule?.street || view.name}</p>}
        </section>
        <div className="choice-label">
          <span className="eyebrow">NEXT JUNCTION</span>
          <span ref={readoutRef}>Approaching</span>
        </div>
        <div className="choices">
          {options.length === 0 && <button className="choice" disabled={!arrived} onClick={uTurn}>
            <span className="turn-arrow">{TURN_ARROW.uturn}</span>
            <span><b>Turn around</b><small>Dead end</small></span>
          </button>}
          {options.map((option, index) => <button
            key={`${option.edge}:${option.direction}:${index}`}
            className="choice"
            disabled={!arrived}
            onClick={() => take(option)}
          >
            <span className="turn-arrow">{TURN_ARROW[option.turn] || '↓'}</span>
            <span><b>{option.turn === 'uturn' ? 'turn around' : option.turn}</b><small>{option.name || 'Unnamed street'}{option.hint ? ` · ${option.hint}` : ''}</small></span>
          </button>)}
        </div>
        <footer className="panel-footer"><span>RAIL-LOCKED CAMERA</span><span>DATA-CONFIRMED RULES</span></footer>
      </aside>
    </>}
  </main>;
}

createRoot(document.getElementById('root')).render(<App />);
