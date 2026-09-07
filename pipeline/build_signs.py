"""Build chunked sign meshes from Berlin's surveyed traffic-sign points."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from build_roads import anchor_utm33, height_sampler, write_glb
from config import BUILD, RAW

CHUNK_M = 500
PLATE_Z = 2.15
PLATE_SIZE = 0.58
POLE_HEIGHT = 2.2
POLE_WIDTH = 0.045


def code_value(raw: str) -> float:
    try:
        return float(str(raw).split(",")[0].strip().replace("e", ""))
    except (TypeError, ValueError):
        return 0.0


def add_box(chunk, x, y, z, width, depth, height, code):
    base = len(chunk["pos"])
    vertices = [
        (x - width / 2, y - depth / 2, z), (x + width / 2, y - depth / 2, z),
        (x + width / 2, y + depth / 2, z), (x - width / 2, y + depth / 2, z),
        (x - width / 2, y - depth / 2, z + height), (x + width / 2, y - depth / 2, z + height),
        (x + width / 2, y + depth / 2, z + height), (x - width / 2, y + depth / 2, z + height),
    ]
    chunk["pos"].extend(vertices)
    chunk["code"].extend([code] * len(vertices))
    chunk["idx"].extend([
        base, base + 1, base + 5, base, base + 5, base + 4,
        base + 1, base + 2, base + 6, base + 1, base + 6, base + 5,
        base + 2, base + 3, base + 7, base + 2, base + 7, base + 6,
        base + 3, base, base + 4, base + 3, base + 4, base + 7,
        base + 4, base + 5, base + 6, base + 4, base + 6, base + 7,
    ])


def add_plate(chunk, x, y, z, code):
    base = len(chunk["pos"])
    half = PLATE_SIZE / 2
    # A simple vertical sign face. The surveyed code drives its renderer colour;
    # keeping the plate geometry separate makes later Wikimedia face replacement local.
    chunk["pos"].extend([
        (x - half, y, z), (x + half, y, z),
        (x + half, y, z + PLATE_SIZE), (x - half, y, z + PLATE_SIZE),
    ])
    chunk["code"].extend([code] * 4)
    chunk["idx"].extend([base, base + 1, base + 2, base, base + 2, base + 3])


def build() -> dict:
    ax, ay = anchor_utm33()
    sample = height_sampler()
    source = json.loads((RAW / "wfs" / "aa_verkehrszeichen.geojson").read_text())
    chunks = defaultdict(lambda: {"pos": [], "code": [], "idx": []})
    skipped = 0
    for feature in source["features"]:
        coordinates = feature.get("geometry", {}).get("coordinates")
        if not coordinates or len(coordinates) < 2:
            skipped += 1
            continue
        point = np.asarray(coordinates[:2], dtype=np.float64) - np.array([ax, ay])
        key = (int(point[0] // CHUNK_M), int(point[1] // CHUNK_M))
        ground = float(sample(point.reshape(1, 2))[0])
        code = code_value(feature.get("properties", {}).get("vkz_zeiche"))
        chunk = chunks[key]
        add_box(chunk, point[0], point[1], ground, POLE_WIDTH, POLE_WIDTH, POLE_HEIGHT, code)
        add_plate(chunk, point[0], point[1], ground + PLATE_Z, code)

    out_dir = BUILD / "signs"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    total = 0
    for (cx, cy), chunk in sorted(chunks.items()):
        name = f"sign_{cx}_{cy}.glb"
        size = write_glb(out_dir / name, {
            "POSITION": np.asarray(chunk["pos"], dtype=np.float32),
            "_SIGN": np.asarray(chunk["code"], dtype=np.float32),
        }, chunk["idx"], 4)
        total += size
        manifest.append({"file": name, "cell": [cx, cy], "signs": len(chunk["code"]) // 12, "bytes": size})
    manifest_data = {"chunk_m": CHUNK_M, "signs": manifest, "count": len(source["features"]) - skipped, "bytes": total}
    (out_dir / "manifest.json").write_text(json.dumps(manifest_data))
    return manifest_data


def main() -> None:
    argparse.ArgumentParser().parse_args()
    result = build()
    print(f"sign chunks {len(result['signs']):,}")
    print(f"signs       {result['count']:,}")
    print(f"sign glb     {result['bytes'] / 1_048_576:,.1f} MB")


if __name__ == "__main__":
    main()
