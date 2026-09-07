"""Phase 3 — turn the drivable OSM ways into a routable graph.

Built from the 6,001 drivable ways only. `service`, `track` and `pedestrian`
are deliberately excluded: they are parking aisles, driveways and alleys, not
streets a driving exam is conducted on, and the player must not be able to
drive them. They stay available as rule context for the "leaving a driveway
always yields" rule, which is Phase 4's concern, not the graph's.

Coordinates are projected to UTM33 and then shifted to a local metric frame
centred on the anchor, because raw UTM northings are ~5.8M and lose float
precision at render time.
"""

import argparse
import json
import math
from collections import Counter, defaultdict, deque

from pyproj import Transformer

from config import (
    ANCHOR_LAT,
    ANCHOR_LON,
    BUILD,
    CRS_UTM33,
    CRS_WGS84,
    RAW,
)

STRAIGHT_DEG = 40.0
UTURN_DEG = 140.0

ONEWAY_TRUE = {"yes", "true", "1"}
ONEWAY_REVERSED = {"-1", "reverse"}


def local_frame():
    transformer = Transformer.from_crs(CRS_WGS84, CRS_UTM33, always_xy=True)
    ax, ay = transformer.transform(ANCHOR_LON, ANCHOR_LAT)

    def to_local(lon: float, lat: float) -> tuple[float, float]:
        x, y = transformer.transform(lon, lat)
        return x - ax, y - ay

    return to_local


def is_oneway(tags: dict) -> int:
    """1 = forward only, -1 = reverse only, 0 = bidirectional."""
    value = tags.get("oneway", "")
    if value in ONEWAY_TRUE:
        return 1
    if value in ONEWAY_REVERSED:
        return -1
    if value in ("no", "false", "0"):
        return 0
    # Roundabouts and motorways are one-way by definition unless tagged otherwise.
    if tags.get("junction") in ("roundabout", "circular"):
        return 1
    if tags.get("highway") in ("motorway", "motorway_link"):
        return 1
    return 0


def bearing_deg(p0: tuple[float, float], p1: tuple[float, float]) -> float:
    """Angle of p0->p1 in degrees, CCW from east, in the local metric frame."""
    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))


def normalise(angle: float) -> float:
    """Wrap to (-180, 180]."""
    return (angle + 180.0) % 360.0 - 180.0


def classify(relative: float) -> str:
    if abs(relative) <= STRAIGHT_DEG:
        return "straight"
    if abs(relative) >= UTURN_DEG:
        return "uturn"
    return "left" if relative > 0 else "right"


def polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def build() -> dict:
    to_local = local_frame()
    raw = json.loads((RAW / "osm" / "ways_drivable_5km.json").read_text())
    ways = [e for e in raw["elements"] if e["type"] == "way"]
    print(f"drivable ways: {len(ways):,}")

    # A node shared by two or more ways is a junction. Way endpoints are also
    # junction candidates so dead ends terminate cleanly rather than dangling.
    node_uses = Counter()
    for way in ways:
        for node_id in way["nodes"]:
            node_uses[node_id] += 1

    node_xy: dict[int, tuple[float, float]] = {}
    for way in ways:
        for node_id, point in zip(way["nodes"], way["geometry"]):
            if node_id not in node_xy:
                node_xy[node_id] = to_local(point["lon"], point["lat"])

    junction_ids = {n for n, count in node_uses.items() if count >= 2}
    for way in ways:
        junction_ids.add(way["nodes"][0])
        junction_ids.add(way["nodes"][-1])
    print(f"junction nodes: {len(junction_ids):,}")

    edges: list[dict] = []
    for way in ways:
        tags = way.get("tags", {})
        oneway = is_oneway(tags)
        node_ids = way["nodes"]

        # Split the way wherever it touches a junction node.
        start = 0
        for i in range(1, len(node_ids)):
            if node_ids[i] not in junction_ids and i != len(node_ids) - 1:
                continue
            segment = node_ids[start : i + 1]
            if len(segment) < 2:
                continue
            points = [node_xy[n] for n in segment]
            length = polyline_length(points)
            if length > 0:
                edges.append(
                    {
                        "id": f"e{len(edges)}",
                        "way_id": way["id"],
                        "from": segment[0],
                        "to": segment[-1],
                        "points": [[round(x, 2), round(y, 2)] for x, y in points],
                        "length_m": round(length, 2),
                        "oneway": oneway,
                        "highway": tags.get("highway", ""),
                        "name": tags.get("name", ""),
                        "maxspeed": tags.get("maxspeed", ""),
                        "zone_maxspeed": tags.get("zone:maxspeed", ""),
                        # Needed by the elevation stage: a bridge deck is not at ground level,
                        # and a tunnel is below it.
                        "bridge": tags.get("bridge", ""),
                        "tunnel": tags.get("tunnel", ""),
                        "layer": tags.get("layer", ""),
                    }
                )
            start = i

    print(f"edges after junction-splitting: {len(edges):,}")

    # Directed traversals: (edge_id, direction) where direction 1 means from->to.
    incident: dict[int, list[tuple[str, int]]] = defaultdict(list)
    by_id = {e["id"]: e for e in edges}
    for edge in edges:
        if edge["oneway"] >= 0:
            incident[edge["from"]].append((edge["id"], 1))
        if edge["oneway"] <= 0:
            incident[edge["to"]].append((edge["id"], -1))

    def departure_bearing(edge: dict, direction: int) -> float:
        points = edge["points"] if direction == 1 else edge["points"][::-1]
        return bearing_deg(tuple(points[0]), tuple(points[1]))

    def arrival_bearing(edge: dict, direction: int) -> float:
        points = edge["points"] if direction == 1 else edge["points"][::-1]
        return bearing_deg(tuple(points[-2]), tuple(points[-1]))

    junctions: dict[str, dict] = {}
    ambiguous = 0
    for node_id in junction_ids:
        outgoing = incident.get(node_id, [])
        legs = [
            {
                "edge": edge_id,
                "direction": direction,
                "bearing": round(departure_bearing(by_id[edge_id], direction), 1),
                "name": by_id[edge_id]["name"],
            }
            for edge_id, direction in outgoing
        ]
        x, y = node_xy[node_id]
        junctions[f"j{node_id}"] = {
            "x": round(x, 2),
            "y": round(y, 2),
            "legs": legs,
            "turns": {},
        }

    # For every way of arriving at a junction, classify every way of leaving it.
    for edge in edges:
        for direction in (1, -1):
            if direction == 1 and edge["oneway"] == -1:
                continue
            if direction == -1 and edge["oneway"] == 1:
                continue
            arrive_node = edge["to"] if direction == 1 else edge["from"]
            key = f"j{arrive_node}"
            if key not in junctions:
                continue
            incoming = arrival_bearing(edge, direction)
            options = []
            for leg in junctions[key]["legs"]:
                if leg["edge"] == edge["id"]:
                    continue
                relative = normalise(leg["bearing"] - incoming)
                options.append(
                    {
                        "edge": leg["edge"],
                        "direction": leg["direction"],
                        "relative": round(relative, 1),
                        "turn": classify(relative),
                        "name": leg["name"],
                    }
                )
            buckets = Counter(o["turn"] for o in options)
            if any(n > 1 for t, n in buckets.items() if t != "uturn"):
                ambiguous += 1
            junctions[key]["turns"][f"{edge['id']}:{direction}"] = options

    return {
        "edges": edges,
        "junctions": junctions,
        "by_id": by_id,
        "incident": incident,
        "node_xy": node_xy,
        "ambiguous": ambiguous,
    }


