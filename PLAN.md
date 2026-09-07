# MyTestDrive — execution plan

Phased, with gates. A **gate** is a point where a measured result decides what happens next —
do not carry on past one on the assumption it passed.

Background and full reasoning: Brain vault, `4-Resources/mytestdrive-5km-desktop-research.md`.
Settled decisions and gotchas: [README.md](README.md).

**Anchor point:** TÜV Rheinland Prüfstelle, Pichelswerderstraße 9 — `52.5304357, 13.2144591`.
Radius: 5km. Center LoD2 tile: `LoD2_378_5821`.

---

## Phase 0 — Repository skeleton

- [x] Create `~/mytestdrive` outside the Brain vault
- [x] Directory skeleton: `pipeline/`, `app/main/`, `app/renderer/world/`, `app/renderer/ui/`,
      `data/raw/`, `data/build/`, `docs/`
- [x] `.gitignore` excluding `data/raw/`, `data/build/`, `node_modules/`, `dist/`, `.venv/`
- [x] `README.md` — settled decisions, verified figures, data sources, gotchas
- [x] `PLAN.md` — this file
- [x] `git init -b main` + first commit
- [x] Python venv: `python3 -m venv .venv && source .venv/bin/activate`
- [x] Install pipeline deps: `requests`, `shapely`, `trimesh`, `pygltflib`, `lxml`, `pyproj`
      (no `citygml-tools` — see Phase 2; there is no Java runtime here and the data is CityGML 1.0)
- [x] Freeze: `pip freeze > pipeline/requirements.txt`, commit the lockfile

**No remote.** The GitHub remote gets created from this folder later, deliberately not now.

---

## Phase 1 — Fetch real data and replace the extrapolated numbers  ← **GATE**

The single most important phase. Three of the headline figures the architecture leans on
(~46,700 buildings, ~6,260 edges, ~5,500 junctions) are **extrapolated from a 3.7–3.8x factor,
never measured.** Nothing downstream should be sized or designed until they are real.

### 1a. Road graph

- [x] `pipeline/fetch_osm.py` — Overpass query, drivable ways, 5km radius from the anchor
- [x] Fall back to `overpass.kumi.systems/api/interpreter` if rate-limited
- [x] Cache the raw response to `data/raw/osm/` so re-runs cost nothing
- [x] Verify against the known figure: 12,598 → **reconciled as `drivable+service` (12,591).
      Drivable-only is 6,001, and that is the traversable network**
- [x] Record actual signal-node count (expected **642**) → **642, exact**

### 1b. Traffic signs and roadway surfaces

- [x] `pipeline/fetch_strassenbefahrung.py` — WFS 2.0, bbox around the anchor
- [x] **Use lat,lon order in the bbox** with `urn:ogc:def:crs:EPSG::4326`. Wrong order returns
      `numberMatched=0` silently, with no error
- [x] Assert `numberMatched > 0` explicitly, so a silent-zero can never pass unnoticed
- [x] Pull `aa_verkehrszeichen` — verify against the known figure: **16,342 signs**
- [x] Pull `cm_fahrbahn` (roadway polygons) — needed for road ribbon meshes
- [x] Pull `be_fahrbahnmarkierunglinie` (lane markings)
- [x] Cache all responses to `data/raw/wfs/`

### 1c. LoD2 buildings

- [x] `pipeline/fetch_lod2.py`
- [x] **Fetch the sub-feed** `https://gdi.berlin.de/data/a_lod2/atom/0.atom`, not the top-level
      feed — the top level is a single entry pointing at it
- [x] Compute the tile set overlapping the 5km circle (expect **101 candidate, 98 present**)
- [x] Confirm the 3 missing tiles are the Havel/Wannsee water gap
      (`LoD2_378_5816`, `LoD2_379_5816`, `LoD2_379_5817`) — 30-second visual map check
- [x] Download all 98 to `data/raw/lod2/`, record total bytes on disk

### 1d. Gate — measure, then compare

- [x] Record the **real** building count (extrapolation said ~46,700) → **69,538, +49%**
- [x] Record the **real** processed-edge count after junction-splitting (said ~6,260) → **6,943**
- [x] Record the **real** junction count (said ~5,500) → **6,001**
- [x] Write the measured numbers into `docs/measured-counts.md` and correct README.md
- [x] Correct the vault research note too, so the extrapolation is not re-used elsewhere

> **Gate condition.** If measured counts land within roughly ±20% of the extrapolation, the
> architecture in README stands — carry on to Phase 2. If they come in materially higher
> (especially buildings), revisit payload size and chunking *before* writing renderer code, not
> after.

### Gate result — TRIPPED, resolved, proceeding

