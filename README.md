# MyTestDrive

An interactive driving-prep desktop app for the Berlin practical driving exam. It reconstructs
every street within a **5km radius** of the TÜV Rheinland Prüfstelle in Spandau as a real 3D
world, and drives it street by street: at every junction you pick left, right or straight, and a
side panel states what must be respected entering that street.

Not a racing game and not a map viewer. The point is learning the actual exam area — real
streets, real signs, real right-of-way — by driving it.

## Status — 2026-09-07

**The data pipeline is complete and verified. The app itself is not built yet.**

| Phase | State |
|---|---|
| 0 — Repository skeleton | done |
| 1 — Fetch + measure (**gate**) | done, gate tripped on building count and resolved |
| 2 — Building geometry (Path A) | done, roofs visually verified |
| 3 — Routable road graph | done |
| 3b — Road elevation | done, low-confidence areas flagged |
| 4 — Rule engine | done, both acceptance junctions validate |
| Road surfaces + markings | done (Phase 6 work pulled forward) |
| 5 — Electron spike (**gate**) | **not started** — next |
| 6 — The app | not started |
| 7 — Free Roam / Exam modes | blocked on an open decision |

Everything the world needs is built: 69,538 buildings with real roofs, a 6,943-edge routable
graph with elevation, surveyed road surfaces and lane markings, and right-of-way resolved for
all 11,844 approaches. What does not exist yet is the Electron app that drives through it.

Full research and reasoning lives outside this repo, in the Brain vault:
`4-Resources/mytestdrive-5km-desktop-research.md`. Detailed measurements and every reconciliation:
[docs/measured-counts.md](docs/measured-counts.md). Phase checklist: [PLAN.md](PLAN.md).

## Running the pipeline

```bash
python3 -m venv .venv
./.venv/bin/pip install -r pipeline/requirements.txt
cd pipeline
```

Stages, in dependency order. Every fetch stage caches to `data/raw/`, so re-runs cost nothing;
pass `--force` to refetch.

| # | Command | Does | Time |
|---|---|---|---|
| 1 | `python fetch_osm.py` | Overpass road graph, signals, hazards | ~2 min |
| 2 | `python fetch_strassenbefahrung.py` | WFS signs, roadway polygons, lane markings | ~3 min |
| 3 | `python fetch_lod2.py` | 98 LoD2 building tiles (182 MB) | ~5 min |
| 4 | `python parse_citygml.py` | Stream-parse 1.57 GB CityGML → intermediate | 100 s |
| 5 | `python build_buildings.py` | Per-chunk merged building meshes → glTF | 199 s |
| 6 | `python build_graph.py` | Ways → routable graph, turns, spawn | ~30 s |
| 7 | `python build_elevation.py` | Adds z to the graph from LoD2 ground | ~60 s |
| 8 | `python build_roads.py` | Road surfaces + lane markings → glTF | 20 s |
| 9 | `python build_rules.py` | Right of way per (junction, approach) | ~40 s |

**Order matters in two places.** `build_elevation.py` rewrites `graph.json` in place and refuses
to run twice; re-running `build_graph.py` resets the graph to 2D, so elevation must be re-run
after it. `build_roads.py` and `build_elevation.py` both need `parse_citygml.py` output for
ground heights.

### Looking at the result

```bash
python3 -m http.server 8731        # from the repo root
# then open http://localhost:8731/tools/preview_chunk.html
```

`tools/preview_chunk.html` loads buildings, road surfaces and markings for a 7×7 chunk window and
offers a driver's-eye camera, an elevated view, and a toggle for the graph wireframe. It is a
verification tool, not part of the app.

## Core design decisions (settled — do not re-litigate)

