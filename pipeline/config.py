from pathlib import Path

# TÜV Rheinland Prüfstelle Berlin-Spandau, Pichelswerderstraße 9.
# Geocoded via Nominatim; every query in the project is centred here.
ANCHOR_LAT = 52.5304357
ANCHOR_LON = 13.2144591
RADIUS_M = 5000

# LoD2 tiles are ETRS89 / UTM33N on a 1km grid; WFS and OSM speak WGS84.
CRS_WGS84 = "EPSG:4326"
CRS_UTM33 = "EPSG:25833"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

WFS_STRASSENBEFAHRUNG = "https://gdi.berlin.de/services/wfs/strassenbefahrung"
LOD2_ATOM_SUBFEED = "https://gdi.berlin.de/data/a_lod2/atom/0.atom"

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BUILD = ROOT / "data" / "build"
DOCS = ROOT / "docs"

# The drivable-highway filter recorded in the vault as the query behind the
# Spandau street table. Reused verbatim so counts stay comparable to it.
DRIVABLE_HIGHWAY_RE = (
    "^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street)"
)

# Everything the above excludes but that still carries traffic. Counted separately
# to explain the gap between the recorded 2.2km figures (1,569 vs 3,402 ways).
EXTRA_HIGHWAY_RE = (
    "^(motorway_link|trunk_link|primary_link|secondary_link|tertiary_link|service|"
    "pedestrian|track|road|busway)"
)