Every *measured* baseline reproduced exactly (642 signals, 16,342 signs, 101/98 tiles, same 3
missing tiles by name, same centre tile). Both misses were **extrapolations** — which is what the
gate was for.

1. **Buildings 69,538, not ~46,700 (+49%).** Payload is 56.3 MB on disk but only ~3.8 MB resident
   for a generous 25-chunk window (Phase 2 measured both), while ~69k draw calls
   would not hold 60fps, colliding with Path A's discrete-per-building addressability.
   **Resolution: per-chunk merged geometry with a per-building id vertex attribute** — carried
   into Phase 2 and Phase 5 below.
2. **"12,598 drivable ways" was `drivable + service`.** Drivable-only is **6,001**. Service ways
   are parking aisles and driveways the player must not drive — carried into Phase 3 below.
3. **1,572 MB uncompressed CityGML** → Phase 2 must parse streaming, not into memory.

4. **Edges and junctions held.** Measured in Phase 3: **6,943 edges** (said ~6,260, +10.9%) and
   **6,001 junctions** (said ~5,500, +9.1%) — both inside the ±20% band despite being scaled from
   the service-inflated figure. No architectural consequence. **Gate now fully closed.**

---

## Phase 2 — Geometry, Path A (CityGML 1.0 → intermediate → glTF)

**Settled 2026-09-07:** no `citygml-tools`, no CityJSON step. The archives are **CityGML 1.0**
(`http://www.opengis.net/citygml/1.0`), which that tool doesn't target, and it needs a Java
runtime not installed here. Parse the GML directly with **streaming `lxml.etree.iterparse`** —
which the 1,572 MB uncompressed volume requires regardless of tooling.

- [x] `pipeline/parse_citygml.py` — streaming `iterparse` over each tile's `.xml` inside its zip,
      clearing elements as it goes. **Never load a whole tile into memory**
- [x] Extract per building: stable id, `gml:Solid` / `gml:MultiSurface` polygons, ground height
- [x] **Note the archives hold `.xml`, not `.gml`** — filtering on `.gml` matches nothing and
      silently yields zero buildings
- [x] Cache a compact per-tile intermediate to `data/build/parsed/` so re-runs skip the 1.5 GB
      parse entirely
- [x] `pipeline/build_buildings.py` — intermediate → simplified meshes
- [x] Preserve **real roof geometry** — the whole reason Path A was chosen over extrusion
- [x] Give every building a **stable id** and keep it addressable
- [x] **Batch into per-chunk merged geometry carrying building id as a vertex attribute.** At
      69,538 buildings, one `Mesh` each means ~69k draw calls and no 60fps. Merging per chunk
      keeps addressability (shader-side alpha for fade/highlight; split out only the few buildings
      actually being faded) without paying per-building draw calls
- [x] Chunk spatially on a **500m grid** (369 chunks), *not* on the graph as originally written.
      The graph-predictive part is a runtime concern: given the current edge and the next candidate
      edges, the renderer computes which cells those polylines cross and prefetches them. Buildings
      have no natural graph membership, so a grid is the right container and the graph drives
      *which* cells to load
- [x] Reproject to a local metric frame centred on the anchor (UTM33 northings are ~5.8M; raw
      coordinates lose float precision at render time)
- [x] Export GLB into `data/build/buildings/` — written directly with `struct` rather than via
      `pygltflib`, since the output is one mesh with three accessors; `pygltflib` is still used to
      validate what was written
- [x] Record real payload size → **56.3 MB** across 369 chunks. The ~5.6MB estimate used a
      footprint-prism ratio; real LoD2 solids are ~850 bytes/building. Resident working set is what
      matters: 25 chunks = 3.8 MB / 203k triangles

**Checkpoint — PASSED 2026-09-07.** `tools/preview_chunk.html` loads 25 chunks (3,802 buildings,
203,438 triangles) through Three.js `GLTFLoader`. Gabled and hipped roofs show clear ridge lines at
close range while large commercial buildings are correctly flat — had the solids been flattened to
extrusion, every roof would be flat. Tinting by the `_BUILDING` attribute gives each building its
own colour inside a single merged mesh, confirming addressability survives the merge.

---

## Phase 3 — Routable road graph

- [x] `pipeline/build_graph.py` — raw OSM ways → routable graph
- [x] **Build the graph from the 6,001 drivable ways only.** Exclude `service`, `track` and
      `pedestrian` — the player must not be able to drive a parking aisle or a driveway
- [ ] Keep service ways alongside, flagged `drivable: false`, as rule context for the "leaving a
      driveway always yields" rule — **deferred to Phase 4**, which is where that rule is resolved;
      `graph.json` deliberately contains drivable edges only
