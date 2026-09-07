"""Phase 2a — stream-parse the LoD2 CityGML tiles into a compact intermediate.

The archives are CityGML 1.0 and total 1,572 MB uncompressed, so this reads
with lxml's iterparse and clears each building as it goes; nothing larger than
one building is ever held. Output is one gzipped JSON per tile under
data/build/parsed/, so re-runs skip the parse entirely.

Real roof geometry is preserved by keeping the bldg:boundedBy surfaces
(RoofSurface / WallSurface / GroundSurface) as actual polygons. Flattening
these to footprint+height would be Path B, which was explicitly rejected.

Note the tile archives contain .xml members, not .gml.
"""

import argparse
import gzip
import json
import zipfile

from lxml import etree

from config import BUILD, RAW

NS = {
    "bldg": "http://www.opengis.net/citygml/building/1.0",
    "gml": "http://www.opengis.net/gml",
    "core": "http://www.opengis.net/citygml/1.0",
}
BUILDING_TAG = f"{{{NS['bldg']}}}Building"
SURFACE_KINDS = {"RoofSurface": "roof", "WallSurface": "wall", "GroundSurface": "ground"}


def local_name(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def polygons_of(element) -> list[dict]:
    """Every gml:Polygon under this element, as exterior ring plus any holes."""
    result = []
    for polygon in element.iter(f"{{{NS['gml']}}}Polygon"):
        rings = {"exterior": None, "interior": []}
        for boundary in polygon:
            kind = local_name(boundary.tag)
            if kind not in ("exterior", "interior"):
                continue
            pos_list = boundary.find(f".//{{{NS['gml']}}}posList")
            if pos_list is None or not pos_list.text:
                continue
            values = [float(v) for v in pos_list.text.split()]
            # srsDimension is 3 throughout this dataset: X Y Z in UTM33.
            points = [values[i : i + 3] for i in range(0, len(values) - 2, 3)]
            if len(points) < 4:
                continue
            if kind == "exterior":
                rings["exterior"] = points
            else:
                rings["interior"].append(points)
        if rings["exterior"]:
            result.append(rings)
    return result


def text_of(building, path: str) -> str | None:
    node = building.find(path, NS)
    return node.text.strip() if node is not None and node.text else None


def parse_building(building) -> dict | None:
    surfaces: dict[str, list] = {}
    # Descend rather than taking direct boundedBy children: a Building that
    # consists of BuildingParts carries its geometry inside those parts, and
    # 1 in 5 buildings here does. Taking direct children only drops them all.
    for tag, kind in SURFACE_KINDS.items():
        for bounded in building.iter(f"{{{NS['bldg']}}}{tag}"):
            polygons = polygons_of(bounded)
            if polygons:
                surfaces.setdefault(kind, []).extend(polygons)

    if not surfaces:
        return None

    parts = sum(1 for _ in building.iter(f"{{{NS['bldg']}}}BuildingPart"))

    height = text_of(building, "bldg:measuredHeight")
    return {
        "id": building.get(f"{{{NS['gml']}}}id"),
        "roof_type": text_of(building, "bldg:roofType"),
        "function": text_of(building, "bldg:function"),
        "height": float(height) if height else None,
        "parts": parts,
        "surfaces": surfaces,
    }


def parse_tile(zip_path, force: bool = False) -> tuple[int, int, int]:
    out_path = BUILD / "parsed" / f"{zip_path.stem}.json.gz"
    if out_path.exists() and not force:
        with gzip.open(out_path, "rt") as handle:
            data = json.load(handle)
        return len(data["buildings"]), data.get("skipped", 0), out_path.stat().st_size

    out_path.parent.mkdir(parents=True, exist_ok=True)
    buildings = []
    skipped = 0

    with zipfile.ZipFile(zip_path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith((".xml", ".gml"))]
        for member in members:
            with archive.open(member) as handle:
                context = etree.iterparse(handle, events=("end",), tag=BUILDING_TAG)
                for _, element in context:
                    parsed = parse_building(element)
                    if parsed:
                        buildings.append(parsed)
                    else:
                        skipped += 1
                    # Release the building and everything already walked past it.
                    element.clear()
                    while element.getprevious() is not None:
                        del element.getparent()[0]
                del context

    with gzip.open(out_path, "wt") as handle:
        json.dump({"tile": zip_path.stem, "skipped": skipped, "buildings": buildings}, handle)
    return len(buildings), skipped, out_path.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, help="parse only the first N tiles")
    args = parser.parse_args()

    tiles = sorted((RAW / "lod2").glob("LoD2_*.zip"))
    if args.limit:
        tiles = tiles[: args.limit]

    total_buildings = 0
    total_skipped = 0
    total_bytes = 0
    for i, tile in enumerate(tiles, 1):
        count, skipped, size = parse_tile(tile, args.force)
        total_buildings += count
        total_skipped += skipped
        total_bytes += size
        if i % 20 == 0 or i == len(tiles):
            print(f"  {i}/{len(tiles)} tiles, {total_buildings:,} buildings, "
                  f"{total_bytes / 1_048_576:.0f} MB intermediate")

    print(f"\nbuildings parsed:  {total_buildings:,}   (Phase 1 counted 69,538)")
    print(f"skipped, no geometry: {total_skipped:,}")
    print(f"accounted for:     {total_buildings + total_skipped:,}")
    print(f"intermediate size: {total_bytes / 1_048_576:.1f} MB gzipped")


if __name__ == "__main__":
    main()
