"""Phase 4 — resolve right of way per (junction, incoming edge).

Runs once at build time and bakes the result into data. No StVO logic ever runs
in the renderer; the app does a lookup.

The central correctness point: rules resolve **per approach leg, not per
junction**. A Vorfahrtstraße crossing a residential street has two signposted
legs and two unsignposted ones at the same junction, so any per-junction
classification is wrong exactly where it matters most.

Where two sources disagree, this emits a conflict and downgrades the leg to
hint-only rather than guessing. A resolved rule is confirmed; a conflicting one
is not, even though both have data behind them.
"""

import argparse
import json
import math
from collections import Counter, defaultdict

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

from config import ANCHOR_LAT, ANCHOR_LON, BUILD, CRS_UTM33, CRS_WGS84, RAW

SEARCH_M = 40.0        # how far from a junction a sign may sit and still govern it
LATERAL_TOL_M = 18.0    # a sign further than this from a road does not govern it
SIGNAL_M = 35.0

# StVO codes that resolve right of way. Speed and zone signs are deliberately
# absent: Zone 30 is a speed regime and never decides priority.
YIELD = "205"
STOP = "206"
PRIORITY_NEXT = "301"
PRIORITY_ROAD = "306"
PRIORITY_ROAD_END = "307"
RVL_WARNING = "102"
CALMED_BEGIN = "325.1"
CALMED_END = "325.2"
LEVEL_CROSSING = {"150", "151", "201"}

PRIORITY_CODES = {YIELD, STOP, PRIORITY_NEXT, PRIORITY_ROAD, PRIORITY_ROAD_END}

RULE_LABEL = {
    "signal": "Lichtzeichenanlage — signal governs",
    "stop": "Halt! Vorfahrt gewähren (Stop, §8)",
    "yield": "Vorfahrt gewähren (§8)",
    "priority": "Vorfahrtstraße — you have priority",
    "rechts_vor_links": "Rechts vor links (§8 Abs. 1)",
    "calmed_exit": "Verkehrsberuhigter Bereich exit — always yield (§10)",
    "yield_inferred": "Vorfahrt gewähren — a Vorfahrtstraße crosses here",
}


def to_local():
    transformer = Transformer.from_crs(CRS_WGS84, CRS_UTM33, always_xy=True)
    ax, ay = transformer.transform(ANCHOR_LON, ANCHOR_LAT)
    return ax, ay


