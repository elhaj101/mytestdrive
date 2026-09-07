"""Phase 1b — pull traffic signs, roadway polygons and lane markings from the
Berlin Straßenbefahrung WFS.

Counts are reported for both the bbox (which is how the research note's 16,342
baseline was measured) and the 5km circle (which is what the app actually
covers). A bbox around a circle holds ~27% more area, so the circle figure is
legitimately lower — that is not a bug.
"""

import argparse
import json
import math

import wfs
from config import ANCHOR_LAT, ANCHOR_LON, BUILD, CRS_UTM33, CRS_WGS84, RADIUS_M

LAYERS = {
    "aa_verkehrszeichen": "traffic signs",
    "at_mast_lsa": "signal masts",
    "cm_fahrbahn": "roadway polygons",
    "be_fahrbahnmarkierunglinie": "lane markings",
}


def anchor_utm33() -> tuple[float, float]:
    from pyproj import Transformer

    transformer = Transformer.from_crs(CRS_WGS84, CRS_UTM33, always_xy=True)
    return transformer.transform(ANCHOR_LON, ANCHOR_LAT)


def representative_point(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    return None


def count_within_circle(payload: dict) -> int:
    """Count features whose geometry falls inside the 5km circle.

    Geometry comes back in the service's native UTM33, where a metric radius
    test is a plain euclidean distance.
    """
    ax, ay = anchor_utm33()
    inside = 0
    for feature in payload["features"]:
        point = representative_point(feature.get("geometry") or {})
        if point is None:
            continue
        if math.dist(point, (ax, ay)) <= RADIUS_M:
            inside += 1
    return inside


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    print(f"WFS Straßenbefahrung, bbox around {ANCHOR_LAT},{ANCHOR_LON}\n")

    results = {}
    for layer, label in LAYERS.items():
        print(f"{label} ({layer})")
        payload = wfs.fetch(layer, args.force)
        results[layer] = {
            "bbox": len(payload["features"]),
            "circle": count_within_circle(payload),
        }

    print("\n--- measured ---")
    print(f"{'layer':<30}{'bbox':>10}{'circle':>10}")
    for layer, counts in results.items():
        print(f"{layer:<30}{counts['bbox']:>10,}{counts['circle']:>10,}")

    signs = results["aa_verkehrszeichen"]
    print(f"\nsigns in bbox {signs['bbox']:,} (note baseline: 16,342)")
    print(f"signs in 5km circle {signs['circle']:,}")

    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "wfs_counts.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
