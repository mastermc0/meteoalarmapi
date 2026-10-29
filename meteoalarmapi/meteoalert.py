"""Weather alerts for a European region from MeteoAlarm.org.

Reads the country's legacy Atom feed, keeps the entries whose area matches
the region (by name or EMMA_ID code) and fetches each one's CAP document.
"""

import re
from datetime import datetime, timezone

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


class Meteoalert:

    endpoint = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{0}"

    def __init__(self, country, province, language="en-GB"):
        self.country = country.lower()
        self.province = province
        self.language = language

    def get_alert(self):
        """The most severe alert for the province, or {} when there is none.

        See get_alerts() for the order.
        """
        alerts = self.get_alerts()
        return alerts[0] if alerts else {}

    def get_alerts(self, include_expired=True):
        """Every alert for the province, most severe first.

        Unexpired alerts come first, then the highest awareness level (or CAP
        severity when there is none). Equally ranked alerts keep feed order.

        With include_expired=False, expired alerts are left out. Future
        alerts (not started yet) are kept, so upcoming warnings still show.
        """
        feed = xmltodict.parse(self._fetch(self.endpoint.format(self.country)))
        entries = (feed.get("feed") or {}).get("entry", [])
        if not isinstance(entries, list):
            entries = [entries]

        alerts = []
        # The feed has an entry per language and area of an alert, all
        # pointing at the same CAP document, so fetch each one once.
        seen = set()
        for entry in entries:
            if not self.is_location_match(entry, self.province):
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
