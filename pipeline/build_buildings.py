"""Phase 2b — parsed CityGML surfaces into per-chunk merged glTF meshes.

Two requirements pull against each other here. Path A wants buildings to stay
discrete and addressable; 69,538 separate meshes would mean 69,538 draw calls
and no 60fps. The resolution is to merge per chunk and carry the building index
as a per-vertex attribute (_BUILDING), so a single building is still selectable
in a shader for highlight or fade while costing one draw call per chunk.

Polygons are triangulated in their own plane (they are planar by construction),
so real roof shape survives. Flattening to footprint+height would be Path B.

Vertices are emitted in a local metric frame centred on the anchor: raw UTM33
northings are ~5.8M and lose float precision at render time.
"""

import argparse
import gzip
import json
import math
import struct
from collections import defaultdict

import numpy as np
from mapbox_earcut import triangulate_float64
from pyproj import Transformer

from config import ANCHOR_LAT, ANCHOR_LON, BUILD, CRS_UTM33, CRS_WGS84, scaled_elevation

CHUNK_M = 500
QUANTISE = 100.0  # vertex dedup grid, 1cm


def anchor_utm33() -> tuple[float, float]:
    transformer = Transformer.from_crs(CRS_WGS84, CRS_UTM33, always_xy=True)
    return transformer.transform(ANCHOR_LON, ANCHOR_LAT)


