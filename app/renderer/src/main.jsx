import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import './styles.css';

const DATA = {
  graph: 'data/build/graph.json',
  rules: 'data/build/rules.json',
  buildings: 'data/build/buildings/manifest.json',
  roads: 'data/build/roads/manifest.json'
};
const turns = ['left', 'straight', 'right'];

async function readJson(path) {
  return JSON.parse(await window.myTestDrive.readText(path));
}

function getCells(points) {
  return points.reduce((cells, point) => {
    cells.add(`${Math.floor(point[0] / 500)}:${Math.floor(point[1] / 500)}`);
    return cells;
  }, new Set());
}

function buildWorld(graph, buildings, roads) {
  const byId = new Map(graph.edges.map((edge) => [edge.id, edge]));
  const outgoing = new Map();
  for (const edge of graph.edges) {
    if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
    outgoing.get(edge.from).push(edge);
  }
  return {
    byId,
    outgoing,
    buildingFiles: new Map(buildings.chunks.map((chunk) => [`${chunk.cell[0]}:${chunk.cell[1]}`, chunk.file])),
    roadFiles: new Map(roads.surfaces.map((chunk) => [`${chunk.cell[0]}:${chunk.cell[1]}`, chunk.file]))
  };
}

function edgePoint(edge, distance) {
  let travelled = 0;
  for (let index = 1; index < edge.points.length; index += 1) {
    const start = edge.points[index - 1];
    const end = edge.points[index];
    const length = Math.hypot(end[0] - start[0], end[1] - start[1]);
    if (travelled + length >= distance) {
      const ratio = Math.max(0, Math.min(1, (distance - travelled) / length));
      return new THREE.Vector3(
        THREE.MathUtils.lerp(start[0], end[0], ratio),
        THREE.MathUtils.lerp(start[2], end[2], ratio),
        THREE.MathUtils.lerp(start[1], end[1], ratio)
      );
    }
    travelled += length;
  }
  const last = edge.points[edge.points.length - 1];
  return new THREE.Vector3(last[0], last[2], last[1]);
}

function makeScene(canvas, world) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#9db4b5');
  scene.fog = new THREE.Fog('#9db4b5', 320, 1800);
  const camera = new THREE.PerspectiveCamera(62, 1, 0.1, 2500);
  const worldGroup = new THREE.Group();
  scene.add(worldGroup);
  scene.add(new THREE.HemisphereLight('#eef5eb', '#38484a', 2.4));
  const sun = new THREE.DirectionalLight('#fff5d6', 2.2);
  sun.position.set(-300, 500, 250);
  scene.add(sun);
  const loader = new GLTFLoader();
  const loaded = new Set();

  function resize() {
    const width = canvas.clientWidth || window.innerWidth;
    const height = canvas.clientHeight || window.innerHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  async function addGlb(relativePath, key, folder) {
    if (!relativePath || loaded.has(key)) return;
    loaded.add(key);
    try {
      const buffer = await window.myTestDrive.readBinary(`data/build/${folder}/${relativePath}`);
      loader.parse(buffer, '', (gltf) => {
        gltf.scene.traverse((object) => {
          if (object.isMesh) object.frustumCulled = true;
        });
        worldGroup.add(gltf.scene);
      }, (error) => console.error(`Could not load ${relativePath}`, error));
    } catch (error) {
      console.error(`Could not read ${relativePath}`, error);
    }
  }

  async function loadForEdges(edges) {
    const cells = getCells(edges.flatMap((edge) => edge.points));
    await Promise.all([...cells].flatMap((cell) => [
      addGlb(world.buildingFiles.get(cell), `building:${cell}`, 'buildings'),
      addGlb(world.roadFiles.get(cell), `road:${cell}`, 'roads')
    ]));
  }

  function render(edge, distance) {
    const position = edgePoint(edge, distance);
    const target = edgePoint(edge, Math.min(edge.length_m, distance + 8));
    camera.position.lerp(position.clone().add(new THREE.Vector3(0, 2.1, 0)), 0.18);
    camera.lookAt(target.clone().add(new THREE.Vector3(0, 1.2, 0)));
    renderer.render(scene, camera);
  }

  function animate() {
    requestAnimationFrame(animate);
    if (world.current) render(world.current, world.distance);
  }
  animate();
  return { loadForEdges, dispose: () => window.removeEventListener('resize', resize) };
}