| Decision | Choice |
|---|---|
| Desktop shell | **Electron** — not Tauri. Ships its own Chromium, so WebGL2 behaves identically on every OS |
| 3D renderer | **Three.js** |
| UI layer | **React** — the side panel/HUD is the hard part, and this is the existing skill stack |
| Geometry source | **Path A** — real Berlin LoD2 building solids, not footprint+height extrusion |
| Pipeline language | **Python**, independent of the renderer/UI choice |
| Camera | **Rail-locked to the road graph.** No orbit, no zoom, no fly-over, no skipping a street |
| Rule resolution | Precomputed at build time per `(junction, approach)`, never live in the renderer |
| Free Roam spawn | Always the TÜV building, facing the exam's own opening junction |

### Why Path A (real LoD2 solids)

Berlin's LoD2 data already contains full 3D building solids with real roof shapes — it is not a
flat map to be extruded. That buys two things: junctions look like *that* junction (real corner
silhouettes, which is what makes the area learnable), and every building stays a **discrete,
addressable object** rather than one fused photogrammetry mesh.

Verified rather than assumed: gabled and hipped roofs render with real ridge lines while large
commercial buildings are correctly flat. Had the pipeline degraded to extrusion, every roof would
be flat.

### Why the camera is rail-locked

The experience is street-to-street driving. Position is a point along the current graph edge,
heading is the direction of travel, and the player's only camera input is the left/right/straight
pick at each junction. You reach a street by driving the streets that lead to it.

Three consequences, all helpful: buildings sit beside the view rather than in front of it (so
occlusion is optional polish, not a core system); chunk load/unload can be **predictive along the
graph**; and no physics engine is needed at all, since movement is a parametric position along an
edge.

## Measured figures

Every number here comes from running the pipeline. Reconciliations and method:
[docs/measured-counts.md](docs/measured-counts.md).

### Road network

| Metric | Value |
|---|---|
| **Drivable ways** (the traversable network) | **6,001** |
| `service` ways (parking aisles, driveways — *not* drivable) | 6,590 |
| **Graph edges** after junction-splitting | **6,943** |
| **Junctions** | **6,001** |
| One-way edges | 2,037 |
| Dead ends | 1,556 |
| Total drivable length | 472.1 km |
| Distinct named streets | 838 |
| Reachable from spawn (directed) | 98.5% |
| Ambiguous turn sets | 78 |
| Spawn | `e6261`, Pichelswerderstraße, 82.9m from the anchor, 32.0 m ASL |

### Buildings

| Metric | Value |
|---|---|
| LoD2 tiles | **98 present** of 101 candidate |
| Raw download / uncompressed | 182 MB / 1,572 MB |
| **Buildings** | **69,538** (0 lost in parsing) |
| Source polygons | 1,095,288 |
| Triangles / vertices | 2,833,272 / 1,546,028 |
| Chunks (500m grid) | 369 |
| **glTF on disk** | **56.3 MB** — but only ~3.8 MB resident for a 25-chunk window |

### Roads, signs and rules

| Metric | Value |
|---|---|
| Roadway surface polygons | 4,927 → 771,740 triangles, 21.0 MB |
| Lane markings | 19,530 → 88,374 segments, 2.1 MB |
| Traffic signs | **16,342** in bbox / 13,253 in circle |
| Traffic signal nodes | **642** (628 after excluding pedestrian crossings) |
| Signal masts (WFS) | 1,793 bbox / 1,508 circle |
| **Resolved (junction, approach) pairs** | **11,844** |
| Rechts vor links | 10,063 (85.0%) |
| Priority road | 599 (5.1%) |
| Signal | 472 (4.0%) |
| Signposted yield | 397 (3.4%) |
| Yield inferred from a crossing priority road | 211 (1.8%) |
| Calmed-area exit / Stop | 74 / 33 |
| **Flagged hint-only, not scored** | **872 (7.4%)** |

### Elevation

Neither OSM nor the WFS roadway layer carries elevation. Height is interpolated by IDW over
**430,929 ground samples** taken from the LoD2 `GroundSurface` polygons — no extra download.
Median grade is **0.49%**, which is right for a flat city. Bridges run straight between abutments
rather than dipping to the water beneath.

Where buildings are sparse the estimate weakens, so it is flagged rather than hidden:
`z_confident: false` on 265 junctions (4.4%) and `grade_suspect: true` on 62 edges (0.9%).