def connectivity(graph: dict, spawn_edge: str) -> tuple[int, int]:
    """Directed reachability from the spawn, and the largest undirected component."""
    by_id, incident = graph["by_id"], graph["incident"]

    seen = set()
    queue = deque([(spawn_edge, 1), (spawn_edge, -1)])
    while queue:
        edge_id, direction = queue.popleft()
        if (edge_id, direction) in seen:
            continue
        edge = by_id[edge_id]
        if direction == 1 and edge["oneway"] == -1:
            continue
        if direction == -1 and edge["oneway"] == 1:
            continue
        seen.add((edge_id, direction))
        arrive = edge["to"] if direction == 1 else edge["from"]
        for next_id, next_dir in incident.get(arrive, []):
            if next_id != edge_id:
                queue.append((next_id, next_dir))
    reachable = {e for e, _ in seen}

    adjacency = defaultdict(set)
    for edge in graph["edges"]:
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])
    largest, unvisited = 0, set(adjacency)
    while unvisited:
        start = unvisited.pop()
        size, queue = 1, deque([start])
        while queue:
            node = queue.popleft()
            for neighbour in adjacency[node]:
                if neighbour in unvisited:
                    unvisited.remove(neighbour)
                    size += 1
                    queue.append(neighbour)
        largest = max(largest, size)
    return len(reachable), largest


def resolve_spawn(graph: dict) -> dict:
    """Free Roam always starts at the TÜV, so the spawn is a real drivable edge."""
    best = None
    for edge in graph["edges"]:
        for i in range(len(edge["points"]) - 1):
            (x0, y0), (x1, y1) = edge["points"][i], edge["points"][i + 1]
            dx, dy = x1 - x0, y1 - y0
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, (-x0 * dx - y0 * dy) / seg))
            px, py = x0 + t * dx, y0 + t * dy
            distance = math.hypot(px, py)
            if best is None or distance < best["distance"]:
                offset = polyline_length([tuple(p) for p in edge["points"][: i + 1]])
                offset += math.hypot(px - x0, py - y0)
                best = {
                    "edge": edge["id"],
                    "name": edge["name"],
                    "highway": edge["highway"],
                    "distance": round(distance, 2),
                    "offset_m": round(offset, 2),
                    "heading": round(bearing_deg((x0, y0), (x1, y1)), 1),
                    "point": [round(px, 2), round(py, 2)],
                }
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()

    graph = build()
    spawn = resolve_spawn(graph)
    reachable, largest = connectivity(graph, spawn["edge"])

    edges, junctions = graph["edges"], graph["junctions"]
    dead_ends = sum(1 for j in junctions.values() if len(j["legs"]) <= 1)
    oneway_edges = sum(1 for e in edges if e["oneway"] != 0)
    total_km = sum(e["length_m"] for e in edges) / 1000.0

    print(f"\n--- measured ---")
    print(f"edges                 {len(edges):>8,}   (extrapolation said ~6,260)")
    print(f"junctions             {len(junctions):>8,}   (extrapolation said ~5,500)")
    print(f"one-way edges         {oneway_edges:>8,}")
    print(f"dead ends             {dead_ends:>8,}")
    print(f"total drivable length {total_km:>8,.1f} km")
    print(f"reachable from spawn  {reachable:>8,}  ({reachable / len(edges) * 100:.1f}% of edges)")
    print(f"largest component     {largest:>8,}  nodes")
    print(f"ambiguous turn sets   {graph['ambiguous']:>8,}  (>1 option in same left/right/straight bucket)")

    print(f"\nspawn: {spawn['name'] or '(unnamed)'} [{spawn['highway']}] {spawn['edge']}")
    print(f"  {spawn['distance']}m from the TÜV anchor, offset {spawn['offset_m']}m, heading {spawn['heading']}deg")

    named = Counter(e["name"] for e in edges if e["name"])
    print(f"\ndistinct named streets: {len(named):,}")
    print("longest by edge count:", ", ".join(f"{n} ({c})" for n, c in named.most_common(3)))

    BUILD.mkdir(parents=True, exist_ok=True)
    out = {
        "anchor": {"lat": ANCHOR_LAT, "lon": ANCHOR_LON},
        "spawn": spawn,
        "edges": edges,
        "junctions": junctions,
        "stats": {
            "edges": len(edges),
            "junctions": len(junctions),
            "oneway_edges": oneway_edges,
            "dead_ends": dead_ends,
            "total_km": round(total_km, 1),
            "reachable_edges": reachable,
            "largest_component_nodes": largest,
            "ambiguous_turn_sets": graph["ambiguous"],
            "named_streets": len(named),
        },
    }
    path = BUILD / "graph.json"
    path.write_text(json.dumps(out))
    print(f"\nwrote {path.name} ({path.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
