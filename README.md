# MyTestDrive

An interactive driving-prep desktop app for the Berlin practical driving exam. It reconstructs
every street within a **5km radius** of the TÜV Rheinland Prüfstelle in Spandau as a real 3D
world, and drives it street by street: at every junction you pick left, right or straight, and a
side panel states what must be respected entering that street.

Not a racing game and not a map viewer. The point is learning the actual exam area — real
streets, real signs, real right-of-way — by driving it.

## Status

Pre-implementation. This repository holds the folder skeleton, the execution plan
([PLAN.md](PLAN.md)) and this README. No pipeline or app code has been written yet.

Full research and reasoning lives outside this repo, in the Brain vault:
`4-Resources/mytestdrive-5km-desktop-research.md`.

## Core design decisions (already settled — do not re-litigate)

| Decision | Choice |
|---|---|
| Desktop shell | **Electron** — not Tauri. Ships its own Chromium, so WebGL2 behaves identically on every OS |
| 3D renderer | **Three.js** |
| UI layer | **React** — the side panel/HUD is the hard part, and this is the existing skill stack |
| Geometry source | **Path A** — real Berlin LoD2 building solids, not footprint+height extrusion |
| Pipeline language | **Python**, independent of the renderer/UI choice |
| Camera | **Rail-locked to the road graph.** No orbit, no zoom, no fly-over, no skipping a street |
| Rule resolution | Precomputed at build time per `(junction, incoming-leg)`, never live in the renderer |

### Why Path A (real LoD2 solids)

Berlin's LoD2 data already contains full 3D building solids with real roof shapes — it is not a
flat map to be extruded. That buys two things: junctions look like *that* junction (real corner
silhouettes, which is what makes the area learnable), and every building stays a **discrete,
addressable object** rather than one fused photogrammetry mesh, so individual buildings can be
referenced and manipulated at runtime.

### Why the camera is rail-locked

The experience is street-to-street driving. Position is a point along the current graph edge,
heading is the direction of travel, and the player's only camera input is the left/right/straight
pick at each junction. You reach a street by driving the streets that lead to it.

Two useful consequences: buildings sit beside the view rather than in front of it (so occlusion
is optional polish, not a core system), and chunk load/unload can be **predictive along the
graph** — the next candidate edges are always known — instead of general spatial streaming.

## Measured figures (Phase 1 complete, 2026-09-07)

All measured by running the pipeline. Full detail and reconciliation:
[docs/measured-counts.md](docs/measured-counts.md).

| Metric | Measured at 5km |
|---|---|
| **Drivable OSM ways** (the traversable network) | **6,001** |
| `service` ways (parking aisles, driveways — *not* drivable) | 6,590 |
| Traffic signal nodes (OSM) | **642** |
| Traffic signs (WFS) | **16,342** in bbox / **13,253** in circle |
| Signal masts (WFS `at_mast_lsa`) | 1,793 bbox / 1,508 circle |
| LoD2 tiles | **98 present** of 101 candidate |
| **Buildings** | **69,538** |
| Raw LoD2 download / uncompressed | 182 MB / 1,572 MB |
| Graph edges, junctions | not yet measured — Phase 3 |

**Two corrections to earlier figures, both worth knowing:**

The old "12,598 ways" figure was `drivable + service` (reproduced exactly: 12,591). Service ways
are parking aisles and driveways — **not streets an exam is driven on, and the player must not be
able to drive them.** The real traversable network is **6,001**. Keep service ways as rule context
only (the "leaving a driveway always yields" rule needs to know they exist), flagged
`drivable: false`.

Buildings came in at **69,538**, not the extrapolated ~46,700 (+49%). The 3.7–3.8x way/sign scale
factor does **not** transfer to buildings — the outer ring loses road density faster than housing
density. Don't scale building estimates by it.

A full 5km radius stays **inside Berlin** on all 8 compass points (verified by reverse-geocoding;
nearest Brandenburg town is ~7.3km out). This matters because Berlin's LoD2 buildings and the
Straßenbefahrung sign survey both stop at the state line — so there is complete data coverage
across the whole radius, with no degraded edges.

The 3 missing LoD2 tiles (`LoD2_378_5816`, `LoD2_379_5816`, `LoD2_379_5817`) all sit due south
over the Havel/Wannsee waterway — almost certainly "no buildings on open water", not a coverage
failure.

> **Still extrapolated:** ~6,260 graph edges and ~5,500 junctions. Both were scaled from the
> *service-inflated* way count, so both are likely well too high for a 6,001-way drivable network.
> Phase 3 measures them directly.

