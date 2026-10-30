"""Weather alerts for a European region from MeteoAlarm.org.

Reads the country's legacy Atom feed, keeps the entries whose area matches
the region (by name or EMMA_ID code) and fetches each one's CAP document.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import requests
import xmltodict


class WrongCountry(Exception):
    pass


class ServerError(Exception):
    pass


class UnexpectedError(Exception):
    pass


# CAP severities, least to most severe
SEVERITIES = ["Unknown", "Minor", "Moderate", "Severe", "Extreme"]
TIMEOUT = 10


def _utcnow():
    # A function so tests can patch the clock
    return datetime.now(timezone.utc)


def parse_polygon(text):
    """CAP polygon text ("lat,lon lat,lon ...") as a list of (lat, lon)"""
    points = []
    for pair in (text or "").split():
        try:
            lat, lon = pair.split(",")
            points.append((float(lat), float(lon)))
        except ValueError:
            continue
    return points


def point_in_polygon(point, polygon):
    """Ray casting on lat/lon, treated as flat: fine for warning-sized areas"""
    if len(polygon) < 3:
        return False
    lat, lon = point
    inside = False
    for (lat1, lon1), (lat2, lon2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (lat1 > lat) != (lat2 > lat):
            crossing = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            if lon < crossing:
                inside = not inside
    return inside


def point_in_geometry(point, geometry):
    """Whether (lat, lon) is inside a GeoJSON Polygon or MultiPolygon"""
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        polygons = [coordinates]
    elif geometry.get("type") == "MultiPolygon":
        polygons = coordinates
    else:
        return False
    for rings in polygons:
        # The first ring is the outline and any others are holes, so the
        # point is inside when an odd number of rings contain it
        inside = False
        for ring in rings:
            # GeoJSON positions are [lon, lat]
            if point_in_polygon(point, [(lat, lon) for lon, lat, *_ in ring]):
                inside = not inside
        if inside:
            return True
    return False


# MeteoAlarm publishes the outlines of the EMMA_ID areas on its Redistribution
# Hub, from this GitLab repository. Each version is a new file named by date.
GEOCODES_LISTING = ("https://gitlab.com/api/v4/projects/meteoalarm-pm-group%2Fdocuments"
                    "/repository/tree?per_page=100")
GEOCODES_FILE = "https://gitlab.com/meteoalarm-pm-group/documents/-/raw/{ref}/{name}"
GEOCODES_NAME = re.compile(r"MeteoAlarm_Geocodes_\d{4}_\d{2}_\d{2}\.json")
# A known version pinned to its commit, which stays available even if the
# file is later removed, for when the listing can't be read
GEOCODES_FALLBACK = ("9bdb5f4dbe996ea627037682b6c3f5e96c44225e",
                     "MeteoAlarm_Geocodes_2026_07_31.json")
# How often update_geocodes() looks for a new version: routinely, and when
# get_alerts() has seen an EMMA_ID it doesn't know
GEOCODES_MAX_AGE = timedelta(days=30)
GEOCODES_OUTDATED_MAX_AGE = timedelta(days=1)


def _get(url):
    """The response for url, raising ServerError/UnexpectedError unless it's a 200"""
    try:
        response = requests.get(url, timeout=TIMEOUT)
    except Exception as err:
        raise UnexpectedError(str(err)) from err
    if response.status_code >= 500:
        raise ServerError(f"Server error - status code: {response.status_code}")
    if response.status_code != 200:
        raise UnexpectedError(f"Server returned unexpected status code: {response.status_code}")
    return response


def latest_geocodes_name():
    """The name of MeteoAlarm's newest geocodes file"""
    try:
        names = [item["path"] for item in _get(GEOCODES_LISTING).json()
                 if GEOCODES_NAME.fullmatch(item.get("path") or "")]
    except (ValueError, TypeError, AttributeError, KeyError) as err:
        raise UnexpectedError(f"Unexpected geocodes listing: {err}") from err
    if not names:
        raise UnexpectedError("MeteoAlarm's listing has no geocodes file")
    # The dates are zero-padded, so the newest name sorts last
    return max(names)


def iter_geocodes(text):
    """The features of a geocodes file (a GeoJSON FeatureCollection), one at a
    time, so the 33 MB file is never decoded into memory all at once"""
    match = re.search(r'"features"\s*:\s*\[', text)
    if not match:
        raise UnexpectedError("The geocodes file has no features")
    decoder = json.JSONDecoder()
    index = match.end()
    while True:
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        if index >= len(text) or text[index] == "]":
            return
        try:
            feature, index = decoder.raw_decode(text, index)
        except ValueError as err:
            raise UnexpectedError(f"Unexpected geocodes file: {err}") from err
        yield feature


