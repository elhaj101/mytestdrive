"""Phase 3b — give the road graph elevation.

OSM carries no elevation and the WFS roadway polygons are 2D (verified: every
sampled coordinate had exactly two components), so nothing else in the pipeline
knows how high a road sits. The LoD2 GroundSurface polygons do: their z is the
ground height at each building's footprint, they are dense, and they are already
parsed, so this needs no new download.

Elevation is interpolated by inverse-distance weighting over the k nearest
ground samples. Accuracy well inside what a driving-height camera needs, but
sparse where there are no buildings — parkland, water, industrial land — so the
distance to the nearest sample is reported per node rather than assumed small.
"""

import argparse
import gzip
import json
import math

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

from config import ANCHOR_LAT, ANCHOR_LON, BUILD, CRS_UTM33, CRS_WGS84

NEIGHBOURS = 8
SAMPLE_GRID_M = 2.0  # dedup ground samples to this spacing
FAR_SAMPLE_M = 50.0   # beyond this the nearest building is too far to vouch for the road
SPREAD_M = 5.0        # neighbour disagreement above this means stepped terrain nearby
MAX_PLAUSIBLE_GRADE = 12.0  # Berlin is flat; steeper than this is an artefact, not a hill


def anchor_utm33() -> tuple[float, float]:
    transformer = Transformer.from_crs(CRS_WGS84, CRS_UTM33, always_xy=True)
    return transformer.transform(ANCHOR_LON, ANCHOR_LAT)


def ground_samples() -> np.ndarray:
    """Every LoD2 GroundSurface vertex, in the local metric frame, deduped."""
    ax, ay = anchor_utm33()
    seen: dict[tuple[int, int], float] = {}
    for path in sorted((BUILD / "parsed").glob("*.json.gz")):
        with gzip.open(path, "rt") as handle:
            data = json.load(handle)
        for building in data["buildings"]:
            for polygon in building["surfaces"].get("ground", []):
                for x, y, z in polygon["exterior"]:
                    key = (round((x - ax) / SAMPLE_GRID_M), round((y - ay) / SAMPLE_GRID_M))
                    if key not in seen:
                        seen[key] = z
    points = np.array(
        [[k[0] * SAMPLE_GRID_M, k[1] * SAMPLE_GRID_M, z] for k, z in seen.items()],
        dtype=np.float64,
    )
    return points


