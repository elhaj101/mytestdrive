import json
import math

import requests

from config import ANCHOR_LAT, ANCHOR_LON, RADIUS_M, RAW, WFS_STRASSENBEFAHRUNG

PAGE_SIZE = 5000
HEADERS = {"User-Agent": "mytestdrive/0.1 (personal driving-exam prep project)"}


def bbox_4326() -> str:
    """Bbox around the anchor in lat,lon order.

    WFS 2.0 with urn:ogc:def:crs:EPSG::4326 wants lat,lon. Passing lon,lat
    returns numberMatched=0 with HTTP 200 and no error at all. Verified
    2026-09-07: lat,lon -> 16,342 signs, lon,lat -> 0.
    """
    dlat = RADIUS_M / 111320.0
    dlon = RADIUS_M / (111320.0 * math.cos(math.radians(ANCHOR_LAT)))
    south, west = ANCHOR_LAT - dlat, ANCHOR_LON - dlon
    north, east = ANCHOR_LAT + dlat, ANCHOR_LON + dlon
    return f"{south},{west},{north},{east},urn:ogc:def:crs:EPSG::4326"


def _request(layer: str, **extra) -> requests.Response:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": f"strassenbefahrung:{layer}",
        "BBOX": bbox_4326(),
        **extra,
    }
    response = requests.get(
        WFS_STRASSENBEFAHRUNG, params=params, headers=HEADERS, timeout=300
    )
    response.raise_for_status()
    return response


def hits(layer: str) -> int:
    import re

    text = _request(layer, RESULTTYPE="hits").text
    match = re.search(r'numberMatched="(\d+)"', text)
    if not match:
        raise RuntimeError(f"{layer}: no numberMatched in response: {text[:300]}")
    return int(match.group(1))


def fetch(layer: str, force: bool = False) -> dict:
    """Fetch a whole layer as GeoJSON, paging through the result set."""
    cache_path = RAW / "wfs" / f"{layer}.geojson"
    if cache_path.exists() and not force:
        print(f"  cached: {layer}")
        return json.loads(cache_path.read_text())

    cache_path.parent.mkdir(parents=True, exist_ok=True)

    expected = hits(layer)
    # A silent zero here means the bbox axis order is wrong, not that the area is empty.
    if expected == 0:
        raise RuntimeError(
            f"{layer}: numberMatched=0. Check bbox axis order (must be lat,lon for EPSG::4326)."
        )

    features: list[dict] = []
    while len(features) < expected:
        response = _request(
            layer,
            OUTPUTFORMAT="application/json",
            COUNT=str(PAGE_SIZE),
            STARTINDEX=str(len(features)),
        )
        page = response.json().get("features", [])
        if not page:
            break
        features.extend(page)
        print(f"    {layer}: {len(features):,}/{expected:,}")

    if len(features) != expected:
        print(f"  WARNING {layer}: got {len(features):,}, expected {expected:,}")

    payload = {"type": "FeatureCollection", "features": features}
    cache_path.write_text(json.dumps(payload))
    size_mb = cache_path.stat().st_size / 1_048_576
    print(f"  {layer}: {len(features):,} features ({size_mb:.1f} MB)")
    return payload
