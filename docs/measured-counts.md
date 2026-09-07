# Measured counts — Phase 1 gate

Real numbers from actual pipeline runs, replacing the extrapolated figures the research note
carried. Anchor `52.5304357, 13.2144591`, radius 5000m.

Status: **OSM measured 2026-09-07. WFS and LoD2 still to run.**

---

## OSM road network — measured 2026-09-07

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
| Traffic signals | **642** |
| Level crossings | 68 |
| Give way | 66 |
| Stop | 26 |

## Reconciling the note's "12,598 ways"

**Traffic signals came back at 642 — an exact match to the note's baseline.** That confirms the
anchor and radius are correct, so any way-count difference is a filter-definition difference, not
a geometry error.

| Filter | Count | vs note's 12,598 |
|---|---|---|
| drivable only | 6,001 | −6,597 |
| **drivable + `service`** | **12,591** | **−7** |
| drivable + service + track | 13,024 | +426 |
| drivable + service + pedestrian | 12,667 | +69 |

**Conclusion: the note's 12,598 figure was `drivable + service`.** The 7-way gap is a couple of
days of OSM edits. Nothing is wrong with either number — they measure different things.

### Consequence — the headline figure overstates the street network by ~2x

`service` ways are parking aisles, driveways and alleys. **They are not streets a driving exam is
conducted on, and the player must not be able to drive them.** The drivable network for this app
is the **6,001** figure, not 12,598.

Service ways are still worth keeping in the data, but as *rule context* rather than drivable
edges: the standing rule "leaving a property or driveway always yields" needs to know a driveway
is there. So: keep them, flag them `drivable: false`, exclude them from the road graph the player
traverses.

This also casts doubt on the derived estimates. The old 2.2km baseline of "3,402 raw ways" was
almost certainly `drivable + service` too — the vault's own recorded Overpass query returned
**1,569 segments** at 2.2km under the drivable-only filter. So the extrapolated **~6,260 graph
edges and ~5,500 junctions** were scaled from a service-inflated base and are likely
substantially too high for the drivable-only network. Phase 3 must measure them directly.

### Filter to use from here

```
highway ~ ^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street)
area  != yes
```

Note the regex is intentionally unanchored at the end, so `*_link` classes are included — they are
real drivable connections. `service`, `track` and `pedestrian` are excluded from the drivable
graph.

---

## Overpass access gotcha — found 2026-09-07

**Overpass returns HTTP 406 to the default `python-requests` user agent.** Sending any custom
`User-Agent` header fixes it. This is separate from rate-limiting: 429 and 504 also occur
regularly on the public endpoint and are handled with backoff plus the kumi.systems mirror. Note
the mirror was itself rate-limiting on 2026-09-07 while the main endpoint worked — try both.

---

## Still to measure

- [ ] WFS traffic signs — note baseline **16,342**, measured on a *bbox*, not a circle. A bbox
      around a 5km circle is ~27% more area, so a circle-clipped count will legitimately come in
      lower. Record both, and compare each against the geometry it was originally measured on.
- [ ] LoD2 tiles present — note baseline **98 of 101 candidate**
- [ ] Real building count — extrapolation said ~46,700
- [ ] Real graph edge count after junction-splitting — extrapolation said ~6,260
- [ ] Real junction count — extrapolation said ~5,500