def interpolate(
    tree: cKDTree, heights: np.ndarray, query: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """IDW over the k nearest ground samples.

    Returns the height, the distance to the nearest sample, and the height
    spread among the neighbours used. IDW beat a median estimator here on the
    only metric that matters (implausible grades: 34 vs 58-67), so the spread is
    reported as a confidence signal rather than smoothed away.
    """
    distances, indices = tree.query(query, k=NEIGHBOURS)
    distances = np.atleast_2d(distances)
    indices = np.atleast_2d(indices)
    # An exact hit would divide by zero; clamp instead of special-casing it.
    weights = 1.0 / np.maximum(distances, 0.01) ** 2
    z = (heights[indices] * weights).sum(axis=1) / weights.sum(axis=1)
    spread = heights[indices].max(axis=1) - heights[indices].min(axis=1)
    return z, distances[:, 0], spread


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()

    graph_path = BUILD / "graph.json"
    graph = json.loads(graph_path.read_text())
    if len(graph["edges"][0]["points"][0]) == 3:
        print("graph.json already carries elevation; rebuild it with build_graph.py to redo")
        return

    print("collecting LoD2 GroundSurface samples")
    samples = ground_samples()
    print(f"  {len(samples):,} deduped ground samples ({SAMPLE_GRID_M}m grid)")
    print(f"  z range {samples[:, 2].min():.1f} .. {samples[:, 2].max():.1f} m ASL")

    tree = cKDTree(samples[:, :2])
    heights = samples[:, 2]

    # One flat array of every polyline vertex, so the interpolation is vectorised.
    flat = []
    spans = []
    for edge in graph["edges"]:
        spans.append((len(flat), len(edge["points"])))
        flat.extend(edge["points"])
    flat = np.array(flat, dtype=np.float64)
    print(f"\ninterpolating {len(flat):,} polyline vertices")
    z, nearest, spread = interpolate(tree, heights, flat)

    for edge, (start, count) in zip(graph["edges"], spans):
        zs = list(z[start : start + count])

        if edge["bridge"] and count > 2:
            # A bridge deck does not follow the ground beneath it. Interpolating
            # ground under a Havel crossing would dip the road to water level, so
            # run the deck straight between its abutments instead.
            cumulative = [0.0]
            for i in range(1, count):
                p0, p1 = edge["points"][i - 1], edge["points"][i]
                cumulative.append(cumulative[-1] + math.dist(p0, p1))
            total = cumulative[-1] or 1.0
            zs = [zs[0] + (zs[-1] - zs[0]) * (c / total) for c in cumulative]
        elif count > 2:
            # Mild smoothing of interior vertices only. Endpoints are left exactly
            # as interpolated so edges meeting at a junction still agree on height.
            smoothed = list(zs)
            for i in range(1, count - 1):
                smoothed[i] = (zs[i - 1] + 2.0 * zs[i] + zs[i + 1]) / 4.0
            zs = smoothed

        edge["points"] = [
            [p[0], p[1], round(float(zz), 2)] for p, zz in zip(edge["points"], zs)
        ]
        z[start : start + count] = zs
        edge["grade_pct"] = None
        if edge["length_m"] > 1:
            rise = z[start + count - 1] - z[start]
            edge["grade_pct"] = round(float(rise / edge["length_m"] * 100), 2)

    junction_xy = np.array(
        [[j["x"], j["y"]] for j in graph["junctions"].values()], dtype=np.float64
    )
    jz, jnear, jspread = interpolate(tree, heights, junction_xy)
    for (key, junction), zz, dd, sp in zip(graph["junctions"].items(), jz, jnear, jspread):
        junction["z"] = round(float(zz), 2)
        junction["ground_sample_m"] = round(float(dd), 1)
        # Same principle the rule engine uses: say when the data cannot vouch for
        # a value rather than presenting an inference as fact.
        junction["z_confident"] = bool(dd <= FAR_SAMPLE_M and sp <= SPREAD_M)

    spawn = graph["spawn"]
    sz, _, _ = interpolate(tree, heights, np.array([spawn["point"]], dtype=np.float64))
    spawn["z"] = round(float(sz[0]), 2)

    for edge in graph["edges"]:
        edge["grade_suspect"] = bool(
            edge["grade_pct"] is not None and abs(edge["grade_pct"]) > MAX_PLAUSIBLE_GRADE
        )

    far = int((jnear > FAR_SAMPLE_M).sum())
    unconfident = sum(1 for j in graph["junctions"].values() if not j["z_confident"])
    suspect = sum(1 for e in graph["edges"] if e["grade_suspect"])
    grades = np.array([e["grade_pct"] for e in graph["edges"] if e["grade_pct"] is not None])
    bridges = [e for e in graph["edges"] if e["bridge"]]

    print("\n--- elevation ---")
    print(f"road z range        {z.min():>8.1f} .. {z.max():.1f} m ASL")
    print(f"junction z range    {jz.min():>8.1f} .. {jz.max():.1f} m ASL")
    print(f"spawn z             {spawn['z']:>8.1f} m ASL")
    print(f"nearest ground sample per junction:")
    for pct in (50, 90, 99):
        print(f"   p{pct:<3}             {np.percentile(jnear, pct):>8.1f} m")
    print(f"   max               {jnear.max():>8.1f} m")
    print(f"junctions >{FAR_SAMPLE_M:.0f}m from any sample: {far:,} ({far / len(jnear) * 100:.1f}%)")
    print(f"\ngrade: median {np.median(np.abs(grades)):.2f}%, "
          f"p99 {np.percentile(np.abs(grades), 99):.2f}%, max {np.abs(grades).max():.2f}%")
    steep = int((np.abs(grades) > 15).sum())
    print(f"edges steeper than 15%: {steep:,}")
    print(f"\nflagged as low confidence:")
    print(f"   junctions (sample >{FAR_SAMPLE_M:.0f}m or spread >{SPREAD_M:.0f}m)  "
          f"{unconfident:,} ({unconfident / len(jnear) * 100:.1f}%)")
    print(f"   edges (grade >{MAX_PLAUSIBLE_GRADE:.0f}%)                      "
          f"{suspect:,} ({suspect / len(graph['edges']) * 100:.1f}%)")

    if bridges:
        bg = np.array([abs(e["grade_pct"]) for e in bridges if e["grade_pct"] is not None])
        print(f"\nbridge edges: {len(bridges)}, median |grade| {np.median(bg):.2f}%")

    graph_path.write_text(json.dumps(graph))
    print(f"\nrewrote {graph_path.name} with 3D polylines "
          f"({graph_path.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
