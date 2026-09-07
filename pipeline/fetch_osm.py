"""Phase 1a — pull the OSM road graph and signal nodes for the 5km radius.

Counts are reported against two highway filters, not one. The vault records
1,569 segments at 2.2km under the drivable filter while the research note's
baseline says 3,402 ways at the same radius — so the note's figure used a wider
filter. Reporting both is what makes the 5km numbers interpretable instead of
a bare count nobody can reconcile.
"""

import argparse

import overpass
from config import (
    ANCHOR_LAT,
    ANCHOR_LON,
    DRIVABLE_HIGHWAY_RE,
    EXTRA_HIGHWAY_RE,
    RADIUS_M,
)

AROUND = f"around:{RADIUS_M},{ANCHOR_LAT},{ANCHOR_LON}"

QUERY_DRIVABLE = f"""
[out:json][timeout:600];
way({AROUND})
  [highway~"{DRIVABLE_HIGHWAY_RE}"]
  [area!=yes];
out geom tags;
"""

QUERY_EXTRA = f"""
[out:json][timeout:600];
way({AROUND})
  [highway~"{EXTRA_HIGHWAY_RE}"]
  [area!=yes];
out geom tags;
"""

QUERY_SIGNALS = f"""
[out:json][timeout:300];
node({AROUND})[highway=traffic_signals];
out;
"""

QUERY_HAZARDS = f"""
[out:json][timeout:300];
(
  node({AROUND})[railway~"^(level_crossing|tram_level_crossing)$"];
  node({AROUND})[highway=stop];
  node({AROUND})[highway=give_way];
);
out;
"""


def count_ways(payload: dict) -> int:
    return sum(1 for el in payload["elements"] if el["type"] == "way")


def count_nodes(payload: dict) -> int:
    return sum(1 for el in payload["elements"] if el["type"] == "node")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore cached responses")
    args = parser.parse_args()

    print(f"Overpass, radius {RADIUS_M}m around {ANCHOR_LAT},{ANCHOR_LON}\n")

    print("drivable ways (recorded vault filter)")
    drivable = overpass.run(QUERY_DRIVABLE, "ways_drivable_5km", args.force)

    print("additional traffic-carrying ways (links, service, pedestrian, track)")
    extra = overpass.run(QUERY_EXTRA, "ways_extra_5km", args.force)

    print("traffic signal nodes")
    signals = overpass.run(QUERY_SIGNALS, "signals_5km", args.force)

    print("hazard nodes (level crossings, stop, give way)")
    hazards = overpass.run(QUERY_HAZARDS, "hazards_5km", args.force)

    n_drivable = count_ways(drivable)
    n_extra = count_ways(extra)
    n_signals = count_nodes(signals)
    n_hazards = count_nodes(hazards)

    print("\n--- measured ---")
    print(f"drivable ways           {n_drivable:>7,}")
    print(f"extra ways              {n_extra:>7,}")
    print(f"combined                {n_drivable + n_extra:>7,}   (note baseline: 12,598)")
    print(f"traffic signal nodes    {n_signals:>7,}   (note baseline:    642)")
    print(f"hazard nodes            {n_hazards:>7,}")

    hazard_kinds: dict[str, int] = {}
    for el in hazards["elements"]:
        tags = el.get("tags", {})
        kind = tags.get("railway") or tags.get("highway") or "unknown"
        hazard_kinds[kind] = hazard_kinds.get(kind, 0) + 1
    for kind, n in sorted(hazard_kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {kind:<22}{n:>7,}")


if __name__ == "__main__":
    main()