### Corrections to earlier figures

- **"12,598 drivable ways" was `drivable + service`** (reproduced exactly at 12,591). Service ways
  are parking aisles and driveways — not streets an exam is driven on. The real network is 6,001.
- **Buildings are 69,538, not the extrapolated ~46,700** (+49%). The 3.7–3.8x way/sign scale factor
  does **not** transfer to buildings.
- **Payload is 56.3 MB, not ~5.6 MB.** The old ratio came from footprint prisms (Path B); real
  solids run ~850 bytes/building. Irrelevant in practice — it is a disk figure.
- Edge and junction extrapolations (~6,260 / ~5,500) both held within 11%.

A full 5km radius stays **inside Berlin** on all 8 compass points, so LoD2 and the sign survey
cover the whole area with no degraded edges. The 3 missing LoD2 tiles all sit over the
Havel/Wannsee waterway — "no buildings on open water", not a coverage failure.

## Data sources

All free, no API keys, no paid services.

1. **Berlin Straßenbefahrung 2014/15 WFS** — `https://gdi.berlin.de/services/wfs/strassenbefahrung`
   (dl-de/zero-2.0). Used: `aa_verkehrszeichen` (signs), `at_mast_lsa` (signal masts),
   `cm_fahrbahn` (roadway polygons), `be_fahrbahnmarkierunglinie` (lane markings).
   Available, not fetched: `cl_gehweg` (62,966 footway polygons), `bd_bordstein` (43,021 kerbs).
2. **OpenStreetMap via Overpass** — the road graph, signals, hazards.
3. **Berlin LoD2 3D buildings** — ATOM feed at `https://gdi.berlin.de/data/a_lod2/atom/`.
4. **StVO sign faces** — Wikimedia Commons SVGs, public domain (§5 UrhG, amtliche Werke).
   Not yet fetched.

## Gotchas that will cost you hours

Every one of these was hit for real, and every one fails **silently**.

**WFS axis order.** A WFS 2.0 bbox using `urn:ogc:def:crs:EPSG::4326` wants **lat,lon**, not
lon,lat. Wrong order returns `numberMatched=0` with HTTP 200 and no error. Verified both ways:
lat,lon → 16,342 signs, lon,lat → 0.

**The LoD2 ATOM feed is two levels deep.** The top-level feed contains a single entry pointing at
`https://gdi.berlin.de/data/a_lod2/atom/0.atom`, which is what lists all 925 tiles.

**Overpass returns HTTP 406 to the default `python-requests` user agent.** Any custom
`User-Agent` fixes it. Separate from rate-limiting and looks nothing like it.

**Overpass rate-limiting.** Try both endpoints in either order — on 2026-09-07 the
`overpass.kumi.systems` *mirror* was returning 429 while the main endpoint worked.

**The LoD2 archives contain `.xml` files, not `.gml`.** Filtering on `.gml` matches nothing and
yields zero buildings with no error.

**The LoD2 data is CityGML 1.0**, not 2.0. `citygml-tools` targets 2.0/3.0 and needs a Java
runtime that isn't installed here, so the pipeline parses the GML directly with streaming
`lxml.etree.iterparse` — which 1.57 GB requires anyway.

**1 in 5 buildings keeps its geometry in `bldg:consistsOfBuildingPart`.** Reading `bldg:boundedBy`
as direct children only drops them all — the first full parse returned 55,574 of 69,538 with no
error. Descend for surface elements, and always assert the parsed count against the raw
`<bldg:Building` count.

**Three.js `GLTFLoader` lowercases custom vertex attributes.** `_BUILDING` arrives as `_building`.
Reading the original name gives `undefined` and throws *inside the load callback*, which surfaces
as no console error, no failed request — just a loader that never completes.

**A 0×0 canvas renders black with no error.** `innerWidth`/`innerHeight` can be 0 when a module
script first runs, and `renderer.setSize(0, 0)` fails silently.

