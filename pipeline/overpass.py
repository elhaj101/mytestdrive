import json
import time

import requests

from config import OVERPASS_ENDPOINTS, RAW


# Overpass answers 406 to the default python-requests user agent. Verified 2026-09-07.
HEADERS = {"User-Agent": "mytestdrive/0.1 (personal driving-exam prep project)"}


def _post(endpoint: str, query: str, timeout: int) -> requests.Response:
    return requests.post(endpoint, data={"data": query}, headers=HEADERS, timeout=timeout)


def run(query: str, cache_name: str, force: bool = False, timeout: int = 300) -> dict:
    """Run an Overpass query, caching the raw response under data/raw/osm/."""
    cache_path = RAW / "osm" / f"{cache_name}.json"
    if cache_path.exists() and not force:
        print(f"  cached: {cache_path.relative_to(RAW.parent.parent)}")
        return json.loads(cache_path.read_text())

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = None

    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(3):
            try:
                response = _post(endpoint, query, timeout)
            except requests.RequestException as exc:
                last_error = f"{endpoint}: {exc}"
                break

            # The public endpoint rate-limits aggressively; this is expected, not fatal.
            if response.status_code in (429, 502, 503, 504):
                wait = 60 * (attempt + 1)
                print(f"  {endpoint} returned {response.status_code}, waiting {wait}s")
                time.sleep(wait)
                continue

            if response.status_code != 200:
                last_error = f"{endpoint}: HTTP {response.status_code}"
                break

            payload = response.json()
            cache_path.write_text(json.dumps(payload))
            size_mb = cache_path.stat().st_size / 1_048_576
            print(f"  fetched from {endpoint} -> {cache_path.name} ({size_mb:.1f} MB)")
            return payload

        print(f"  giving up on {endpoint}, trying next")

    raise RuntimeError(f"Overpass query '{cache_name}' failed. Last error: {last_error}")
