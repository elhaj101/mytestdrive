# Measured counts — Phase 1 gate

Real numbers from actual pipeline runs, replacing the extrapolated figures the research note
carried. Anchor `52.5304357, 13.2144591`, radius 5000m. All measured 2026-09-07.

**Gate result: TRIPPED on building count.** See "Gate assessment" below.

---

## Summary against the note's baselines

| Metric | Note said | Measured | Verdict |
|---|---|---|---|
| Traffic signal nodes (OSM) | 642 | **642** | exact |
| Traffic signs (WFS, bbox) | 16,342 | **16,342** | exact |
| LoD2 candidate tiles | 101 | **101** | exact |
| LoD2 tiles present | 98 | **98** | exact |
| Missing tiles | 3, named | **same 3, same names** | exact |
| Centre tile | `LoD2_378_5821` | **`LoD2_378_5821`** | exact |
| Raw drivable ways | 12,598 | 12,591 as `drivable+service` | reconciled, see below |
| **Buildings** | **~46,700** *(extrapolated)* | **69,538** | **+48.9% — gate tripped** |
| Graph edges | ~6,260 *(extrapolated)* | **6,943** | +10.9%, within band |
| Junctions | ~5,500 *(extrapolated)* | **6,001** | +9.1%, within band |

Every *measured* baseline reproduced exactly. Both figures that missed were **extrapolations**,
which is precisely what the gate existed to catch.

---

## OSM road network

| Highway classes | Ways |
|---|---|
| `residential` | 3,205 |
| `secondary` | 1,258 |
| `tertiary` | 915 |
| `unclassified` | 223 |
| `living_street` | 189 |
| `primary` | 147 |
| `motorway` | 18 |
| links (`secondary_link` 27, `motorway_link` 18, `tertiary_link` 1) | 46 |
| **Drivable subtotal** | **6,001** |
| `service` (parking aisles, driveways, alleys) | 6,590 |
| `track` | 433 |
| `pedestrian` | 76 |

| Nodes | Count |
|---|---|
| Traffic signals | **642** (exact baseline match) |
| Level crossings | 68 |
| Give way | 66 |
| Stop | 26 |

### Reconciling the note's "12,598 ways"

Signals matching exactly at 642 confirms the anchor and radius, so the way-count gap is a
filter-definition difference, not a geometry error.

| Filter | Count | vs 12,598 |
|---|---|---|
| drivable only | 6,001 | −6,597 |
| **drivable + `service`** | **12,591** | **−7** |
| drivable + service + track | 13,024 | +426 |

**The note's 12,598 was `drivable + service`.** The 7-way gap is a few days of OSM edits.

**Consequence — the headline figure overstates the drivable network by ~2x.** `service` ways are
parking aisles, driveways and alleys. They are not streets a driving exam is conducted on and the
player must not drive them. The drivable network is **6,001**, not 12,598.

Keep service ways in the data but flagged `drivable: false`, excluded from the traversable graph.
The standing rule "leaving a property or driveway always yields" needs to know a driveway is
there, which is the only reason they matter.

---

## WFS Straßenbefahrung

| Layer | In bbox | In 5km circle |
|---|---|---|
| `aa_verkehrszeichen` (signs) | **16,342** | 13,253 |
| `at_mast_lsa` (signal masts) | 1,793 | 1,508 |
| `cm_fahrbahn` (roadway polygons) | 4,927 | 3,935 |
| `be_fahrbahnmarkierunglinie` (lane markings) | 19,530 | 14,977 |

The note's 16,342 was measured on the **bbox**. A bbox around a 5km circle holds ~27% more area,
so the circle figure being lower is correct, not a bug. Both are recorded so neither gets
mistaken for the other later.

`at_mast_lsa` was not in the original plan but is worth having: 1,508 signal masts in-circle
cross-check against OSM's 642 signal nodes, which is exactly the "OSM signal with no matching WFS
mast" conflict case the rule engine has to detect. (The counts differ by design — a junction has
several masts but one OSM node.)

### Axis-order trap — reproduced exactly

| Bbox form | numberMatched |
|---|---|
| `lat,lon` + `urn:ogc:def:crs:EPSG::4326` | **16,342** |
| `lon,lat` + same CRS | **0**, HTTP 200, no error |
| native `EPSG::25833` easting,northing | 15,833 |

Confirmed: wrong axis order returns a silent zero, not an error. The service's native CRS is
**25833**, not 4326.