def resolve_geocodes(latitude, longitude, text, version):
    """The EMMA_ID areas containing a point, from a geocodes file's text.

    Returns the dict that Meteoalert(geocodes=...) and update_geocodes() take:
    the matching codes, every code of the same country (to notice new ones),
    the point, the file version and when it was checked. It's JSON-safe, so
    the caller can store it.
    """
    point = (float(latitude), float(longitude))
    areas = []
    for feature in iter_geocodes(text):
        properties = feature.get("properties") or {}
        if properties.get("type") == "EMMA_ID" and properties.get("code"):
            inside = point_in_geometry(point, feature.get("geometry") or {})
            areas.append((properties["code"], properties.get("country"), inside))
    countries = {country for _, country, inside in areas if inside}
    return {
        "codes": [code for code, _, inside in areas if inside],
        "known": sorted(code for code, country, _ in areas if country in countries),
        "latitude": point[0],
        "longitude": point[1],
        "version": version,
        "checked": _utcnow().isoformat(),
    }


def update_geocodes(latitude, longitude, geocodes=None, outdated=False):
    """The EMMA_ID areas for a point, downloaded only when needed.

    Pass the dict from the last call (or None the first time) and store what
    comes back. Without one, or for a different point, this downloads the
    newest geocodes file (33 MB). Otherwise it checks MeteoAlarm's listing
    (a few KB) once GEOCODES_MAX_AGE has passed, or GEOCODES_OUTDATED_MAX_AGE
    when outdated (Meteoalert.geocodes_outdated), and downloads again only
    when there's a newer file.

    It can take a while and raises ServerError/UnexpectedError when MeteoAlarm
    can't be reached, so call it outside get_alerts() and keep the previous
    dict on an error.
    """
    point = (float(latitude), float(longitude))
    same_point = bool(geocodes) and (geocodes.get("latitude"), geocodes.get("longitude")) == point
    if same_point:
        max_age = GEOCODES_OUTDATED_MAX_AGE if outdated else GEOCODES_MAX_AGE
        try:
            checked = datetime.fromisoformat(geocodes["checked"])
        except (KeyError, TypeError, ValueError):
            checked = None
        if checked is not None and _utcnow() - checked < max_age:
            return geocodes

    ref = "master"
    try:
        version = latest_geocodes_name()
    except (ServerError, UnexpectedError):
        if same_point:
            raise
        # A first setup shouldn't depend on the listing
        ref, version = GEOCODES_FALLBACK

    if same_point and geocodes.get("version") == version:
        return {**geocodes, "checked": _utcnow().isoformat()}
    text = _get(GEOCODES_FILE.format(ref=ref, name=version)).content.decode("utf-8")
    return resolve_geocodes(*point, text, version)


