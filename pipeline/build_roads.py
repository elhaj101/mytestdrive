"""Phase 6 (brought forward) — road surfaces and lane markings as real geometry.

The graph centreline drives movement and is never rendered. What the player
actually sees is the surveyed roadway surface: cm_fahrbahn holds 4,927
MultiPolygons of real paved area — true width, junction shapes, flared corners
— and be_fahrbahnmarkierunglinie holds 19,530 painted line features.

Both layers are 2D, so they are draped onto the same LoD2-derived height
surface the road graph uses, keeping roads and buildings consistent.
"""

import argparse
import json
import struct
from collections import defaultdict

import numpy as np
from mapbox_earcut import triangulate_float64
from pyproj import Transformer
from scipy.spatial import cKDTree

import build_elevation
from config import ANCHOR_LAT, ANCHOR_LON, BUILD, CRS_UTM33, CRS_WGS84, RAW

CHUNK_M = 500
MARKING_LIFT_M = 0.06  # painted lines sit just above the surface, not in it
ROAD_LIFT_M = 0.02


def anchor_utm33() -> tuple[float, float]:
    transformer = Transformer.from_crs(CRS_WGS84, CRS_UTM33, always_xy=True)
    return transformer.transform(ANCHOR_LON, ANCHOR_LAT)


def height_sampler():
    samples = build_elevation.ground_samples()
    tree = cKDTree(samples[:, :2])
    heights = samples[:, 2]

    def sample(points: np.ndarray) -> np.ndarray:
        z, _, _ = build_elevation.interpolate(tree, heights, points)
        return z

    return sample


def rings_of(geometry: dict) -> list[list[list[list[float]]]]:
    """Normalise Polygon / MultiPolygon into a list of ring-lists."""
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def lines_of(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def write_glb(path, attributes: dict, indices, mode: int) -> int:
    """Minimal glTF 2.0 binary. mode 4 = triangles, 1 = lines."""
    blobs, accessors, views = [], [], []
    offset = 0

    def add(data: bytes, target: int) -> int:
        nonlocal offset
        padded = data + b"\x00" * (-len(data) % 4)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data),
                      "target": target})
        blobs.append(padded)
        offset += len(padded)
        return len(views) - 1

    attribute_map = {}
    for name, array in attributes.items():
        array = np.asarray(array, dtype=np.float32)
        view = add(array.tobytes(), 34962)
        accessor = {
            "bufferView": view,
            "componentType": 5126,
            "count": len(array),
            "type": "VEC3" if array.ndim == 2 and array.shape[1] == 3 else "SCALAR",
        }
        if name == "POSITION":
            accessor["min"] = array.min(axis=0).tolist()
            accessor["max"] = array.max(axis=0).tolist()
        accessors.append(accessor)
        attribute_map[name] = len(accessors) - 1

    indices = np.asarray(indices, dtype=np.uint32)
    view = add(indices.tobytes(), 34963)
    accessors.append({"bufferView": view, "componentType": 5125,
                      "count": len(indices), "type": "SCALAR"})

    buffer = b"".join(blobs)
    gltf = {
        "asset": {"version": "2.0", "generator": "mytestdrive/build_roads"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attribute_map,
                                    "indices": len(accessors) - 1, "mode": mode}]}],
        "buffers": [{"byteLength": len(buffer)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode()
    json_bytes += b"\x20" * (-len(json_bytes) % 4)
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes) + 8 + len(buffer))
    path.write_bytes(
        header
        + struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
        + struct.pack("<II", len(buffer), 0x004E4942) + buffer
    )
    return path.stat().st_size