---

## LoD2 buildings

- Sub-feed lists **925** tiles (as documented — the top-level feed only points at `0.atom`)
- Candidate tiles overlapping the circle: **101**
- Present in feed: **98**
- Missing: `LoD2_378_5816`, `LoD2_379_5816`, `LoD2_379_5817` — all south/south-east over the
  Havel/Wannsee. Confirms the "no buildings on open water" reading, not a coverage failure
- Centre tile `LoD2_378_5821` — matches
- **Raw download: 181.8 MB zipped**
- **Uncompressed CityGML: 1,572 MB**
- **Buildings: 69,538**

Densest tiles: `376_5819` (2,328), `375_5821` (2,300), `374_5822` (2,265).
Emptiest: `375_5818` (1), `380_5816` (1), `374_5818` (2) — the water-adjacent edge.

### Boundary note

A strict circle test gives 100 candidates / 97 present. `LoD2_381_5816` misses by **6 metres**.
The pipeline applies a 50m buffer so boundary buildings are not clipped, which yields 101/98 and
matches the recorded figures.

---

## Phase 2 — geometry built and visually verified

| Metric | Value |
|---|---|
| Buildings parsed from CityGML | **69,538** (0 skipped — exact match to the raw count) |
| Source polygons | 1,095,288 (wall 801,310 / roof 189,981 / ground 103,997) |
| Polygons with holes (courtyards) | 782 |
| Triangles after triangulation | **2,833,272** |
| Vertices after per-chunk dedup | 1,546,028 (from 5,022,438 ring points, 3.2x) |
| Chunks (500m grid) | **369** |
| **Total glTF on disk** | **56.3 MB** |
| Mean per chunk | 188 buildings, 156 KB, 7,678 triangles |
| Largest chunk | 591 KB, 414 buildings, 29,655 triangles |
| Parse time / build time | 100s / 199s (peak RSS 833 MB) |

### Payload estimate corrected: 5.6 MB → 56.3 MB

The note's ~3.9MB, and my own revised ~5.6MB, both derived from the old build's
**~84 bytes/building** ratio. That ratio came from *footprint+height prisms* — Path B geometry.
Real LoD2 solids average 15.8 polygons per building, so the true figure is **~850 bytes/building**,
10x higher.

**This changes nothing that matters.** What the renderer holds at once is a handful of chunks, not
the whole city:

- 25 chunks (a 2.5km × 2.5km window, far more than street-level view distance needs) =
  **3.8 MB and 203,438 triangles**
- A realistic street-level working set of 9 chunks is well under 2 MB

56.3 MB is a disk figure, not a memory figure. Both are trivial for a desktop app.

### Visual verification — Path A confirmed

Rendered through Three.js `GLTFLoader` in `tools/preview_chunk.html` (25 chunks, 3,802 buildings):

- **Roofs are real roof shapes.** Gabled and hipped roofs show clear ridge lines at close range;
  large commercial buildings are correctly flat. Roof type codes in the source confirm it —
  2100 (pent), 3100 (gable), 5000 (hipped), 1000 (flat), with up to 19 roof polygons and 4.7m of
  vertical spread on one building. **If Path A had silently degraded to extrusion, every roof
  would be flat. They are not.**
- **Per-building addressability survives the merge.** Tinting by the `_BUILDING` vertex attribute
  gives every building its own colour inside a single merged chunk mesh — which is exactly the
  property the occlusion-fade and landmark-cue features depend on, at one draw call per chunk
  instead of 69,538.

### Two failures worth remembering

**CityGML `BuildingPart` nesting cost 20% of the buildings, silently.** Taking
`bldg:boundedBy` as *direct children* of `bldg:Building` misses any building whose geometry lives
in `bldg:consistsOfBuildingPart` — 10,459 BuildingParts across the first 20 tiles alone. The first
full parse returned 55,574 of 69,538 with no error. Fix: descend for surface elements rather than
taking direct children. The parser now also counts and reports buildings yielding no geometry, so
a shortfall can never be silent again.

**`GLTFLoader` lowercases custom vertex attributes.** `_BUILDING` in the file arrives as
`_building` on the geometry. Reading the original name returns `undefined` and throws *inside the
load callback*, where it surfaces as nothing at all — no console error, no failed request, just a
loader that never completes.

---

## Phase 3b — road elevation

