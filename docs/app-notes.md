# The app — renderer notes

What the renderer has to agree with the pipeline about, what happened when it didn't, and the
Phase 5 gate numbers. Companion to [measured-counts.md](measured-counts.md), which covers the
pipeline side.

---

## The contract between `data/build/` and the renderer

Five things the renderer must get right. Each of these was wrong in the first version of
[`app/renderer/src/main.jsx`](../app/renderer/src/main.jsx), and every one of them failed
quietly — the app started, showed a panel, and rendered a window.

### 1. One frame, two conventions

The pipeline writes **everything** — `graph.json` points and every GLB vertex — in a single
Z-up metric frame: `x` east, `y` north, `z` elevation, metres, relative to the TÜV anchor.
Three.js is Y-up.

There is no node translation in the GLBs and no scene rotation. The geometry is in world
coordinates already, so the *only* correct move is to convert once, consistently:

```js
worldGroup.rotation.x = -Math.PI / 2;    // maps (x, y, z) -> (x, z, -y)
const toScene = (x, y, z) => new THREE.Vector3(x, z, -y);   // camera must match exactly
```

Converting the camera but not the geometry (or vice versa) puts the streets and the buildings
in different worlds. Nothing errors; you fly through empty space beside a city lying on its side.

**How to tell which component is elevation** without trusting a comment: read the accessor
bounds. In `road_-11_-10.glb` the third component spans 47.22234 → 47.22273 — 0.4mm across a
chunk whose other two axes span hundreds of metres. Flat roads make the elevation axis obvious.

### 2. A position is an edge **plus a direction**

This is the one that generates the most downstream bugs, because tracking only the edge looks
like it works right up until it doesn't.

`graph.json` keys its turn tables by **`"<edgeId>:<direction>"`**, direction being `+1` or `-1`:

```
graph.junctions["j13418555801"].turns["e6261:1"]
  -> [{ edge: "e439", direction: 1, relative: -0.1, turn: "straight", name: "…" }]
```

Everything follows from carrying that pair:

| | direction `+1` | direction `-1` |
|---|---|---|
| Arrival junction | `j${edge.to}` | `j${edge.from}` |
| Polyline | `edge.points` | `edge.points` reversed |
| Turn table key | `${edge.id}:1` | `${edge.id}:-1` |

A two-way street has a **different arrival junction, a different rule and a reversed geometry**
depending on which way it is driven. Edges carry **no `turn` property** — turn labels exist only
inside a junction's turn table, because "left" is a fact about an approach, not about a street.

### 3. Rule keys carry a `j` prefix

```
rules.json  ->  "j13418555801|e6261"        not  "13418555801|e6261"
```

Junction ids are `j` + the OSM node id throughout `graph.json` and `rules.json`; edge endpoints
(`edge.from`, `edge.to`) are bare node ids. Building the key from the bare id misses **every**
one of the 11,844 approaches, and the miss is indistinguishable from "this approach has no
special rule" — the panel just reads *Continue with care* forever.

Verified after the fix by walking the whole reachable network from the spawn:
**10,802 approaches reachable, every one resolving a rule, zero inescapable edges.**

### 4. The chunk GLBs ship no materials and no normals

Deliberately. They carry `POSITION`, an index buffer, and one custom attribute — nothing else:

| File | Custom attribute | Meaning | Primitive mode |
|---|---|---|---|
| `buildings/chunk_*.glb` | `_BUILDING` | index into that chunk's `manifest.buildings` | triangles |
| `roads/road_*.glb` | `_MATERIAL` | surveyed surface code (13 distinct) | triangles |
| `roads/marking_*.glb` | — | — | **LINES** (mode 1) |

Verified across every chunk: 369/369 building chunks carry `_BUILDING`, 281/281 marking chunks
are LINES. No chunk can silently fall back to the default material.

