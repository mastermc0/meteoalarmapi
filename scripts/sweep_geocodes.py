"""Check point matching against MeteoAlarm's live geocodes file and feeds.

Run by hand before a release and when MeteoAlarm publishes a new geocodes
file; it needs the network, so it isn't part of the tests:

    python scripts/sweep_geocodes.py [geocodes.json]

Without a path it downloads the newest file (33 MB). It reports:
- areas: whether a point inside each area resolves to that area. Nested
  areas (such as Lithuania's municipalities in counties) show as overlaps.
- feeds: for each country, the codes its feed uses, any EMMA_IDs missing from
  the file, and an end-to-end get_alerts() for one warned area with no polygon.

It exits with 1 when an area or a warned area can't be matched by a point.
"""

import collections
import os
import sys
import time

import xmltodict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meteoalarmapi import Meteoalert
from meteoalarmapi.meteoalert import (
    GEOCODES_FILE,
    _get,
    iter_geocodes,
    latest_geocodes_name,
    point_in_geometry,
)

COUNTRIES = [
    "andorra", "austria", "belgium", "bosnia-herzegovina", "bulgaria", "croatia", "cyprus",
    "czechia", "denmark", "estonia", "finland", "france", "germany", "greece", "hungary",
    "iceland", "ireland", "israel", "italy", "latvia", "lithuania", "luxembourg", "malta",
    "moldova", "montenegro", "netherlands", "norway", "poland", "portugal",
    "republic-of-north-macedonia", "romania", "serbia", "slovakia", "slovenia", "spain",
    "sweden", "switzerland", "ukraine", "united-kingdom",
]


def polygons(geometry):
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"]]
    if geometry.get("type") == "MultiPolygon":
        return geometry["coordinates"]
    return []


def bounds(geometry):
    points = [p for rings in polygons(geometry) for ring in rings for p in ring]
    lats, lons = [p[1] for p in points], [p[0] for p in points]
    return min(lats), max(lats), min(lons), max(lons)


def interior_point(geometry):
    """A point inside the geometry, well away from its edges: the middle of the
    widest inside stretch of a few horizontal lines across its largest part"""
    rings = max(polygons(geometry), key=lambda part: len(part[0]))
    lats = [p[1] for p in rings[0]]
    for fraction in (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9):
        lat = min(lats) + (max(lats) - min(lats)) * fraction
        crossings = sorted(
            lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            for ring in rings
            for (lon1, lat1, *_), (lon2, lat2, *_) in zip(ring, ring[1:] + ring[:1])
            if (lat1 > lat) != (lat2 > lat)
        )
        stretches = sorted(zip(crossings[::2], crossings[1::2]), key=lambda s: s[0] - s[1])
        for west, east in stretches:
            point = (lat, (west + east) / 2)
            if point_in_geometry(point, geometry):
                return point
    return None


def areas_at(point, areas):
    lat, lon = point
    return [code for code, (_, geometry, (south, north, west, east)) in areas.items()
            if south <= lat <= north and west <= lon <= east
            and point_in_geometry(point, geometry)]


def load(path):
    if path:
        with open(path, encoding="utf-8") as geocodes_file:
            return path, geocodes_file.read()
    name = latest_geocodes_name()
    print(f"downloading {name}")
    return name, _get(GEOCODES_FILE.format(ref="master", name=name)).content.decode("utf-8")


def sweep_areas(areas):
    started = time.perf_counter()
    exact, overlaps, failures = 0, collections.Counter(), []
    for code, (_, geometry, _) in areas.items():
        point = interior_point(geometry)
        found = areas_at(point, areas) if point else []
        if found == [code]:
            exact += 1
        elif code in found:
            overlaps[code[:2]] += 1
        else:
            failures.append((code, point, found))
    print(f"areas: {exact} exact, {sum(overlaps.values())} overlapping {dict(overlaps)}, "
          f"{len(failures)} failed ({time.perf_counter() - started:.0f}s)")
    for failure in failures[:10]:
        print("  failed:", failure)
    return failures


def sweep_feed(country, areas):
    """One line about the country's feed, and whether a warned area failed"""
    entries = (xmltodict.parse(_get(Meteoalert.endpoint.format(country)).content)
               .get("feed") or {}).get("entry") or []
    if not isinstance(entries, list):
        entries = [entries]
    kinds = collections.Counter()
    for entry in entries:
        geocodes = entry.get("cap:geocode")
        for geocode in geocodes if isinstance(geocodes, list) else [geocodes]:
            if isinstance(geocode, dict):
                kinds[geocode.get("valueName")] += 1
    with_polygon = sum(bool(entry.get("cap:polygon")) for entry in entries)
    codes = set().union(*(Meteoalert.emma_ids(entry) for entry in entries))
    missing = sorted(code for code in codes if code not in areas)

    line = (f"{country:27} entries={len(entries):3} polygons={with_polygon:3} "
            f"codes={dict(kinds)} missing from file={missing}")
    warned = sorted(code for code in codes - set(missing) if any(
        code in Meteoalert.emma_ids(entry) and not entry.get("cap:polygon") for entry in entries))
    if not warned:
        return line, False

    point = interior_point(areas[warned[0]][1])
    found = areas_at(point, areas)
    countries = {areas[code][0] for code in found}
    known = sorted(code for code, (area_country, *_) in areas.items() if area_country in countries)
    meteo = Meteoalert(country, latitude=point[0], longitude=point[1],
                       geocodes={"codes": found, "known": known})
    alerts = meteo.get_alerts()
    line += f" | {warned[0]}: {len(alerts)} alert(s), outdated={meteo.geocodes_outdated}"
    return line, not alerts


def main(path=None):
    name, text = load(path)
    started = time.perf_counter()
    areas = {}
    for feature in iter_geocodes(text):
        properties, geometry = feature.get("properties") or {}, feature.get("geometry") or {}
        if properties.get("type") == "EMMA_ID" and polygons(geometry):
            areas[properties["code"]] = (properties.get("country"), geometry, bounds(geometry))
    print(f"{name}: {len(areas)} areas, read in {time.perf_counter() - started:.1f}s")

    failed = bool(sweep_areas(areas))
    print()
    for country in COUNTRIES:
        line, feed_failed = sweep_feed(country, areas)
        print(line)
        failed |= feed_failed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