- [x] Measure and record the real edge and junction counts — these close the last open item of the
      Phase 1 gate, and both extrapolations were scaled from the service-inflated way count
- [x] Resolve the Free Roam spawn to an explicit `(edge_id, offset, heading)` at the TÜV, validated
      as a real drivable edge
- [x] Split ways at junctions into edges; assign stable `edge_id` / `junction_id`
- [x] Respect one-way tags — the player must not be able to drive the wrong way
- [x] Compute per-edge bearings at each endpoint (needed by both the rule matcher and the
      left/right/straight picker)
- [x] For each junction, enumerate legs with their bearings
- [x] Classify each outgoing option from each incoming leg as left / straight / right
- [x] Sanity check: no orphan edges, no junction with a single leg, no disconnected component
      that traps the player
- [x] Emit `data/build/graph.json`

---

## Phase 4 — Rule-conflict engine

Pure Python, build-time only. Runs once and bakes results into per-junction data — no StVO logic
ever runs in the renderer.

- [ ] `pipeline/build_rules.py`
- [ ] Match each sign to its **approach leg by bearing** from the junction, not by radius alone
      (a sign 20m down the wrong street beats the right one on raw distance)
- [ ] Search radius ~30–50m from the junction, tuned against the two named test junctions below
- [ ] Match OSM signal nodes to legs the same way
- [ ] Resolve each `(junction_id, incoming_edge_id)` in **StVO precedence order**:
  1. [ ] Police officer / hand signal — not present in any source; structurally absent here
  2. [ ] Signal (Ampel) overrides fixed signage on the same leg
  3. [ ] Fixed priority signage (Vorfahrtstraße / Vorfahrt gewähren / Stop) overrides the default
  4. [ ] **Rechts vor links** when neither is present — the §8 default, not a fallback guess
  5. [ ] Zone 30 is a **speed regime, never a priority rule** — must not suppress rechts-vor-links
  6. [ ] Leaving a verkehrsberuhigter Bereich (Spielstraße) or a driveway/property always yields
- [ ] Load the `service` ways (already cached as `ways_extra_5km.json`) as non-drivable rule
      context, so a driveway/property exit can be detected at a junction
- [ ] Emit a resolved rule **and** a conflict flag per leg
- [ ] Where sources disagree (OSM signal with no matching WFS mast; signal + Vorfahrtstraße on one
      leg with no clear precedence) → **flag conflict, downgrade to hint-only, not scored**
- [ ] Emit `data/build/rules.json`, keyed `(junction_id, incoming_edge_id)`

### Validation against the two hand-verified junctions

- [ ] **Pichelswerderstraße → Freiheit** — the exam's own opening junction. Signposted:
      Vorfahrt-gewähren onto a Vorfahrtstraße, near a Bahnübergang. Exercises "fixed sign wins"
- [ ] **Tiefwerderweg / Schulenburgstraße** — 4-leg, unsignposted. Exercises the §8
      rechts-vor-links branch with no sign present
- [ ] Both must resolve correctly before the engine is trusted on the other ~5,500 junctions

---

## Phase 5 — Spike 1: Electron baseline  ← **GATE**

Prove the chosen stack clears the bar before building the real app on it. One shared dataset
(the real Phase 1–4 output at **full scale** — a single tile would pass on every stack and
discriminate nothing).

- [ ] Electron shell + Three.js scene + React side panel
- [ ] Full 5km payload resident: per-building meshes + `InstancedMesh` for the 16,342 signs
- [ ] Graph-predictive chunk load/unload keyed to the next candidate edges (not general spatial
      streaming — the rail-locked camera makes the next edges knowable)
- [ ] Rail-locked camera: position along current edge, heading = travel direction, reorients on
      the player's pick. **No orbit, no zoom, no fly-over**
- [ ] Direction picker + side panel + conflict display wired to the two named junctions only —
      full geometry, minimal game logic

### Numeric bar — same for all spikes, numbers not adjectives

| Measure | Threshold | Result |
|---|---|---|
| Sustained fps, full payload resident, driving-height camera | ≥ 60fps | |
| Side panel update on entering a flagged leg | ≤ 100ms perceived | |
| Cold start → first interactive frame (data local) | ≤ 5s | |
| Packaged binary size | recorded, not gated | |

**Estimated cost:** ~1 working day (6–8h). Every technology here is already in the existing
stack; the only new API surface is Three.js instancing/culling.

> **Gate — the stop rule. Run spikes in order, not all three regardless.**
> - Clears every threshold → **Phase 6.** Treat Phases 5a/5b as unnecessary, not as pending work.
> - Misses **fps** at full payload → run **Phase 5b (Godot)**; hand-built culling may genuinely be
>   insufficient. But check React re-render cost and memoization first — panel lag usually blames
>   the wrong layer.
> - Binary size or cold start turns out to matter in practice (e.g. distributing to another
>   machine) → run **Phase 5a (Tauri)**.