def plane_basis(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Orthonormal basis for the polygon's own plane, via Newell's method."""
    normal = np.zeros(3)
    for i in range(len(points)):
        current, following = points[i], points[(i + 1) % len(points)]
        normal[0] += (current[1] - following[1]) * (current[2] + following[2])
        normal[1] += (current[2] - following[2]) * (current[0] + following[0])
        normal[2] += (current[0] - following[0]) * (current[1] + following[1])
    length = np.linalg.norm(normal)
    if length < 1e-12:
        return None, None, None
    normal /= length
    reference = np.array([0.0, 0.0, 1.0]) if abs(normal[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(normal, reference)
    u /= np.linalg.norm(u)
    return normal, u, np.cross(normal, u)


def triangulate(polygon: dict) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Triangulate one planar polygon (with holes) into 3D triangles."""
    exterior = np.array(polygon["exterior"][:-1], dtype=np.float64)
    if len(exterior) < 3:
        return []
    interiors = [np.array(h[:-1], dtype=np.float64) for h in polygon["interior"]]
    interiors = [h for h in interiors if len(h) >= 3]

    normal, u, v = plane_basis(exterior)
    if normal is None:
        return []

    rings = [exterior] + interiors
    origin = exterior[0]
    flat = np.concatenate(rings)
    relative = flat - origin
    projected = np.column_stack((relative @ u, relative @ v))

    ring_ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    try:
        indices = triangulate_float64(projected, ring_ends)
    except Exception:
        return []

    return [
        (flat[indices[i]], flat[indices[i + 1]], flat[indices[i + 2]])
        for i in range(0, len(indices) - 2, 3)
    ]


def write_glb(path, positions: np.ndarray, building_ids: np.ndarray, indices: np.ndarray) -> int:
    """Minimal glTF 2.0 binary: POSITION + _BUILDING + indices, one mesh."""
    pos_bytes = positions.astype(np.float32).tobytes()
    id_bytes = building_ids.astype(np.float32).tobytes()
    idx_bytes = indices.astype(np.uint32).tobytes()

    def pad(data: bytes, fill: bytes = b"\x00") -> bytes:
        return data + fill * (-len(data) % 4)

    buffer = pad(pos_bytes) + pad(id_bytes) + pad(idx_bytes)
    pos_off = 0
    id_off = len(pad(pos_bytes))
    idx_off = id_off + len(pad(id_bytes))

    gltf = {
        "asset": {"version": "2.0", "generator": "mytestdrive/build_buildings"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0, "_BUILDING": 1}, "indices": 2}]}
        ],
        "buffers": [{"byteLength": len(buffer)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": pos_off, "byteLength": len(pos_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": id_off, "byteLength": len(id_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": idx_off, "byteLength": len(idx_bytes), "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
                "min": positions.min(axis=0).tolist(),
                "max": positions.max(axis=0).tolist(),
            },
            {"bufferView": 1, "componentType": 5126, "count": len(building_ids), "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
    }

    # The glTF spec requires the JSON chunk be padded with spaces, the BIN chunk with zeros.
    json_bytes = pad(json.dumps(gltf, separators=(",", ":")).encode(), b"\x20")
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes) + 8 + len(buffer))
    chunks = (
        struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
        + struct.pack("<II", len(buffer), 0x004E4942) + buffer
    )
    path.write_bytes(header + chunks)
    return path.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    ax, ay = anchor_utm33()
    out_dir = BUILD / "buildings"
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks: dict[tuple[int, int], dict] = defaultdict(
        lambda: {"verts": {}, "positions": [], "ids": [], "indices": [], "buildings": []}
    )

    tiles = sorted((BUILD / "parsed").glob("*.json.gz"))
    if args.limit:
        tiles = tiles[: args.limit]

    degenerate = 0
    for n, tile_path in enumerate(tiles, 1):
        data = json.load(gzip.open(tile_path, "rt"))
        for building in data["buildings"]:
            triangles = []
            for kind, polygons in building["surfaces"].items():
                for polygon in polygons:
                    triangles.extend(triangulate(polygon))
            if not triangles:
                degenerate += 1
                continue

            centroid = np.mean([t[0] for t in triangles], axis=0)
            key = (int((centroid[0] - ax) // CHUNK_M), int((centroid[1] - ay) // CHUNK_M))
            chunk = chunks[key]
            local_index = len(chunk["buildings"])
            chunk["buildings"].append(
                {
                    "id": building["id"],
                    "roof_type": building["roof_type"],
                    "height": building["height"],
                    "parts": building.get("parts", 0),
                }
            )

            for triangle in triangles:
                for point in triangle:
                    x, y, z = point[0] - ax, point[1] - ay, scaled_elevation(point[2])
                    vkey = (round(x * QUANTISE), round(y * QUANTISE), round(z * QUANTISE),
                            local_index)
                    existing = chunk["verts"].get(vkey)
                    if existing is None:
                        existing = len(chunk["positions"])
                        chunk["verts"][vkey] = existing
                        chunk["positions"].append((x, y, z))
                        chunk["ids"].append(local_index)
                    chunk["indices"].append(existing)

        if n % 20 == 0 or n == len(tiles):
            print(f"  {n}/{len(tiles)} tiles, {len(chunks)} chunks")

    manifest = []
    total_bytes = total_verts = total_tris = 0
    for (cx, cy), chunk in sorted(chunks.items()):
        positions = np.array(chunk["positions"], dtype=np.float32)
        ids = np.array(chunk["ids"], dtype=np.float32)
        indices = np.array(chunk["indices"], dtype=np.uint32)
        name = f"chunk_{cx}_{cy}.glb"
        size = write_glb(out_dir / name, positions, ids, indices)
        total_bytes += size
        total_verts += len(positions)
        total_tris += len(indices) // 3
        manifest.append(
            {
                "file": name,
                "cell": [cx, cy],
                "origin_m": [cx * CHUNK_M, cy * CHUNK_M],
                "buildings": chunk["buildings"],
                "vertices": len(positions),
                "triangles": len(indices) // 3,
                "bytes": size,
            }
        )

    (out_dir / "manifest.json").write_text(json.dumps({"chunk_m": CHUNK_M, "chunks": manifest}))

    building_total = sum(len(c["buildings"]) for c in chunks.values())
    print(f"\n--- built ---")
    print(f"chunks          {len(chunks):>10,}  ({CHUNK_M}m grid)")
    print(f"buildings       {building_total:>10,}  (parsed 69,538)")
    print(f"degenerate      {degenerate:>10,}")
    print(f"vertices        {total_verts:>10,}")
    print(f"triangles       {total_tris:>10,}")
    print(f"total glb       {total_bytes / 1_048_576:>10,.1f} MB")
    print(f"mean per chunk  {building_total / len(chunks):>10,.0f} buildings, "
          f"{total_bytes / len(chunks) / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