Neither OSM nor the WFS roadway polygons carry elevation (verified: every `cm_fahrbahn`
coordinate sampled had exactly two components). The LoD2 `GroundSurface` polygons do, and they
were already parsed, so elevation needed no new download.

| Metric | Value |
|---|---|
| Ground samples (deduped to a 2m grid) | **430,929** |
| Sample z range | 28.1 – 112.9 m ASL |
| Road z range after interpolation | 28.6 – 87.5 m ASL |
| Spawn elevation | **32.0 m ASL** (TÜV sits near the Havel — plausible) |
| Median grade | **0.49%** (Berlin is flat — as expected) |
| Bridge edges / median grade | 69 / 1.24% |

**Method: inverse-distance weighting over the 8 nearest ground samples.** A median estimator was
tried and is *worse* on the only metric that matters — implausible grades: IDW 34, median-of-8 67,
median-of-16 58. IDW kept.

**Continuity is automatic.** Height is a pure function of (x, y), so two edges meeting at a
junction always agree. Interior vertices get mild smoothing; endpoints are left untouched
precisely to preserve that.

**Bridges are run straight between their abutments** rather than following the ground beneath.
Interpolating ground under a Havel crossing would dip the road to water level.

### Quality: good where buildings are dense, weak in open ground

| Nearest ground sample per junction | Distance |
|---|---|
| p50 | 14.9 m |
| p90 | 33.8 m |
| p99 | 93.5 m |
| max | 1,346.6 m |

Local agreement is the better quality signal: the height spread among the 8 neighbours used is
**0.4 m at p50** and 1.8 m at p90. Only 0.3% of junctions exceed 10 m.

**34 edges come out steeper than 15%, and they are artefacts, not hills.** 22 of them are under
20 m long (median 16.5 m against 39.8 m for all edges), so a small height difference becomes a
large percentage. The worst case — Heerstraße, 84.6% over 29 m — is not estimator noise: both
endpoints have *confident* estimates (all 8 neighbours agreeing within 0.1 m) but their nearest
buildings are 50–70 m away on opposite sides of a genuine terrain step. The road between them has
no buildings near it to sample.

### Low-confidence data is flagged, not silently smoothed

Following the project's standing rule that inference is a hint and never a verdict:

- `junction.z_confident = false` where the nearest sample is >50 m away or the neighbour spread
  exceeds 5 m → **265 junctions (4.4%)**
- `edge.grade_suspect = true` where |grade| > 12% → **62 edges (0.9%)**

Phase 5/6 can smooth, clamp or ignore those rather than trusting them blindly.

**The proper fix, if it ever matters, is Berlin's DGM terrain model.** It is not at the obvious
GDI endpoints (`/services/wfs/dgm`, `/services/wcs/dgm`, `/data/a_dgm/atom/0.atom` all 404), so
finding it is a small research task deferred until the flagged 0.9% actually proves annoying in
the app.

### Verified visually

The preview renders the road graph at its own interpolated height with **no ground plane** — a
hardcoded one would hide exactly this bug. Roads sit at building-base level and thread correctly
between buildings; the city does not float.

---

## Road surfaces and markings (Phase 6 work brought forward)

Built to answer a direct question: *what will the roads actually look like?* The green wireframe
in earlier previews is the graph centreline — it drives movement and is **never rendered in the
app**. The visible road is the surveyed surface.

| Metric | Value |
|---|---|
| Roadway polygons (`cm_fahrbahn`) | 4,927 → **771,740 triangles** in 342 chunks, **21.0 MB** |
| Untriangulable polygons | **0** |
| Lane markings (`be_fahrbahnmarkierunglinie`) | 19,530 → **88,374 line segments**, 281 chunks, 2.1 MB |
| Surface materials present | codes 01, 02, 04, 05, 06, 14 — asphalt dominates, rest are setts/pavers |
| Build time | 20s |

These are real surveyed surfaces, not centrelines widened by a guess: true width, junction shapes,
flared corners and separately-surfaced strips (cycle lanes, tram beds) all come through as
distinct material codes.

Both layers are 2D in the source and are draped onto the same LoD2-derived height surface the
road graph uses, so roads, buildings and markings stay mutually consistent.

**Two rendering traps found here, both silent:**

- **A mesh with no `NORMAL` attribute renders pure black under Lambert lighting.** The building
  meshes got away with it because their material sets `flatShading: true`, which derives normals
  in the shader. The road material did not, so the roads rendered as a black, road-shaped hole
  with no error. Either set `flatShading` or write normals.