**A mesh with no `NORMAL` attribute renders pure black under Lambert lighting** unless the
material sets `flatShading: true`, which derives normals in the shader.

**Lane markings ~4cm above the road vanish into it** at distance. Use `polygonOffset` on the road
material rather than lifting the paint, which looks wrong close up. `LineBasicMaterial` also
ignores `linewidth` in WebGL, so markings can never be more than a hairline as lines — they need
to become textured quads.

### Rule-engine specifics

**Rules resolve per approach, not per junction.** "Junction X is rechts-vor-links" breaks wherever
a Vorfahrtstraße crosses a residential street — two approaches signposted, two not, at the same
junction. Key on `(junction_id, edge_id)`.

**Do not match signs by bearing from the junction centre.** This was the original design and it is
wrong: median angular error to the correct leg is **48°**, because a sign stands at the corner
where its lateral offset dominates the bearing. Use **perpendicular distance to the road's own
polyline**, plus the surveyed `strasse` name (83% match rate). This cut unassigned priority signs
from 900 to 253.

**Do not match signals by proximity.** Only 53 of 642 signal nodes are junction nodes; **515 are
stop lines partway along an edge**. Match by OSM node id and use `traffic_signals:direction` to
place them on the correct approach. Exclude `crossing=traffic_signals` — pedestrian signals do not
govern junction right of way.

**A Vorfahrtstraße designates the road, not one approach.** Resolving per-sign left 322 approaches
wrong *in the unsafe direction* — telling the driver they had priority from the right where a
priority road crosses. Priority must propagate across the junction.

**One real junction can be several graph nodes.** Tiefwerderweg × Schulenburgstraße is 5 nodes (a
one-way pair layout). The app must cluster these or the player is asked for several direction
choices while crossing one visible intersection.

## Layout

```
pipeline/             Python. Fetch + build. Independent of the renderer/UI stack
  config.py           Anchor, radius, CRS, endpoints, highway filters
  overpass.py, wfs.py Cached HTTP clients
  fetch_*.py          Stages 1-3
  parse_citygml.py    Streaming CityGML 1.0 parser
  build_*.py          Stages 5-9
app/main/             Electron main process — not written yet
app/renderer/world/   Three.js scene — not written yet
app/renderer/ui/      React side panel, direction picker, HUD — not written yet
tools/                preview_chunk.html — verification viewer, not part of the app
data/raw/             Downloaded source data — gitignored
data/build/           Generated artifacts — gitignored, reproducible
docs/                 measured-counts.md — every measurement and reconciliation
```

`data/` is deliberately not committed: raw CityGML runs to 182 MB zipped, and everything in
`data/build/` is reproducible by re-running the pipeline.

## Standing rules

- **Only data-confirmed situations are scored.** Anything inferred is a hint, never a verdict.
  Rechts-vor-links is scoreable precisely because the Straßenbefahrung survey is *complete* — "no
  priority sign in the data" means "there is no sign", not "we don't know".
- **Where sources conflict, downgrade to hint-only.** A resolved rule is confirmed; a conflicting
  one is not, even though both have data behind them. Currently 7.4% of approaches.
- **Flag, don't smooth.** The same applies to elevation: low-confidence heights are marked, not
  quietly averaged away.
- **€0 in licences and services.** Every dependency and data source here is free.

## Open decisions — not settled here

1. **Free Roam vs Exam Simulation vs both.** Phase 7 is blocked on this. A real 25–30 minute exam
   route only reaches a 2–4km loop, so the outer ring is street knowledge no single exam will touch.
2. **Verbatim § citations.** The StVO precedence order used is standard structure, but the vault's
   own source holds only secondary summaries. Verify exact § citations against the actual StVO/FeV
   text before printing them in-app as legal fact.
3. **Berlin DGM terrain model** for the 0.9% of edges with suspect grades. Not at the obvious GDI
   endpoints (all 404), so locating it is a small research task, deferred.

## Repository

Local-first, by design. There is no remote yet — the remote will be created *from* this folder
when the time comes, not the other way around.