### Consequence for rendering — settled before Phase 5

69,538 buildings as one `Mesh` each would mean ~69k draw calls, which will not hold 60fps. That
collides with the "discrete, addressable buildings" property Path A is chosen for. Resolution:
**batch buildings into per-chunk merged geometry carrying a per-building id as a vertex
attribute.** Addressability is preserved (shader-side alpha for highlight/fade, or splitting out
only the handful of buildings actually being faded) without paying per-building draw calls. This
fits the graph-predictive chunking already agreed.

Payload itself is a non-issue: ~5.6MB simplified, at the old build's ~84 bytes/building ratio.

## Data sources

All free, no API keys, no paid services.

1. **Berlin Straßenbefahrung 2014/15 WFS** — `https://gdi.berlin.de/services/wfs/strassenbefahrung`
   (licence dl-de/zero-2.0). Layers: `aa_verkehrszeichen` (signs), `cm_fahrbahn` (roadway
   polygons), `be_fahrbahnmarkierunglinie` (lane markings), `cl_gehweg` / `bd_bordstein`
   (pavements/kerbs).
2. **OpenStreetMap via Overpass** — the road graph.
3. **Berlin LoD2 3D buildings** — ATOM feed at `https://gdi.berlin.de/data/a_lod2/atom/`.
4. **StVO sign faces** — Wikimedia Commons SVGs, public domain (§5 UrhG, amtliche Werke).

## Gotchas that will cost you hours

**WFS axis order.** A WFS 2.0 bbox using `urn:ogc:def:crs:EPSG::4326` wants **lat,lon** order, not
lon,lat. Get it wrong and `numberMatched` silently comes back **0 — not an error**. Re-confirmed
2026-09-07.

**The LoD2 ATOM feed is two levels deep.** The top-level feed at `.../a_lod2/atom/` contains a
single entry pointing at a **sub-feed**, `https://gdi.berlin.de/data/a_lod2/atom/0.atom`, which is
what actually lists all 925 `LoD2_<E>_<N>` tiles. Fetch the sub-feed, not the top-level one.

**Overpass returns HTTP 406 to the default `python-requests` user agent.** Any custom `User-Agent`
header fixes it. Confirmed 2026-09-07 — this is separate from rate-limiting and looks nothing like
it.

**Overpass rate-limiting.** The public endpoint rate-limited mid-session during the old build. Use
the mirror if it happens: `overpass.kumi.systems/api/interpreter`. Note that on 2026-09-07 the
*mirror* was rate-limiting (429) while the main endpoint worked — try both, in either order.

**The LoD2 archives contain `.xml` files, not `.gml`.** Filtering an archive's contents on a
`.gml` extension silently matches nothing and yields zero buildings with no error.

**The LoD2 data is CityGML 1.0**, not 2.0 (`http://www.opengis.net/citygml/1.0` namespaces).
`citygml-tools` targets 2.0/3.0 and needs a Java runtime that isn't installed here — so the
pipeline parses the GML directly with streaming `lxml.etree.iterparse`, which the 1.57 GB
uncompressed volume requires in any case.

**Rules resolve per approach leg, not per junction.** "Junction X is rechts-vor-links" breaks
wherever a Vorfahrtstraße crosses a residential street — two legs signposted, two not, at the same
junction. Match each sign to the leg it faces **by bearing**, and key the resolved rule on
`(junction_id, incoming_edge_id)`.

## Layout

```
pipeline/     Python. Fetch + build. Independent of the renderer/UI stack
app/main/     Electron main process, window/bundling
app/renderer/world/   Three.js scene
app/renderer/ui/      React side panel, direction picker, HUD, conflict display
data/raw/     Downloaded source data — gitignored
data/build/   Generated artifacts — gitignored, reproducible from pipeline/
docs/         Notes that belong with the code
```

`data/` is deliberately not committed: raw CityGML runs to hundreds of MB, and everything in
`data/build/` is reproducible by re-running the pipeline.

## Standing rules

- **Only data-confirmed situations are scored.** Anything inferred is a hint, never a verdict.
  Rechts-vor-links is scoreable precisely because the Straßenbefahrung survey is *complete* — "no
  priority sign in the data" means "there is no sign", not "we don't know".
- **Where sources conflict, downgrade to hint-only.** A resolved rule is confirmed; a conflicting
  one is not, even though both have data behind them.
- **€0 in licences and services.** Every dependency and data source here is free.

## Repository

Local-first, by design. There is no remote yet — the remote will be created *from* this folder
when the time comes, not the other way around.
