"""AsyncMeteoalert and async_update_geocodes(), on the fixtures of test_meteoalarmapi.py"""

import asyncio
import json
import re
import sys
import unittest
from unittest import mock

import aiohttp

import meteoalarmapi
from meteoalarmapi import (
    AsyncMeteoalert,
    ServerError,
    UnexpectedError,
    WrongCountry,
    async_update_geocodes,
)
from meteoalarmapi.meteoalert import GEOCODES_FALLBACK, GEOCODES_LISTING

from .test_meteoalarmapi import (
    BELFAST,
    GEOCODES_FILES,
    LISTING,
    MULTIPLE,
    NORWAY,
    UK_CODES,
    clock,
    read,
)

FEED = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-"
WARNINGS = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-"
NEWEST = GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_07_31.json"


class FakeResponse:
    """The part of aiohttp.ClientResponse the client uses, as an async
    context manager like session.get()'s"""

    def __init__(self, url, route):
        self.url = url
        self.route = route

    async def __aenter__(self):
        if self.route is None:
            raise aiohttp.ClientConnectionError(f"Connection refused: GET {self.url}")
        status, body, exception = self.route
        if exception is not None:
            raise exception
        self.status = status
        self.body = body
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def read(self):
        return self.body


class FakeSession:
    """Stands in for aiohttp.ClientSession: answers GETs from respond()
    (a later route for the same URL wins, as in requests_mock) and records
    each request. aioresponses doesn't work with aiohttp 3.14."""

    def __init__(self):
        self.routes = []
        self.requests = []
        self.closed = False

    def respond(self, url, body="", status=200, payload=None, exception=None):
        if payload is not None:
            body = json.dumps(payload)
        self.routes.insert(0, (url, (status, body.encode("utf-8"), exception)))

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        for pattern, route in self.routes:
            if pattern.match(url) if isinstance(pattern, re.Pattern) else pattern == url:
                return FakeResponse(url, route)
        return FakeResponse(url, None)


def calls(http, url):
    """How many GET requests were made for url"""
    return sum(1 for requested, _ in http.requests if requested == url)


def cap_calls(http):
    return sorted(url.rsplit("/", 1)[-1][:8] for url, _ in http.requests
                  if "/api/v1/warnings/" in url)