def build_surfaces(sample, ax, ay, out_dir) -> dict:
    data = json.loads((RAW / "wfs" / "cm_fahrbahn.geojson").read_text())
    chunks = defaultdict(lambda: {"pos": [], "mat": [], "idx": []})
    skipped = 0

    for feature in data["features"]:
        try:
            material = float(feature["properties"].get("material") or 0)
        except ValueError:
            material = 0.0
        for polygon in rings_of(feature["geometry"]):
            rings = [np.array(r, dtype=np.float64)[:, :2] for r in polygon if len(r) >= 4]
            if not rings:
                continue
            rings = [r[:-1] if np.allclose(r[0], r[-1]) else r for r in rings]
            rings = [r for r in rings if len(r) >= 3]
            if not rings:
                continue
            flat = np.concatenate(rings)
            flat = flat - np.array([ax, ay])
            ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
            try:
                tri = triangulate_float64(flat, ends)
            except Exception:
                skipped += 1
                continue
            if len(tri) < 3:
                skipped += 1
                continue

            z = sample(flat) + ROAD_LIFT_M
            centre = flat.mean(axis=0)
            key = (int(centre[0] // CHUNK_M), int(centre[1] // CHUNK_M))
            chunk = chunks[key]
            base = len(chunk["pos"])
            for (x, y), zz in zip(flat, z):
                chunk["pos"].append((x, y, zz))
                chunk["mat"].append(material)
            chunk["idx"].extend(int(base + i) for i in tri)

    manifest = []
    total = 0
    for (cx, cy), chunk in sorted(chunks.items()):
        name = f"road_{cx}_{cy}.glb"
        size = write_glb(out_dir / name,
                         {"POSITION": np.array(chunk["pos"], dtype=np.float32),
                          "_MATERIAL": np.array(chunk["mat"], dtype=np.float32)},
                         chunk["idx"], 4)
        total += size
        manifest.append({"file": name, "cell": [cx, cy],
                         "vertices": len(chunk["pos"]),
                         "triangles": len(chunk["idx"]) // 3, "bytes": size})
    return {"chunks": manifest, "bytes": total, "skipped": skipped}


def build_markings(sample, ax, ay, out_dir) -> dict:
    data = json.loads((RAW / "wfs" / "be_fahrbahnmarkierunglinie.geojson").read_text())
    chunks = defaultdict(lambda: {"pos": [], "idx": []})

    for feature in data["features"]:
        for line in lines_of(feature["geometry"]):
            points = np.array(line, dtype=np.float64)[:, :2] - np.array([ax, ay])
            if len(points) < 2:
                continue
            z = sample(points) + MARKING_LIFT_M
            centre = points.mean(axis=0)
            key = (int(centre[0] // CHUNK_M), int(centre[1] // CHUNK_M))
            chunk = chunks[key]
            base = len(chunk["pos"])
            for (x, y), zz in zip(points, z):
                chunk["pos"].append((x, y, zz))
            for i in range(len(points) - 1):
                chunk["idx"].extend((base + i, base + i + 1))

    manifest = []
    total = 0
    for (cx, cy), chunk in sorted(chunks.items()):
        name = f"marking_{cx}_{cy}.glb"
        size = write_glb(out_dir / name,
                         {"POSITION": np.array(chunk["pos"], dtype=np.float32)},
                         chunk["idx"], 1)
        total += size
        manifest.append({"file": name, "cell": [cx, cy],
                         "vertices": len(chunk["pos"]),
                         "segments": len(chunk["idx"]) // 2, "bytes": size})
    return {"chunks": manifest, "bytes": total}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()

    ax, ay = anchor_utm33()
    out_dir = BUILD / "roads"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("building height sampler from LoD2 ground surfaces")
    sample = height_sampler()

    print("triangulating roadway surfaces (cm_fahrbahn)")
    surfaces = build_surfaces(sample, ax, ay, out_dir)

    print("building lane markings (be_fahrbahnmarkierunglinie)")
    markings = build_markings(sample, ax, ay, out_dir)

    (out_dir / "manifest.json").write_text(json.dumps(
        {"chunk_m": CHUNK_M, "surfaces": surfaces["chunks"], "markings": markings["chunks"]}
    ))

    tris = sum(c["triangles"] for c in surfaces["chunks"])
    segs = sum(c["segments"] for c in markings["chunks"])
    print("\n--- built ---")
    print(f"road chunks     {len(surfaces['chunks']):>10,}")
    print(f"road triangles  {tris:>10,}")
    print(f"road glb        {surfaces['bytes'] / 1_048_576:>10,.1f} MB")
    print(f"untriangulable  {surfaces['skipped']:>10,}")
    print(f"marking chunks  {len(markings['chunks']):>10,}")
    print(f"marking segments{segs:>10,}")
    print(f"marking glb     {markings['bytes'] / 1_048_576:>10,.1f} MB")


if __name__ == "__main__":
    main()