With no material in the file, glTF says use the default — which is **`metallicFactor: 1.0`**.
A fully metallic surface with no environment map reflects nothing, so three.js renders it
black. Horizontal faces catch enough hemisphere light to look tan; vertical walls go pure
black. The symptom reads like a texture or lighting bug and is neither.

The renderer therefore supplies both material and normals, matching
[`tools/preview_chunk.html`](../tools/preview_chunk.html), which solved this first:

```js
new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true })
```

`flatShading` derives normals in the shader — without it, no normals means black under Lambert
regardless of the metalness fix. Roads additionally want `side: DoubleSide` (earcut winding on
ground polygons is not guaranteed to face up) and `polygonOffset` (markings sit ~4cm above the
surface and the depth buffer cannot resolve that at distance).

### 5. Most graph nodes are not junctions

6,001 junctions, but most are polyline splits with exactly one continuation. Over the network
reachable from the spawn: **6,528 single-option approaches against 3,957 real ones.** Treating
every node as a decision point asks the driver to click "straight" every few metres — the spawn
edge alone is 10m long and would demand a click before the car has moved.

The app drives through single-option nodes and stops only where there is a genuine choice.
This is a deliberate departure from the README's "at every junction you pick left, right or
straight", recorded here rather than left to be rediscovered.

---

## Chunk streaming

Chunks are 500m. Fog reaches 1800m. Loading only the cells the current polyline crosses leaves
the horizon empty, so the renderer keeps a **ring** (`LOAD_RADIUS = 2`, a 5×5 cell window)
around the driver, re-evaluated whenever the driver crosses a cell boundary.

Two things worth keeping:

- A failed chunk read must **not** be recorded as loaded, or one transient failure blacklists
  that chunk for the session. Track in-flight separately from succeeded.
- Chunks are never evicted. The ceiling is the full 86 MB of GLB, which the gate shows is fine
  to hold resident, so eviction is not currently worth its complexity.

---

## Phase 5 gate

Measured by [`tools/measure_gate.cjs`](../tools/measure_gate.cjs), which boots the real app and
drives it through a debug handle the renderer exposes (`window.__mtd`), so the numbers belong to
the shipping renderer. Raw output in [gate-phase5.json](gate-phase5.json). Full reasoning and the
stop-rule decision are in [PLAN.md](../PLAN.md#phase-5--spike-1-electron-baseline---gate).

Headline: **the gate clears**, and the interesting part is *why* it first didn't.

At device pixel ratio 2 the app measured 51fps at full payload. The stop rule's hypothesis for
an fps miss was insufficient culling, which would have sent the project to Godot for 3–5 days.
Culling was never the problem — 992 resident chunks cull to **119 draw calls and ~490k
triangles**. Two isolating measurements:

| Test | Result | Conclusion |
|---|---|---|
| `matrixAutoUpdate = false` on all static chunks | no change | not CPU / scene-graph bound |
| Halve the pixels | straight to the 60Hz cap | **fill-rate bound** |

The pixel-ratio knee is sharp: 4.5 Mpx → 51.3fps, 3.4 Mpx → 58.5, **2.5 Mpx → 60.2**, and flat
below that. The renderer caps device pixel ratio at 1.5.

**Fragment cost at a given resolution is a property of the pixels, not the engine.** Godot would
shade the same 4.5 Mpx for the same price, so an engine rewrite could not have bought what a
one-line quality cap did.

> **Not yet in the payload:** the 16,342 signs, because sign geometry does not exist yet — there
> is no `build_signs.py`. The fps result is a floor and must be re-measured once signs land.

---

## Running it

```bash
npm start                                                  # build + launch
env -u ELECTRON_RUN_AS_NODE npm start                      # if launched from VSCode's terminal
env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/measure_gate.cjs
```

**`ELECTRON_RUN_AS_NODE=1` makes Electron run as plain Node**: no window, no error, exit code 0.
VSCode's integrated terminal exports it, and it is inherited by everything launched from there,
including `npm start`. It looks exactly like an app that starts and immediately does nothing.