- **Markings sit ~4cm above the surface, which the depth buffer cannot resolve at distance**, so
  they vanish under the road. The fix is `polygonOffset` on the *road* material rather than
  lifting the paint off the road, which would look wrong close up. For production, markings should
  become textured quads (decals) rather than 1px lines — `LineBasicMaterial` ignores `linewidth`
  in WebGL, so lines can never be more than a hairline.

**Still available, deliberately not fetched** (cosmetic and large): `cl_gehweg` — 62,966 pavement
polygons — and `bd_bordstein` — 43,021 kerb segments. These add footways and kerb edges when
wanted.

---

## Gate assessment

### Buildings: +48.9% over extrapolation

69,538 measured against ~46,700 extrapolated. The 3.7–3.8x way/sign scale factor does **not**
carry over to buildings — reasonable in hindsight, since the outer ring loses road density faster
than it loses housing density.

**Payload impact: still a non-issue.** The old build simplified 12,402 buildings to ~1MB
(~84 bytes/building). At the same ratio 69,538 buildings is **~5.6MB** — above the note's
3.3–3.9MB estimate, still trivial for a desktop app. The note's conclusion that tile-streaming
isn't needed *for memory reasons* survives.

**Draw-call impact: this is the real finding.** The design commits to buildings as *discrete,
addressable objects* (Path A's advantage, and what the optional occlusion-fade depends on). At
69,538, one `Mesh` per building means ~69k draw calls, which will not hold 60fps. These two
requirements now collide, and the resolution has to be chosen before Phase 5, not during it:

- Batch buildings into **per-chunk merged geometry**, preserving per-building identity as a
  vertex attribute (building id), so a specific building is still addressable for fade/highlight
  via shader-side alpha or by splitting only the handful of buildings actually being faded.
- This keeps Path A's per-building addressability without paying per-building draw calls, and it
  fits the graph-predictive chunking already agreed.

**Processing impact:** 1.57 GB of uncompressed CityGML means Phase 2 must parse **streaming**
(iterative, per-tile, releasing as it goes), not load-all-into-memory.

### Edges and junctions: measured, and the extrapolation held

Despite being scaled from the service-inflated way count, both landed inside the ±20% band:
**6,943 edges** (said ~6,260, +10.9%) and **6,001 junctions** (said ~5,500, +9.1%). No
architectural consequence.

| Graph metric | Value |
|---|---|
| Edges after junction-splitting | 6,943 |
| Junctions | 6,001 |
| One-way edges | 2,037 |
| Dead ends | 1,556 |
| Total drivable length | 472.1 km |
| Distinct named streets | 838 |
| Reachable from spawn (directed) | 6,836 — **98.5%** |
| Largest undirected component | 5,912 nodes |
| Ambiguous turn sets | 78 |

**Why only 942 splits across 6,001 ways.** OSM already splits ways at intersections, so only
**49** nodes are interior to every way using them (true X-crossings where neither way terminates).
The splits come from the 696 ways carrying a T-junction interior node — a node that is an endpoint
of one way and interior to another. The graph is correct; the low split count is an OSM data
convention, not a bug. (The junction count landing on exactly 6,001, equal to the way count, is a
coincidence — verified, not an off-by-one.)

**Dead ends are genuine.** 1,556 (26% of junctions) sounds high, but they distribute evenly across
distance bands rather than clustering at the 5km boundary, so they are real cul-de-sacs plus
streets whose only continuation is an excluded `service` road. Correct behaviour for this app —
the player should not be able to drive on into a parking aisle. 9 zero-leg junctions remain as a
minor anomaly worth a look during Phase 4, not a blocker.

**Spawn resolved.** Nearest drivable edge to the TÜV anchor is `e6261` on **Pichelswerderstraße**
(`tertiary`), 82.9m from the anchor point, offset 1.26m along the edge, heading −117.0°. That is
the correct street by name, which independently confirms the anchor.

**Turn ambiguity — a real UI finding.** At 78 junctions, two or more options fall into the *same*
left/right/straight bucket (e.g. two roads both bearing left). A three-button picker cannot express
those. Phase 6 needs a disambiguation affordance for this small set — street name on the option, or
a finer angular fan — rather than assuming three buttons always suffice.

### Verdict

Nothing here invalidates the stack decision or Path A. Two concrete changes land in the plan:
per-chunk batched geometry with per-building ids (Phase 2/5), and streaming CityGML parsing
(Phase 2). Proceed.
