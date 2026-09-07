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
| Graph edges | ~6,260 *(extrapolated)* | not yet measured | Phase 3 |
| Junctions | ~5,500 *(extrapolated)* | not yet measured | Phase 3 |

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

### Edges and junctions: not yet measured, but suspect

~6,260 edges / ~5,500 junctions were scaled from the **service-inflated** way count. Since the
drivable network is 6,001 ways rather than 12,591, both are likely substantially too high for the
traversable graph. Phase 3 measures them directly.

### Verdict

Nothing here invalidates the stack decision or Path A. Two concrete changes land in the plan:
per-chunk batched geometry with per-building ids (Phase 2/5), and streaming CityGML parsing
(Phase 2). Proceed.