### Phase 5a — Tauri swap *(conditional — only if size/cold-start matters)*

- [ ] Same Three.js/React code, swap only the shell
- [ ] Re-measure the same four numbers
- [ ] Note any WebGL2 feature gaps vs Electron on the same machine — this is the actual question
      being tested, since Tauri uses the OS webview rather than a bundled Chromium

**Estimated cost:** ~2–3h incremental on Phase 5's code.

### Phase 5b — Godot from zero *(conditional — only if Phase 5 misses fps)*

- [ ] Godot 4 project, `MultiMeshInstance3D` from the same data (no glTF step)
- [ ] Control-node side panel wired to the same two junctions
- [ ] Same four measurements

**Estimated cost:** ~3–5 days (24–40h) — GDScript, the 3D API and Control nodes are all new. A
performance win costing 4–5x the hours needs the web stack to have actually *failed* first, not
merely scored lower.

---

## Phase 6 — Build the app

- [ ] Promote the spike into a real project structure under `app/`
- [ ] `app/main/` — Electron main process, window, packaging
- [ ] `app/renderer/world/` — Three.js scene: buildings, instanced signs, ribbon-mesh roads built
      from `cm_fahrbahn` polygons, graph-predictive chunking
- [ ] `app/renderer/ui/` — React: direction picker, side panel, HUD, conflict display
- [ ] Rule lookup on entering an edge: read `(junction_id, incoming_edge_id)` from `rules.json`.
      **Lookup only — no rule computation at runtime**
- [ ] Render hint-only legs visibly differently from scored ones — the distinction is a core
      correctness promise of the app, not a UI detail
- [ ] Sign faces from Wikimedia Commons SVGs, instanced
- [ ] Smooth heading transition through a turn (the one piece of camera motion that exists)

**Optional polish, explicitly not required:**

- [ ] Per-building raycast-and-fade when a corner building blocks the sightline to a sign.
      Cheap because Path A gives discrete objects — but the side panel already states the rule,
      so this is atmosphere and gates nothing

---

## Phase 7 — Modes  *(depends on an open decision — see below)*

- [ ] **Free Roam** — full 5km network, no timer, no fail state, panel informs on every street
      entered. This is where the outer ring's study value lives
  - [ ] **Fixed spawn at the TÜV Spandau building** (decided 2026-09-07). Every session starts at
        the anchor `52.5304357, 13.2144591`, on the edge leaving Pichelswerderstraße 9, heading
        toward the exam's own opening junction (Pichelswerderstraße → Freiheit). Not random, not
        last-visited — the real exam's first approach gets rehearsed every single session
  - [ ] Pipeline consequence: `build_graph.py` must resolve and store the spawn as an explicit
        `(edge_id, offset, heading)` anchored to the TÜV, validated as a real drivable edge —
        not a bare lat/lon the renderer has to snap at runtime
- [ ] **Exam Simulation** — same world, constrained to a 2–4km loop, 25-minute budget, scoring by
      schwere/leichte Fehler
- [ ] Both share one world and one dataset

---

## Open decisions — Ali's, not settled here

1. **Free Roam vs Exam Simulation vs both.** The research recommends building both on a shared
   world, but this has not been decided. Phase 7 is blocked on it.
   Context: a real 25–30 minute exam route only reaches a **2–4km** loop, so the outer ring
   (4–5km) is real street knowledge that no single exam route will ever touch.
2. **Verbatim § citations.** The StVO precedence order in Phase 4 is standard structure, but the
   vault's `fahrpruefung-fehlerkatalog` holds only secondary summaries — TÜV's official page
   returned HTTP 403. **Verify exact § citations against actual StVO/FeV text before printing
   them in-app as legal fact.** Until then, keep them indicative, not verbatim.

---

## Sequencing summary

```
Phase 0  skeleton ......................... done, bar the venv
Phase 1  fetch + MEASURE .................. GATE: replaces extrapolated numbers
Phase 2  geometry, Path A ................. checkpoint: roofs must look real
Phase 3  road graph
Phase 4  rule engine ...................... validate on 2 known junctions
Phase 5  Electron spike ................... GATE: numeric bar, then stop rule
         ├── 5a Tauri  (only if size/cold-start matters)
         └── 5b Godot  (only if fps missed)
Phase 6  build the app
Phase 7  modes ............................ blocked on open decision #1
```

Phases 1–4 are pure Python and carry over **unchanged** whichever shell wins in Phase 5. That is
deliberate: the stack question was only ever a renderer/UI question, never a pipeline one.