class TestAsyncMeteoalert(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.http = self.session = FakeSession()

    def meteo(self, *args, **kwargs):
        return AsyncMeteoalert(*args, session=self.session, **kwargs)

    def register_multiple(self):
        self.http.respond(FEED + "united-kingdom", body=read("uk_multiple_sample.xml"))
        for uuid, name in MULTIPLE:
            self.http.respond(WARNINGS + "united-kingdom/" + uuid,
                              body=read(f"uk_multiple_detail_{name}.xml"))

    def register_norway(self):
        self.http.respond(FEED + "norway", body=read("norway_sample.xml"))
        for uuid in NORWAY:
            self.http.respond(re.compile(re.escape(WARNINGS + "norway/" + uuid)),
                              body=read(f"norway_sample_detail_{uuid}.xml"))

    async def test_get_alert(self):
        self.http.respond(FEED + "united-kingdom", body=read("uk_sample.xml"))
        self.http.respond(re.compile(re.escape(WARNINGS + "united-kingdom/")),
                          body=read("uk_nothern_ireland_sample_detail.xml"))

        data = await self.meteo("United-Kingdom", "Northern Ireland").async_get_alert()
        self.assertEqual(type(data), dict)
        self.assertGreater(len(data), 0)

    async def test_retrieve_alert_by_code(self):
        self.http.respond(FEED + "ireland", body=read("ireland_sample.xml"))
        self.http.respond(re.compile(re.escape(WARNINGS + "ireland/")),
                          body=read("ireland_sample_detail.xml"))

        self.assertGreater(len(await self.meteo("Ireland", "EI25").async_get_alert()), 0)
        self.assertEqual(await self.meteo("Ireland", "FAKE").async_get_alert(), {})

    async def test_language(self):
        self.http.respond(FEED + "france", body=read("france_sample.xml"))
        self.http.respond(re.compile(re.escape(WARNINGS + "france/")),
                          body=read("france_sample_detail.xml"))

        data = await self.meteo("France", "Haute Savoie", "fr-FR").async_get_alert()
        self.assertEqual(data["language"], "fr-FR")
        self.assertGreater(len(await self.meteo("France", "FR822").async_get_alert()), 0)

    async def test_get_alerts_order(self):
        self.register_multiple()
        meteo = self.meteo("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            events = [a["event"] for a in await meteo.async_get_alerts()]
        # fetched concurrently, but the two equal yellows still keep feed order
        self.assertEqual(events, ["Amber wind warning", "Yellow rain warning", "Yellow wind warning"])

        with clock("2026-10-25T16:00:00"):
            events = [a["event"] for a in await meteo.async_get_alerts()]
        self.assertEqual(events, ["Amber wind warning", "Yellow wind warning", "Yellow rain warning"])
        with clock("2026-10-25T16:00:00"):
            alert = await meteo.async_get_alert()
        self.assertEqual(alert["awareness_level"], "3; Orange; Severe")

    async def test_get_alerts_without_expired(self):
        self.register_multiple()
        meteo = self.meteo("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-25T16:00:00"):
            events = [a["event"] for a in await meteo.async_get_alerts(include_expired=False)]
        self.assertEqual(events, ["Amber wind warning", "Yellow wind warning"])
        with clock("2026-10-26T12:00:00"):
            self.assertEqual(await meteo.async_get_alerts(include_expired=False), [])
            self.assertEqual(len(await meteo.async_get_alerts()), 3)

    async def test_cap_message_fields(self):
        self.register_multiple()
        with clock("2026-10-24T12:00:00"):
            alert = await self.meteo("United-Kingdom", "Highlands & Eilean Siar").async_get_alert()
        self.assertEqual(alert["identifier"], "2.49.0.0.826.0.GB_261024074259_34b39a5a.v2.0.W")
        self.assertEqual(alert["message_type"], "Update")
        self.assertEqual(alert["sent"], "2026-10-24T07:42:59+00:00")

    async def test_missing_cap_document_skipped(self):
        self.register_multiple()
        self.http.respond(WARNINGS + "united-kingdom/" + MULTIPLE[1][0], status=404)
        meteo = self.meteo("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            events = [a["event"] for a in await meteo.async_get_alerts()]
        self.assertEqual(events, ["Yellow rain warning", "Yellow wind warning"])

    async def test_cap_document_server_error(self):
        self.register_multiple()
        self.http.respond(WARNINGS + "united-kingdom/" + MULTIPLE[1][0], status=503)
        meteo = self.meteo("United-Kingdom", "Highlands & Eilean Siar")
        with self.assertRaises(ServerError):
            await meteo.async_get_alerts()

    async def test_feed_not_found(self):
        self.http.respond(FEED + "atlantis", status=404)
        with self.assertRaisesRegex(WrongCountry, "'atlantis'.*temporarily unavailable"):
            await self.meteo("Atlantis", "Somewhere").async_get_alert()

    async def test_server_error(self):
        self.http.respond(FEED + "poland", status=500, body="simulated server error")
        with self.assertRaises(ServerError):
            await self.meteo("Poland", "Krakow").async_get_alert()

    async def test_server_not_reachable(self):
        # the fake session refuses the connection for a URL it doesn't know
        with self.assertRaisesRegex(UnexpectedError, "Connection refused"):
            await self.meteo("Poland", "Krakow").async_get_alert()

    async def test_timeout(self):
        self.http.respond(FEED + "poland", exception=asyncio.TimeoutError())
        with self.assertRaisesRegex(UnexpectedError, "TimeoutError"):
            await self.meteo("Poland", "Krakow").async_get_alert()

    async def test_requests_use_timeout(self):
        self.register_multiple()
        await self.meteo("United-Kingdom", "Highlands & Eilean Siar").async_get_alerts()
        self.assertEqual(len(self.http.requests), 4)
        for _, kwargs in self.http.requests:
            timeout = kwargs["timeout"]
            self.assertEqual((timeout.sock_connect, timeout.sock_read), (10, 10))
            self.assertIsNone(timeout.total)
        # the caller's session is left open
        self.assertFalse(self.session.closed)

    async def test_point_match(self):
        self.register_norway()

        async def events(lat, lon, province=None):
            meteo = self.meteo("Norway", province, latitude=lat, longitude=lon)
            return sorted(a["headline"].split(",")[2].strip() if "," in a["headline"]
                          else a["event"] for a in await meteo.async_get_alerts())

        self.assertEqual(await events(58.970, 5.733), ["Coast and fjords in Rogaland"])
        self.assertEqual(await events(59.668, 9.650), ["Flood"])
        self.assertEqual(await events(63.430, 10.395), [])
        self.assertEqual(await events(60.392, 5.324, "Rogaland"),
                         ["Coast and fjords in Hordaland", "Coast and fjords in Rogaland"])

    async def test_alert_fetched_once(self):
        self.register_norway()
        alerts = await self.meteo("Norway", "stlandet").async_get_alerts()
        self.assertEqual([a["event"] for a in alerts], ["Flood"])
        self.assertEqual(cap_calls(self.http), ["15082db6"])

    async def test_geocode_match(self):
        self.http.respond(FEED + "united-kingdom", body=read("uk_sample.xml"))
        self.http.respond(re.compile(re.escape(WARNINGS + "united-kingdom/")),
                          body=read("uk_nothern_ireland_sample_detail.xml"))

        meteo = self.meteo("United-Kingdom", latitude=BELFAST[0], longitude=BELFAST[1])
        self.assertEqual(await meteo.async_get_alerts(), [])
        self.assertFalse(meteo.geocodes_outdated)

        meteo = self.meteo("United-Kingdom", geocodes={"codes": ["UK108"], "known": UK_CODES})
        self.assertEqual(len(await meteo.async_get_alerts()), 2)
        self.assertEqual(cap_calls(self.http), ["a4647d38", "bca9d20a"])
        self.assertFalse(meteo.geocodes_outdated)

        meteo = self.meteo("United-Kingdom",
                           geocodes={"codes": ["UK108"], "known": UK_CODES[:-1]})
        self.assertEqual(len(await meteo.async_get_alerts()), 2)
        self.assertTrue(meteo.geocodes_outdated)

    def test_session_required(self):
        self.assertRaises(TypeError, AsyncMeteoalert, "Norway", "Oslo")
        self.assertRaises(ValueError, AsyncMeteoalert, "Norway", session=self.session)

    async def test_update_geocodes(self):
        self.http.respond(GEOCODES_LISTING, payload=LISTING)
        self.http.respond(NEWEST, body=read("geocodes_sample.json"))

        with clock("2026-10-29T12:00:00"):
            geocodes = await async_update_geocodes(self.session, *BELFAST)
        self.assertEqual(geocodes["codes"], ["UK108"])
        self.assertEqual(geocodes["version"], "MeteoAlarm_Geocodes_2026_07_31.json")
        self.assertEqual(geocodes["checked"], "2026-10-29T12:00:00+00:00")
        self.assertEqual(calls(self.http, NEWEST), 1)

        # within a month nothing is fetched, unless outdated and a day has passed
        self.http.requests.clear()
        with clock("2026-11-20T12:00:00"):
            self.assertIs(await async_update_geocodes(self.session, *BELFAST, geocodes), geocodes)
        with clock("2026-10-29T18:00:00"):
            self.assertIs(await async_update_geocodes(self.session, *BELFAST, geocodes,
                                                      outdated=True), geocodes)
        self.assertEqual(self.http.requests, [])

        # after that only the listing is read while the version is the same
        with clock("2026-10-31T12:00:00"):
            checked = await async_update_geocodes(self.session, *BELFAST, geocodes, outdated=True)
        self.assertEqual(checked["checked"], "2026-10-31T12:00:00+00:00")
        self.assertEqual(calls(self.http, GEOCODES_LISTING), 1)
        self.assertEqual(calls(self.http, NEWEST), 0)

        # a different point downloads the file again, whatever its age
        with clock("2026-10-29T12:05:00"):
            moved = await async_update_geocodes(self.session, 51.48, -3.18, geocodes)
        self.assertEqual(moved["codes"], ["UK117"])
        self.assertEqual(calls(self.http, NEWEST), 1)

    async def test_update_geocodes_new_version(self):
        newer = GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_11_30.json"
        self.http.respond(GEOCODES_LISTING,
                          payload=LISTING + [{"path": "MeteoAlarm_Geocodes_2026_11_30.json"}])
        self.http.respond(newer, body=read("geocodes_sample.json"))
        geocodes = {"codes": ["UK108"], "known": UK_CODES, "latitude": BELFAST[0],
                    "longitude": BELFAST[1], "version": "MeteoAlarm_Geocodes_2026_07_31.json",
                    "checked": "2026-10-29T12:00:00+00:00"}
        with clock("2026-12-01T12:00:00"):
            updated = await async_update_geocodes(self.session, *BELFAST, geocodes)
        self.assertEqual(updated["version"], "MeteoAlarm_Geocodes_2026_11_30.json")
        self.assertEqual(calls(self.http, newer), 1)

    async def test_update_geocodes_without_listing(self):
        self.http.respond(GEOCODES_LISTING, status=503)
        ref, name = GEOCODES_FALLBACK
        self.http.respond(f"{GEOCODES_FILES}{ref}/{name}", body=read("geocodes_sample.json"))

        # a first setup falls back to the pinned version
        geocodes = await async_update_geocodes(self.session, *BELFAST)
        self.assertEqual(geocodes["codes"], ["UK108"])

        # a later check raises, so the caller keeps what it has
        with clock("2027-01-01T12:00:00"), self.assertRaises(ServerError):
            await async_update_geocodes(self.session, *BELFAST, geocodes)

    async def test_update_geocodes_bad_listing(self):
        geocodes = {"latitude": BELFAST[0], "longitude": BELFAST[1],
                    "checked": "2026-01-01T12:00:00+00:00"}
        self.http.respond(GEOCODES_LISTING, payload=[{"path": "README.md"}])
        with self.assertRaisesRegex(UnexpectedError, "no geocodes file"):
            await async_update_geocodes(self.session, *BELFAST, geocodes)
        self.http.respond(GEOCODES_LISTING, body="<html>")
        with self.assertRaisesRegex(UnexpectedError, "Unexpected geocodes listing"):
            await async_update_geocodes(self.session, *BELFAST, geocodes)

    async def test_update_geocodes_bad_file(self):
        self.http.respond(GEOCODES_LISTING, payload=LISTING)
        self.http.respond(NEWEST, body="<html>")
        with self.assertRaises(UnexpectedError):
            await async_update_geocodes(self.session, *BELFAST)
        self.http.respond(NEWEST, body='{"features": [{"type": "Feature", ')
        with self.assertRaises(UnexpectedError):
            await async_update_geocodes(self.session, *BELFAST)


class TestWithoutAiohttp(unittest.TestCase):

    def test_import_error(self):
        """Without the [async] extra, the sync API works and the async one says
        what to install"""
        aio = sys.modules.pop("meteoalarmapi.aio")
        delattr(meteoalarmapi, "aio")
        try:
            with mock.patch.dict(sys.modules, {"aiohttp": None}):
                self.assertIs(meteoalarmapi.Meteoalert, meteoalarmapi.meteoalert.Meteoalert)
                with self.assertRaisesRegex(ImportError, r"meteoalarmapi\[async\]"):
                    meteoalarmapi.AsyncMeteoalert  # noqa: B018
        finally:
            sys.modules["meteoalarmapi.aio"] = aio
            meteoalarmapi.aio = aio
