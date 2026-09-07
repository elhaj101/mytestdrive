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

## Verified figures (measured 2026-09-07, not estimated)

| Metric | 2.2km (old build) | 5km (verified) | Factor |
|---|---|---|---|
| Raw drivable OSM ways | 3,402 | **12,598** | 3.70x |
| Traffic signs (WFS exact hit count) | 4,339 | **16,342** | 3.77x |
| Traffic signal nodes (OSM) | 197 | **642** | 3.26x |
| LoD2 building tiles | ~26 | **98 present** of 101 candidate | 3.77x |

The consistent scale factor is **3.7–3.8x, not the naive area ratio of 5.16x** — density falls off
toward the outer ring. Use 3.7–3.8x for any further estimate.

A full 5km radius stays **inside Berlin** on all 8 compass points (verified by reverse-geocoding;
nearest Brandenburg town is ~7.3km out). This matters because Berlin's LoD2 buildings and the
Straßenbefahrung sign survey both stop at the state line — so there is complete data coverage
across the whole radius, with no degraded edges.

The 3 missing LoD2 tiles (`LoD2_378_5816`, `LoD2_379_5816`, `LoD2_379_5817`) all sit due south
over the Havel/Wannsee waterway — almost certainly "no buildings on open water", not a coverage
failure.

> **Extrapolated, NOT measured:** ~46,700 buildings, ~6,260 graph edges, ~5,500 junctions. These
> come from applying the 3.7–3.8x factor, not from a real run. Phase 1 must replace them with
> measured counts before any architecture is committed to. See PLAN.md Phase 1.

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

**Overpass rate-limiting.** The public endpoint rate-limited mid-session during the old build. Use
the mirror if it happens: `overpass.kumi.systems/api/interpreter`.

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