function App() {
  const canvasRef = useRef(null);
  const sceneRef = useRef(null);
  const [state, setState] = useState({ loading: true, error: null });

  useEffect(() => {
    let disposed = false;
    Promise.all([readJson(DATA.graph), readJson(DATA.rules), readJson(DATA.buildings), readJson(DATA.roads)])
      .then(([graph, rules, buildings, roads]) => {
        if (disposed) return;
        const world = buildWorld(graph, buildings, roads);
        const current = world.byId.get(graph.spawn.edge);
        if (!current) throw new Error(`Spawn edge ${graph.spawn.edge} is missing`);
        world.current = current;
        world.distance = graph.spawn.offset_m;
        const choices = world.outgoing.get(current.to) || [];
        const rule = rules.rules[`${current.to}|${current.id}`] || null;
        sceneRef.current = makeScene(canvasRef.current, world);
        setState({ loading: false, error: null, graph, world, current, choices, rule });
        sceneRef.current.loadForEdges([current, ...choices]);
      })
      .catch((error) => setState({ loading: false, error: error.message }));
    return () => { disposed = true; sceneRef.current?.dispose(); };
  }, []);

  function choose(edge) {
    const { world } = state;
    world.current = edge;
    world.distance = 0;
    const choices = world.outgoing.get(edge.to) || [];
    const rule = state.rule?.junction === edge.from ? state.rule : null;
    setState((previous) => ({ ...previous, current: edge, choices, rule }));
    sceneRef.current?.loadForEdges([edge, ...choices]);
  }

  const { current, choices, rule } = state;
  return <main className="app-shell">
    <canvas ref={canvasRef} className="world-canvas" />
    {state.loading && <div className="loading-overlay">Loading the Spandau driving world<span>Building the first candidate streets...</span></div>}
    {state.error && <div className="loading-overlay error">Could not start the world<span>{state.error}</span></div>}
    {!state.loading && !state.error && <>
      <header className="topbar"><div><span className="eyebrow">MYTESTDRIVE / SPANDAU</span><h1>Free roam</h1></div><div className="status"><span className="status-dot" /> LIVE WORLD <strong>{state.graph.edges.length.toLocaleString('de-DE')} edges</strong></div></header>
      <aside className="panel">
        <div className="panel-heading"><span className="eyebrow">CURRENT APPROACH</span><h2>{current.name || 'Unnamed street'}</h2><p>{current.highway} · {Math.round(current.length_m)} m ahead</p></div>
        <section className={`rule ${rule?.scored ? 'scored' : 'hint'}`}><div className="rule-kicker">{rule?.scored ? 'SCORED RULE' : 'HINT ONLY'}</div><strong>{rule?.label || 'Continue with care'}</strong>{rule?.conflicts?.length ? <p>{rule.conflicts.length} source conflict{rule.conflicts.length === 1 ? '' : 's'} · not scored</p> : <p>{rule?.street || current.name || 'Road priority'}</p>}</section>
        <div className="choice-label"><span className="eyebrow">NEXT JUNCTION</span><span>Choose your line</span></div>
        <div className="choices">{turns.map((turn) => { const edge = choices.find((candidate) => candidate.turn === turn); return <button key={turn} className="choice" disabled={!edge} onClick={() => choose(edge)}><span className="turn-arrow">{turn === 'left' ? '↙' : turn === 'right' ? '↘' : '↓'}</span><span><b>{turn}</b><small>{edge?.name || (edge ? 'Unnamed street' : 'No route')}</small></span></button>; })}</div>
        <footer className="panel-footer"><span>RAIL-LOCKED CAMERA</span><span>DATA-CONFIRMED RULES</span></footer>
      </aside>
    </>}
  </main>;
}

createRoot(document.getElementById('root')).render(<App />);
