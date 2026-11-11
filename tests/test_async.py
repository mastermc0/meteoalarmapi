"""AsyncMeteoalert and async_update_geocodes(), on the fixtures of test_meteoalarmapi.py"""

import asyncio
import inspect
import re
import sys
import unittest
from unittest import mock

import aiohttp
from aiohttp.client_proto import ResponseHandler
from aioresponses import aioresponses
from aioresponses import core as aioresponses_core
from yarl import URL

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


# aioresponses 0.7.9 predates aiohttp 3.14, which added a required
# stream_writer argument to ClientResponse and asserts when a mocked body over
# 64 KB pauses reading (ireland_sample.xml is 600 KB). These two patches do
# what the open fixes pnuckowski/aioresponses#288 and #292 do; drop them once
# a release has them (pnuckowski/aioresponses#289).
class _ClientResponse(aiohttp.ClientResponse):
    def __init__(self, *args, **kwargs):
        if "stream_writer" in inspect.signature(aiohttp.ClientResponse).parameters:
            kwargs.setdefault("stream_writer", mock.Mock(output_size=0))
        super().__init__(*args, **kwargs)


def _stream_reader(loop=None):
    # A limit big enough that feeding a fixture never pauses reading
    return aiohttp.StreamReader(ResponseHandler(loop=loop), limit=2 ** 30, loop=loop)


def mock_http():
    """aioresponses, patched for aiohttp 3.14 (see above)"""
    patches = [mock.patch.object(aioresponses_core, "ClientResponse", _ClientResponse),
               mock.patch.object(aioresponses_core, "stream_reader_factory", _stream_reader)]
    return patches, aioresponses()


def calls(m, url):
    """How many GET requests were made for url"""
    return len(m.requests.get(("GET", URL(url)), []))


def cap_calls(m):
    return sorted(str(url).rsplit("/", 1)[-1][:8] for _, url in m.requests
                  if "/api/v1/warnings/" in str(url))