class Meteoalert:

    endpoint = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{0}"

    def __init__(self, country, province=None, language="en-GB",
                 latitude=None, longitude=None, geocodes=None):
        """Alerts for a province (name or EMMA_ID code), a point, or both.

        latitude/longitude match alerts whose area polygon contains the point,
        for countries such as Norway whose feeds have no named provinces. With
        both, an alert matching either is included.

        Most feeds give areas only as EMMA_ID codes, without polygons. To match
        those by point too, pass geocodes from update_geocodes(). After
        get_alerts(), geocodes_outdated is True when the feed had an EMMA_ID
        that geocodes doesn't know, a sign to call update_geocodes() again.
        """
        if (latitude is None) != (longitude is None):
            raise ValueError("latitude and longitude must be given together")
        if province is None and latitude is None and not geocodes:
            raise ValueError("a province or a latitude and longitude is required")
        self.country = country.lower()
        self.province = province
        self.language = language
        self.point = None if latitude is None else (float(latitude), float(longitude))
        self.geocodes = geocodes
        self.geocodes_outdated = False

    def get_alert(self):
        """The most severe alert for the location, or {} when there is none.

        See get_alerts() for the order.
        """
        alerts = self.get_alerts()
        return alerts[0] if alerts else {}

    def get_alerts(self, include_expired=True):
        """Every alert for the location, most severe first.

        Unexpired alerts come first, then the highest awareness level (or CAP
        severity when there is none). Equally ranked alerts keep feed order.

        With include_expired=False, expired alerts are left out. Future
        alerts (not started yet) are kept, so upcoming warnings still show.
        """
        feed = xmltodict.parse(self._fetch(self.endpoint.format(self.country)))
        entries = (feed.get("feed") or {}).get("entry", [])
        if not isinstance(entries, list):
            entries = [entries]

        codes = set((self.geocodes or {}).get("codes") or [])
        known = set((self.geocodes or {}).get("known") or [])
        self.geocodes_outdated = False

        alerts = []
        # The feed has an entry per language and area of an alert, all
        # pointing at the same CAP document, so fetch each one once.
        seen = set()
        for entry in entries:
            emma_ids = self.emma_ids(entry) if self.geocodes else set()
            # An area without a polygon can only be matched by its code, so
            # a code missing from geocodes means they need updating
            if not entry.get("cap:polygon") and not emma_ids <= known:
                self.geocodes_outdated = True
            if not (self.province is not None and self.is_location_match(entry, self.province)
                    or self.point is not None and self.is_point_match(entry, self.point)
                    or emma_ids & codes):
                continue
            links = entry.get("link", [])
            if not isinstance(links, list):
                links = [links]
            cap_url = next((link.get("@href") for link in links
                            if link.get("@type") == "application/cap+xml"), None)
            if cap_url and cap_url not in seen:
                seen.add(cap_url)
                text = self._fetch(cap_url, missing_ok=True)
                # A warning withdrawn between reading the feed and fetching its
                # document is gone, not a sign of a wrong country, so skip it.
                if text is not None:
                    alerts.append(self._parse_cap(text))

        if not include_expired:
            # Some feeds keep warnings for a while after they end
            alerts = [a for a in alerts if not self.is_expired(a.get("expires"))]

        # sorted() is stable, so equally ranked alerts keep feed order
        return sorted(alerts, key=self.alert_rank, reverse=True)

    def _parse_cap(self, text):
        """The CAP <info> in the requested language (or the first one) as a dict,
        plus the message fields needed to track an alert across updates"""
        alert = xmltodict.parse(text).get("alert", {})
        infos = alert.get("info", [])
        if not isinstance(infos, list):
            infos = [infos]
        if not infos:
            return {}
        info = next((i for i in infos if self.language in (i.get("language") or "")),
                    infos[0])

        data = {key: value for key, value in info.items() if isinstance(value, str)}
        parameters = info.get("parameter", [])
        if not isinstance(parameters, list):
            parameters = [parameters]
        for parameter in parameters:
            data[parameter.get("valueName")] = parameter.get("value")

        # Each CAP message has its own identifier; an Update or Cancel lists
        # the messages it replaces in references
        data["identifier"] = alert.get("identifier")
        data["message_type"] = alert.get("msgType")
        data["references"] = alert.get("references")
        data["sent"] = alert.get("sent")
        return data

    def alert_rank(self, data):
        """Sort key for an alert: (not expired, awareness level, severity)"""
        level = re.match(r"\s*(\d+)", data.get("awareness_level") or "")
        severity = data.get("severity")
        return (
            not self.is_expired(data.get("expires")),
            int(level.group(1)) if level else 0,
            SEVERITIES.index(severity) if severity in SEVERITIES else 0,
        )

    @staticmethod
    def is_expired(expires):
        """Whether a CAP time (2024-01-01T12:00:00+01:00) is in the past"""
        try:
            moment = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return False
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment <= _utcnow()

    def is_location_match(self, entry, province):
        # Attempt to match the province first by regex on name
        if re.search(rf"{province}", entry.get("cap:areaDesc") or "", re.IGNORECASE):
            return True

        # then by areaCode
        geocode = entry.get("cap:geocode")
        return isinstance(geocode, dict) and geocode.get("value") == province

    @staticmethod
    def is_point_match(entry, point):
        """Whether (lat, lon) is inside any of the entry's cap:polygon areas"""
        polygons = entry.get("cap:polygon") or []
        if not isinstance(polygons, list):
            polygons = [polygons]
        return any(point_in_polygon(point, parse_polygon(text)) for text in polygons)

    @staticmethod
    def emma_ids(entry):
        """The entry's EMMA_ID codes (feeds also use FIPS and NUTS3 codes)"""
        geocodes = entry.get("cap:geocode") or []
        if not isinstance(geocodes, list):
            geocodes = [geocodes]
        return {geocode.get("value") for geocode in geocodes
                if isinstance(geocode, dict) and geocode.get("valueName") == "EMMA_ID"}

    def _fetch(self, url, missing_ok=False):
        """The response text, or None for a 404/410 when missing_ok"""
        try:
            response = requests.get(url, timeout=TIMEOUT)
        except Exception as err:
            raise UnexpectedError(str(err)) from err
        if missing_ok and response.status_code in (404, 410):
            return None
        self.check_status_code(response.status_code)
        return response.content.decode("utf-8")

    def check_status_code(self, status_code):
        if status_code == 404:
            # MeteoAlarm sometimes briefly returns 404 for valid countries too
            raise WrongCountry(
                f"MeteoAlarm has no feed for country {self.country!r} (404). Check the "
                "name matches feeds.meteoalarm.org; if it does, MeteoAlarm may be "
                "temporarily unavailable"
            )
        elif status_code >= 500:
            raise ServerError(f"Server error - status code: {status_code}")
        elif status_code != 200:
            raise UnexpectedError(f"Server returned unexpected status code: {status_code}")