def normalise(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def representative(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    return None


def load_signs(ax: float, ay: float) -> list[dict]:
    data = json.loads((RAW / "wfs" / "aa_verkehrszeichen.geojson").read_text())
    signs = []
    for feature in data["features"]:
        point = representative(feature.get("geometry") or {})
        if point is None:
            continue
        properties = feature["properties"]
        codes = [c.strip() for c in (properties.get("vkz_zeiche") or "").split(",") if c.strip()]
        signs.append(
            {
                "x": point[0] - ax,
                "y": point[1] - ay,
                "codes": codes,
                "street": (properties.get("strasse") or "").strip(),
                "mast": properties.get("mast_id"),
                "mount": properties.get("auf") or "",
            }
        )
    return signs


def load_signal_nodes() -> dict[int, dict]:
    """Signal nodes with their tags.

    `traffic_signals:direction` (present on 389 of 642) states which way of
    travel the signal faces, relative to the OSM way's own node order. That
    places a stop line on the correct approach instead of inferring it.
    Pedestrian-crossing signals (crossing=traffic_signals) do not govern
    junction right of way and are excluded.
    """
    data = json.loads((RAW / "osm" / "signals_5km.json").read_text())
    nodes = {}
    for element in data["elements"]:
        if element["type"] != "node":
            continue
        tags = element.get("tags") or {}
        if tags.get("crossing") == "traffic_signals":
            continue
        nodes[element["id"]] = {"direction": tags.get("traffic_signals:direction")}
    return nodes


def signalised_approaches(graph: dict, signal_nodes: set[int]) -> tuple[set, set]:
    """Which (junction, approach edge) pairs a traffic signal actually governs.

    Matched by OSM node id, not proximity. Only 53 of 642 signal nodes are
    junction nodes; 515 are stop lines partway along an edge. A stop line
    governs traffic on its own edge heading for the nearer end of that edge —
    which proximity to a junction centre cannot express, and gets wrong at
    junctions standing close together.
    """
    pairs, junctions_with_signal = set(), set()
    for edge in graph["edges"]:
        nodes = edge.get("nodes") or []
        if not nodes:
            continue
        for index, node in enumerate(nodes):
            signal = signal_nodes.get(node)
            if signal is None:
                continue

            direction = signal["direction"]
            if direction == "forward":
                ends = [nodes[-1]]
            elif direction == "backward":
                ends = [nodes[0]]
            elif index == 0:
                ends = [nodes[0]]
            elif index == len(nodes) - 1:
                ends = [nodes[-1]]
            elif direction == "both":
                ends = [nodes[0], nodes[-1]]
            else:
                # No direction stated: a stop line faces the end it sits nearer.
                ends = [nodes[-1] if index * 2 >= len(nodes) else nodes[0]]

            for end in ends:
                junctions_with_signal.add(f"j{end}")
                pairs.add((f"j{end}", edge["id"]))
    return pairs, junctions_with_signal


def point_segment_distance(px, py, x0, y0, x1, y1) -> float:
    dx, dy = x1 - x0, y1 - y0
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def distance_to_edge(edge: dict, x: float, y: float, junction_xy, within_m: float) -> float:
    """Shortest distance from a point to the part of the edge near the junction.

    Only the stretch within `within_m` of the junction counts, so a sign is not
    attributed to a long road merely because that road passes nearby further on.
    """
    best = float("inf")
    jx, jy = junction_xy
    points = edge["points"]
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i][:2], points[i + 1][:2]
        if min(math.dist((x0, y0), (jx, jy)), math.dist((x1, y1), (jx, jy))) > within_m:
            continue
        best = min(best, point_segment_distance(x, y, x0, y0, x1, y1))
    return best


def assign_to_edge(
    incident_edges: list[dict], junction_xy, x: float, y: float, street: str
) -> tuple[str | None, bool]:
    """Which road does the thing at (x, y) stand beside?

    Bearing from the junction centre was tried first and is too weak at these
    ranges: a sign sits at the corner, 5-15m out and offset laterally, so the
    lateral offset dominates the bearing (median error 48 degrees against the
    correct leg). Perpendicular distance to the road's own polyline encodes
    "stands beside this road" directly, and the surveyed street name — which
    matches a leg name 83% of the time — settles the rest.
    """
    scored = []
    for edge in incident_edges:
        distance = distance_to_edge(edge, x, y, junction_xy, SEARCH_M)
        if distance == float("inf"):
            continue
        named = bool(street) and edge["name"] == street
        scored.append((distance, named, edge))
    if not scored:
        return None, False

    named_matches = [s for s in scored if s[1]]
    if named_matches:
        # The survey says which street this sign belongs to; trust it.
        named_matches.sort(key=lambda s: s[0])
        if len(named_matches) > 1 and named_matches[1][0] - named_matches[0][0] < 1.0:
            return named_matches[0][2]["id"], True
        return named_matches[0][2]["id"], False

    scored.sort(key=lambda s: s[0])
    if scored[0][0] > LATERAL_TOL_M:
        return None, False
    ambiguous = len(scored) > 1 and (scored[1][0] - scored[0][0]) < 2.0
    return scored[0][2]["id"], ambiguous


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()

    ax, ay = to_local()
    graph = json.loads((BUILD / "graph.json").read_text())
    junctions = graph["junctions"]
    edges = {e["id"]: e for e in graph["edges"]}

    print(f"junctions {len(junctions):,}, edges {len(edges):,}")

    signs = load_signs(ax, ay)
    print(f"signs {len(signs):,}")
    signal_nodes = load_signal_nodes()
    print(f"osm signal nodes {len(signal_nodes):,}")

    # Every edge touching a junction, in both directions. junction["legs"] holds
    # only departable edges, so a one-way street approaching the junction has no
    # leg at all and its signs could never be matched.
    incident: dict[str, list[dict]] = defaultdict(list)
    for edge in graph["edges"]:
        incident[f"j{edge['from']}"].append(edge)
        incident[f"j{edge['to']}"].append(edge)

    junction_keys = list(junctions.keys())
    junction_xy = np.array([[junctions[k]["x"], junctions[k]["y"]] for k in junction_keys])
    junction_tree = cKDTree(junction_xy)

    # Signs to junctions, then to a leg of that junction.
    leg_signs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    leg_ambiguous: set[tuple[str, str]] = set()
    unassigned = 0
    priority_signs = 0

    for sign in signs:
        if not any(c in PRIORITY_CODES or c in (CALMED_BEGIN, CALMED_END)
                   or c in LEVEL_CROSSING for c in sign["codes"]):
            continue
        priority_signs += 1
        near = junction_tree.query_ball_point([sign["x"], sign["y"]], SEARCH_M)
        if not near:
            unassigned += 1
            continue
        # Nearest junction first; a sign governs the junction it stands before.
        near.sort(key=lambda i: math.dist(junction_xy[i], (sign["x"], sign["y"])))
        key = junction_keys[near[0]]
        edge_id, ambiguous = assign_to_edge(
            incident[key], (junctions[key]["x"], junctions[key]["y"]),
            sign["x"], sign["y"], sign["street"])
        if edge_id is None:
            unassigned += 1
            continue
        leg_signs[(key, edge_id)].append(sign)
        if ambiguous:
            leg_ambiguous.add((key, edge_id))

    leg_signals, signal_junctions = signalised_approaches(graph, signal_nodes)

    print(f"priority-relevant signs {priority_signs:,}, unassigned {unassigned:,}")
    print(f"signalised approaches {len(leg_signals):,} across {len(signal_junctions):,} junctions")

    rules = {}
    stats = Counter()

    for key, junction in junctions.items():
        # Edges you can *arrive* on, which is what a rule is keyed to. A one-way
        # street leaving the junction is never an approach; one entering it is,
        # even though it appears in no departure leg.
        approaches = []
        for edge in incident[key]:
            if f"j{edge['to']}" == key and edge["oneway"] >= 0:
                approaches.append(edge)
            elif f"j{edge['from']}" == key and edge["oneway"] <= 0:
                approaches.append(edge)

        leaves_calmed_area = any(
            e["highway"] != "living_street" for e in incident[key]
        )

        # A Vorfahrtstraße designates the *road*, not one approach to it. Collect
        # the priority streets at this junction first: without this, the opposite
        # approach on the same priority street defaults to rechts-vor-links, and
        # so does the street crossing it — both wrong, and wrong in the unsafe
        # direction, since rechts-vor-links does not apply where a priority road
        # crosses at all.
        priority_streets = set()
        for edge in approaches:
            codes = {c for s in leg_signs.get((key, edge["id"]), []) for c in s["codes"]}
            if codes & {PRIORITY_ROAD, PRIORITY_NEXT} and edge["name"]:
                priority_streets.add(edge["name"])

        for edge in approaches:
            edge_id = edge["id"]
            pair = (key, edge_id)
            codes = {c for sign in leg_signs.get(pair, []) for c in sign["codes"]}

            conflicts: list[str] = []

            # StVO precedence order. Police direction would outrank everything but
            # appears in no data source, so it is structurally absent here.
            if pair in leg_signals:
                rule = "signal"
                if codes & {YIELD, STOP, PRIORITY_ROAD, PRIORITY_NEXT}:
                    # Not a conflict: the signal legally wins. Recorded because the
                    # side panel should say the fixed sign is there but inactive.
                    stats["signal_over_sign"] += 1
            elif STOP in codes:
                rule = "stop"
            elif YIELD in codes:
                rule = "yield"
            elif codes & {PRIORITY_ROAD, PRIORITY_NEXT}:
                rule = "priority"
            elif edge["name"] and edge["name"] in priority_streets:
                # Same designated priority road, other approach to the same junction.
                rule = "priority"
            elif priority_streets:
                # A priority road crosses here, so this approach must yield — but no
                # sign on *this* approach says so, so it is inferred, not confirmed.
                rule = "yield_inferred"
                conflicts.append(
                    "yield inferred from a crossing Vorfahrtstraße, no sign on this approach"
                )
            elif edge["highway"] == "living_street" and leaves_calmed_area:
                # §10 applies on *leaving* a verkehrsberuhigter Bereich. Driving
                # from one calmed street into another stays inside the area, where
                # the ordinary §8 default still governs.
                rule = "calmed_exit"
            else:
                rule = "rechts_vor_links"

            # Contradictions that the data cannot settle.
            if {YIELD, PRIORITY_ROAD} <= codes or {STOP, PRIORITY_ROAD} <= codes:
                conflicts.append("yield and priority signage on the same leg")
            if pair in leg_ambiguous:
                conflicts.append("sign could not be attributed to one road by distance or name")
            if key in signal_junctions and pair not in leg_signals:
                # An OSM signal at this junction that no WFS mast confirms for this
                # approach. The note's named conflict case; downgrade, do not guess.
                conflicts.append("signal at junction but not confirmed for this approach")

            scored = not conflicts
            rules[f"{key}|{edge_id}"] = {
                "junction": key,
                "edge": edge_id,
                "street": edge["name"],
                "rule": rule,
                "label": RULE_LABEL[rule],
                "scored": scored,
                "conflicts": conflicts,
                "codes": sorted(codes),
                "level_crossing": bool(codes & LEVEL_CROSSING),
                # Speed regime is recorded but never used to resolve priority.
                "zone_maxspeed": edge["zone_maxspeed"],
                "maxspeed": edge["maxspeed"],
            }
            stats[rule] += 1
            if not scored:
                stats["hint_only"] += 1

    out = BUILD / "rules.json"
    out.write_text(json.dumps({"rules": rules}))

    total = len(rules)
    print(f"\n--- resolved {total:,} (junction, incoming edge) pairs ---")
    for rule in ("signal", "stop", "yield", "yield_inferred", "priority", "calmed_exit", "rechts_vor_links"):
        n = stats[rule]
        print(f"  {rule:<18}{n:>7,}  {n / total * 100:5.1f}%")
    print(f"\n  hint-only (conflict)  {stats['hint_only']:>5,}  "
          f"{stats['hint_only'] / total * 100:.1f}%")
    print(f"  signal over fixed sign{stats['signal_over_sign']:>5,}")
    print(f"\nwrote {out.name} ({out.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