class TestAsyncMeteoalert(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        patches, self.m = mock_http()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.m.start()
        self.addCleanup(self.m.stop)
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()

    def respond(self, url, repeat=True, **kwargs):
        """Answer GETs for url (a string or pattern). The first route that
        matches a request answers it."""
        self.m.get(url, repeat=repeat, **kwargs)

    def meteo(self, *args, **kwargs):
        return AsyncMeteoalert(*args, session=self.session, **kwargs)

    def register_multiple(self):
        self.respond(FEED + "united-kingdom", body=read("uk_multiple_sample.xml"))
        for uuid, name in MULTIPLE:
            self.respond(WARNINGS + "united-kingdom/" + uuid,
                         body=read(f"uk_multiple_detail_{name}.xml"))

    def register_norway(self):
        self.respond(FEED + "norway", body=read("norway_sample.xml"))
        for uuid in NORWAY:
            self.respond(re.compile(re.escape(WARNINGS + "norway/" + uuid)),
                         body=read(f"norway_sample_detail_{uuid}.xml"))

    async def test_get_alert(self):
        self.respond(FEED + "united-kingdom", body=read("uk_sample.xml"))
        self.respond(re.compile(re.escape(WARNINGS + "united-kingdom/")),
                     body=read("uk_nothern_ireland_sample_detail.xml"))

        data = await self.meteo("United-Kingdom", "Northern Ireland").async_get_alert()
        self.assertEqual(type(data), dict)
        self.assertGreater(len(data), 0)

    async def test_retrieve_alert_by_code(self):
        self.respond(FEED + "ireland", body=read("ireland_sample.xml"))
        self.respond(re.compile(re.escape(WARNINGS + "ireland/")),
                     body=read("ireland_sample_detail.xml"))

        self.assertGreater(len(await self.meteo("Ireland", "EI25").async_get_alert()), 0)
        self.assertEqual(await self.meteo("Ireland", "FAKE").async_get_alert(), {})

    async def test_language(self):
        self.respond(FEED + "france", body=read("france_sample.xml"))
        self.respond(re.compile(re.escape(WARNINGS + "france/")),
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
        self.respond(WARNINGS + "united-kingdom/" + MULTIPLE[1][0], status=404)
        self.register_multiple()
        meteo = self.meteo("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            events = [a["event"] for a in await meteo.async_get_alerts()]
        self.assertEqual(events, ["Yellow rain warning", "Yellow wind warning"])

    async def test_cap_document_server_error(self):
        self.respond(WARNINGS + "united-kingdom/" + MULTIPLE[1][0], status=503)
        self.register_multiple()
        meteo = self.meteo("United-Kingdom", "Highlands & Eilean Siar")
        with self.assertRaises(ServerError):
            await meteo.async_get_alerts()

    async def test_feed_not_found(self):
        self.respond(FEED + "atlantis", status=404)
        with self.assertRaisesRegex(WrongCountry, "'atlantis'.*temporarily unavailable"):
            await self.meteo("Atlantis", "Somewhere").async_get_alert()

    async def test_server_error(self):
        self.respond(FEED + "poland", status=500, body="simulated server error")
        with self.assertRaises(ServerError):
            await self.meteo("Poland", "Krakow").async_get_alert()

    async def test_server_not_reachable(self):
        # aioresponses refuses the connection for a URL it doesn't know
        with self.assertRaisesRegex(UnexpectedError, "Connection refused"):
            await self.meteo("Poland", "Krakow").async_get_alert()

    async def test_timeout(self):
        self.respond(FEED + "poland", exception=asyncio.TimeoutError())
        with self.assertRaisesRegex(UnexpectedError, "TimeoutError"):
            await self.meteo("Poland", "Krakow").async_get_alert()

    async def test_requests_use_timeout(self):
        self.register_multiple()
        await self.meteo("United-Kingdom", "Highlands & Eilean Siar").async_get_alerts()
        requests = [call for calls in self.m.requests.values() for call in calls]
        self.assertEqual(len(requests), 4)
        for call in requests:
            timeout = call.kwargs["timeout"]
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
        self.assertEqual(cap_calls(self.m), ["15082db6"])

    async def test_geocode_match(self):
        self.respond(FEED + "united-kingdom", body=read("uk_sample.xml"))
        self.respond(re.compile(re.escape(WARNINGS + "united-kingdom/")),
                     body=read("uk_nothern_ireland_sample_detail.xml"))

        meteo = self.meteo("United-Kingdom", latitude=BELFAST[0], longitude=BELFAST[1])
        self.assertEqual(await meteo.async_get_alerts(), [])
        self.assertFalse(meteo.geocodes_outdated)

        meteo = self.meteo("United-Kingdom", geocodes={"codes": ["UK108"], "known": UK_CODES})
        self.assertEqual(len(await meteo.async_get_alerts()), 2)
        self.assertEqual(cap_calls(self.m), ["a4647d38", "bca9d20a"])
        self.assertFalse(meteo.geocodes_outdated)

        meteo = self.meteo("United-Kingdom",
                           geocodes={"codes": ["UK108"], "known": UK_CODES[:-1]})
        self.assertEqual(len(await meteo.async_get_alerts()), 2)
        self.assertTrue(meteo.geocodes_outdated)

    def test_session_required(self):
        self.assertRaises(TypeError, AsyncMeteoalert, "Norway", "Oslo")
        self.assertRaises(ValueError, AsyncMeteoalert, "Norway", session=self.session)

    async def test_update_geocodes(self):
        self.respond(GEOCODES_LISTING, payload=LISTING)
        self.respond(NEWEST, body=read("geocodes_sample.json"))

        with clock("2026-10-29T12:00:00"):
            geocodes = await async_update_geocodes(self.session, *BELFAST)
        self.assertEqual(geocodes["codes"], ["UK108"])
        self.assertEqual(geocodes["version"], "MeteoAlarm_Geocodes_2026_07_31.json")
        self.assertEqual(geocodes["checked"], "2026-10-29T12:00:00+00:00")
        self.assertEqual(calls(self.m, NEWEST), 1)

        # within a month nothing is fetched, unless outdated and a day has passed
        self.m.requests.clear()
        with clock("2026-11-20T12:00:00"):
            self.assertIs(await async_update_geocodes(self.session, *BELFAST, geocodes), geocodes)
        with clock("2026-10-29T18:00:00"):
            self.assertIs(await async_update_geocodes(self.session, *BELFAST, geocodes,
                                                      outdated=True), geocodes)
        self.assertEqual(self.m.requests, {})

        # after that only the listing is read while the version is the same
        with clock("2026-10-31T12:00:00"):
            checked = await async_update_geocodes(self.session, *BELFAST, geocodes, outdated=True)
        self.assertEqual(checked["checked"], "2026-10-31T12:00:00+00:00")
        self.assertEqual(calls(self.m, GEOCODES_LISTING), 1)
        self.assertEqual(calls(self.m, NEWEST), 0)

        # a different point downloads the file again, whatever its age
        with clock("2026-10-29T12:05:00"):
            moved = await async_update_geocodes(self.session, 51.48, -3.18, geocodes)
        self.assertEqual(moved["codes"], ["UK117"])
        self.assertEqual(calls(self.m, NEWEST), 1)

    async def test_update_geocodes_new_version(self):
        newer = GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_11_30.json"
        self.respond(GEOCODES_LISTING,
                     payload=LISTING + [{"path": "MeteoAlarm_Geocodes_2026_11_30.json"}])
        self.respond(newer, body=read("geocodes_sample.json"))
        geocodes = {"codes": ["UK108"], "known": UK_CODES, "latitude": BELFAST[0],
                    "longitude": BELFAST[1], "version": "MeteoAlarm_Geocodes_2026_07_31.json",
                    "checked": "2026-10-29T12:00:00+00:00"}
        with clock("2026-12-01T12:00:00"):
            updated = await async_update_geocodes(self.session, *BELFAST, geocodes)
        self.assertEqual(updated["version"], "MeteoAlarm_Geocodes_2026_11_30.json")
        self.assertEqual(calls(self.m, newer), 1)

    async def test_update_geocodes_without_listing(self):
        self.respond(GEOCODES_LISTING, status=503)
        ref, name = GEOCODES_FALLBACK
        self.respond(f"{GEOCODES_FILES}{ref}/{name}", body=read("geocodes_sample.json"))

        # a first setup falls back to the pinned version
        geocodes = await async_update_geocodes(self.session, *BELFAST)
        self.assertEqual(geocodes["codes"], ["UK108"])

        # a later check raises, so the caller keeps what it has
        with clock("2027-01-01T12:00:00"), self.assertRaises(ServerError):
            await async_update_geocodes(self.session, *BELFAST, geocodes)

    async def test_update_geocodes_bad_listing(self):
        geocodes = {"latitude": BELFAST[0], "longitude": BELFAST[1],
                    "checked": "2026-01-01T12:00:00+00:00"}
        self.respond(GEOCODES_LISTING, payload=[{"path": "README.md"}], repeat=False)
        with self.assertRaisesRegex(UnexpectedError, "no geocodes file"):
            await async_update_geocodes(self.session, *BELFAST, geocodes)
        self.respond(GEOCODES_LISTING, body="<html>")
        with self.assertRaisesRegex(UnexpectedError, "Unexpected geocodes listing"):
            await async_update_geocodes(self.session, *BELFAST, geocodes)

    async def test_update_geocodes_bad_file(self):
        self.respond(GEOCODES_LISTING, payload=LISTING)
        self.respond(NEWEST, body="<html>", repeat=False)
        with self.assertRaises(UnexpectedError):
            await async_update_geocodes(self.session, *BELFAST)
        self.respond(NEWEST, body='{"features": [{"type": "Feature", ')
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
