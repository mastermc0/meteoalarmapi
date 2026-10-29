import os
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

import requests_mock

from meteoalarmapi import Meteoalert, ServerError, UnexpectedError, WrongCountry
from meteoalarmapi.meteoalert import (
    GEOCODES_FALLBACK,
    GEOCODES_LISTING,
    parse_polygon,
    point_in_geometry,
    point_in_polygon,
    resolve_geocodes,
    update_geocodes,
)

# uk_multiple_sample.xml (Highlands & Eilean Siar, 24 Oct 2026) lists, in feed order:
MULTIPLE = (
    ("43e42eb0-196b-4fe7-b26e-1e3490954cc9", "yellow_rain"),
    ("ebbdecd5-fa00-40ec-9fa2-a4a5ea13b424", "amber_wind"),
    ("b0e83ff1-0a4b-4f2e-947a-c20b1432417c", "yellow_wind"),
)
# norway_sample.xml (26 Oct 2026) has no provinces, only area polygons: high
# water in Rogaland and Hordaland, and a flood with two entries (two areas).
# Detail files are named by the CAP document's id prefix.
NORWAY = ("ef68e1fd", "7be0d389", "15082db6")
# geocodes_sample.json is a made-up excerpt of MeteoAlarm's geocodes file in
# the same format: boxes for Northern Ireland (UK108), Wales (UK117, with a
# hole and Anglesey as a second polygon) and Haute Savoie (FR718).
BELFAST = (54.597, -5.930)
UK_CODES = ["UK101", "UK102", "UK103", "UK104", "UK105", "UK107", "UK108", "UK109",
            "UK110", "UK111", "UK112", "UK113", "UK114", "UK115", "UK117"]
GEOCODES_FILES = "https://gitlab.com/meteoalarm-pm-group/documents/-/raw/"
LISTING = [{"path": "MeteoAlarm_CAP_Profile_v2.0.pdf"},
           {"path": "MeteoAlarm_Geocodes_2026_02_20.json"},
           {"path": "MeteoAlarm_Geocodes_2026_07_31.json"},
           {"path": "README.md"}]


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def clock(text):
    return mock.patch("meteoalarmapi.meteoalert._utcnow", return_value=utc(text))


def file_path(file_name):
    return os.path.join(os.path.dirname(__file__), file_name)


def read(file_name):
    with open(file_path(file_name), encoding="utf-8") as xml_file:
        return xml_file.read()


class TestMeteoalert(unittest.TestCase):

    @requests_mock.mock()
    def test_get_alert(self, m):
        m.get("/feeds/meteoalarm-legacy-atom-united-kingdom", text=read("uk_sample.xml"))
        # every matching alert is fetched to rank them
        m.get(re.compile("/api/v1/warnings/feeds-united-kingdom/"),
              text=read("uk_nothern_ireland_sample_detail.xml"))

        data = Meteoalert("United-Kingdom", "Northern Ireland").get_alert()
        self.assertEqual(type(data), dict)
        self.assertGreater(len(data), 0)

    def test_wrong_country_name(self):
        meteo = Meteoalert("wrong_country_name", "some_city", "some-lang")
        self.assertRaises(WrongCountry, meteo.get_alert)

    @requests_mock.mock()
    def test_retrieve_alert_by_code(self, m):
        m.get("/feeds/meteoalarm-legacy-atom-ireland", text=read("ireland_sample.xml"))
        m.get(re.compile("/api/v1/warnings/feeds-ireland/"), text=read("ireland_sample_detail.xml"))

        data = Meteoalert("Ireland", "EI25").get_alert()
        self.assertGreater(len(data), 0)

        self.assertEqual(Meteoalert("Ireland", "FAKE").get_alert(), {})

    @requests_mock.mock()
    def test_lookup_by_code_and_area_name(self, m):
        m.get("/feeds/meteoalarm-legacy-atom-france", text=read("france_sample.xml"))
        m.get(re.compile("/api/v1/warnings/feeds-france/"), text=read("france_sample_detail.xml"))

        self.assertGreater(len(Meteoalert("France", "Haute Savoie").get_alert()), 0)
        # the French localised version
        data = Meteoalert("France", "Haute Savoie", "fr-FR").get_alert()
        self.assertEqual(data["language"], "fr-FR")
        # by area code
        self.assertGreater(len(Meteoalert("France", "FR822", "fr-FR").get_alert()), 0)
        self.assertEqual(Meteoalert("France", "FAKE").get_alert(), {})

    def test_server_not_reachable(self):
        meteo = Meteoalert("poland", "krakow", "en-US")
        meteo.endpoint = "https://simulate-dns-problem-with-meteo.zzzzzz/{0}"
        self.assertRaises(UnexpectedError, meteo.get_alert)

    def test_server_error(self):
        with requests_mock.mock() as m:
            m.get(requests_mock.ANY, status_code=500, text="simulated server error")
            meteo = Meteoalert("wrong_country_name", "some_city", "some-lang")
            self.assertRaises(ServerError, meteo.get_alert)

    @requests_mock.mock()
    def test_most_severe_alert(self, m):
        self._register_multiple(m)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            data = meteo.get_alert()
        self.assertEqual(data["event"], "Amber wind warning")
        self.assertEqual(data["severity"], "Severe")

    @requests_mock.mock()
    def test_get_alerts_order(self, m):
        self._register_multiple(m)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            events = [a["event"] for a in meteo.get_alerts()]
        # the two yellows rank equally, so keep feed order
        self.assertEqual(events, ["Amber wind warning", "Yellow rain warning", "Yellow wind warning"])

        with clock("2026-10-25T16:00:00"):
            events = [a["event"] for a in meteo.get_alerts()]
        # yellow rain expired at 15:00
        self.assertEqual(events, ["Amber wind warning", "Yellow wind warning", "Yellow rain warning"])

    @requests_mock.mock()
    def test_get_alerts_without_expired(self, m):
        self._register_multiple(m)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            events = [a["event"] for a in meteo.get_alerts(include_expired=False)]
        # future alerts are kept: rain and amber wind only start at 20:00
        self.assertEqual(events, ["Amber wind warning", "Yellow rain warning", "Yellow wind warning"])

        with clock("2026-10-25T16:00:00"):
            events = [a["event"] for a in meteo.get_alerts(include_expired=False)]
        # yellow rain expired at 15:00
        self.assertEqual(events, ["Amber wind warning", "Yellow wind warning"])

        with clock("2026-10-26T12:00:00"):
            self.assertEqual(meteo.get_alerts(include_expired=False), [])
            # by default expired alerts are still returned
            self.assertEqual(len(meteo.get_alerts()), 3)

    @requests_mock.mock()
    def test_single_language_parameters(self, m):
        """UK CAP documents have one <info>; its parameters used to be dropped"""
        self._register_multiple(m)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            data = meteo.get_alert()
        self.assertEqual(data["awareness_level"], "3; Orange; Severe")
        self.assertEqual(data["awareness_type"], "1; Wind")

    def test_expired_alert_not_preferred(self):
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")
        expired_amber = {"event": "Amber wind warning", "severity": "Severe",
                         "awareness_level": "3; Orange; Severe",
                         "expires": "2026-10-24T10:00:00+00:00"}
        current_yellow = {"event": "Yellow rain warning", "severity": "Moderate",
                          "expires": "2026-10-25T15:00:00+00:00"}

        with clock("2026-10-24T12:00:00"):
            ranked = max([expired_amber, current_yellow], key=meteo.alert_rank)
        self.assertIs(ranked, current_yellow)

    def test_is_expired(self):
        with clock("2026-10-24T12:00:00"):
            self.assertTrue(Meteoalert.is_expired("2026-10-24T11:59:59+00:00"))
            self.assertFalse(Meteoalert.is_expired("2026-10-24T12:30:00+00:00"))
            # 13:30 at +02:00 is 11:30 UTC
            self.assertTrue(Meteoalert.is_expired("2026-10-24T13:30:00+02:00"))
            self.assertTrue(Meteoalert.is_expired("2026-10-24T11:00:00Z"))
            self.assertFalse(Meteoalert.is_expired(None))
            self.assertFalse(Meteoalert.is_expired("not a date"))

    @requests_mock.mock()
    def test_missing_cap_document_skipped(self, m):
        """A warning withdrawn after the feed was read (home-assistant/core#177632)"""
        self._register_multiple(m)
        m.get("/api/v1/warnings/feeds-united-kingdom/" + MULTIPLE[1][0], status_code=404)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            events = [a["event"] for a in meteo.get_alerts()]
        self.assertEqual(events, ["Yellow rain warning", "Yellow wind warning"])

    @requests_mock.mock()
    def test_cap_document_server_error(self, m):
        """Other failures fetching a warning still fail the update"""
        self._register_multiple(m)
        m.get("/api/v1/warnings/feeds-united-kingdom/" + MULTIPLE[1][0], status_code=503)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")
        self.assertRaises(ServerError, meteo.get_alerts)

    @requests_mock.mock()
    def test_feed_not_found(self, m):
        m.get("/feeds/meteoalarm-legacy-atom-atlantis", status_code=404)
        with self.assertRaisesRegex(WrongCountry, "'atlantis'.*temporarily unavailable"):
            Meteoalert("Atlantis", "Somewhere").get_alert()

    @requests_mock.mock()
    def test_cap_message_fields(self, m):
        """identifier, msgType, references and sent come from the CAP <alert>"""
        self._register_multiple(m)
        meteo = Meteoalert("United-Kingdom", "Highlands & Eilean Siar")

        with clock("2026-10-24T12:00:00"):
            alerts = {a["event"]: a for a in meteo.get_alerts()}

        amber = alerts["Amber wind warning"]
        self.assertEqual(amber["identifier"], "2.49.0.0.826.0.GB_261024074259_34b39a5a.v2.0.W")
        self.assertEqual(amber["message_type"], "Update")
        self.assertEqual(amber["references"], "https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings,2.49.0.0.826.0.GB_261023090239_34b39a5a.v1.0.W,2026-10-23T09:02:39+00:00")
        self.assertEqual(amber["sent"], "2026-10-24T07:42:59+00:00")

        rain = alerts["Yellow rain warning"]
        self.assertEqual(rain["message_type"], "Alert")
        self.assertIsNone(rain["references"])

        # each alert can be told apart by its identifier
        self.assertEqual(len({a["identifier"] for a in alerts.values()}), 3)

    @requests_mock.mock()
    def test_point_match(self, m):
        self._register_norway(m)

        def events(lat, lon, province=None):
            # area names, from headlines like "High water level, yellow level, <area>, ..."
            alerts = Meteoalert("Norway", province, latitude=lat, longitude=lon).get_alerts()
            return sorted(a["headline"].split(",")[2].strip() if "," in a["headline"]
                          else a["event"] for a in alerts)

        # Stavanger and Bergen are each in one coastal high water area
        self.assertEqual(events(58.970, 5.733), ["Coast and fjords in Rogaland"])
        self.assertEqual(events(60.392, 5.324), ["Coast and fjords in Hordaland"])
        # Kongsberg is in the flood area
        self.assertEqual(events(59.668, 9.650), ["Flood"])
        # Trondheim is in none of them
        self.assertEqual(events(63.430, 10.395), [])
        # with a province too, alerts matching either are included
        self.assertEqual(events(60.392, 5.324, "Rogaland"),
                         ["Coast and fjords in Hordaland", "Coast and fjords in Rogaland"])

    @requests_mock.mock()
    def test_alert_fetched_once(self, m):
        """The flood has two feed entries (one per area) sharing a CAP document"""
        self._register_norway(m)

        alerts = Meteoalert("Norway", "stlandet").get_alerts()
        self.assertEqual([a["event"] for a in alerts], ["Flood"])
        cap_requests = [r for r in m.request_history if "15082db6" in r.url]
        self.assertEqual(len(cap_requests), 1)

    def test_location_required(self):
        self.assertRaises(ValueError, Meteoalert, "Norway")
        self.assertRaises(ValueError, Meteoalert, "Norway", latitude=59.9)
        self.assertRaises(ValueError, Meteoalert, "Norway", "Oslo", longitude=10.7)

    def test_point_in_polygon(self):
        square = parse_polygon("0,0 0,10 10,10 10,0 0,0")
        self.assertTrue(point_in_polygon((5, 5), square))
        self.assertFalse(point_in_polygon((15, 5), square))
        self.assertFalse(point_in_polygon((5, -1), square))
        # an L shape: the notch at (7, 7) is outside
        ell = parse_polygon("0,0 0,10 5,10 5,5 10,5 10,0")
        self.assertTrue(point_in_polygon((2, 8), ell))
        self.assertFalse(point_in_polygon((7, 7), ell))
        self.assertFalse(point_in_polygon((1, 1), parse_polygon("0,0 1,1")))
        self.assertEqual(parse_polygon("1,2 bad 3,4"), [(1.0, 2.0), (3.0, 4.0)])

    def test_point_in_geometry(self):
        # GeoJSON is [lon, lat]
        square = {"type": "Polygon", "coordinates": [[[0, 0], [20, 0], [20, 10], [0, 10], [0, 0]]]}
        self.assertTrue(point_in_geometry((5, 15), square))
        self.assertFalse(point_in_geometry((15, 5), square))
        with_hole = {"type": "Polygon", "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
        self.assertTrue(point_in_geometry((2, 2), with_hole))
        self.assertFalse(point_in_geometry((5, 5), with_hole))
        two = {"type": "MultiPolygon", "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]]]}
        self.assertTrue(point_in_geometry((5.5, 5.5), two))
        self.assertFalse(point_in_geometry((3, 3), two))
        self.assertFalse(point_in_geometry((0.5, 0.5), {"type": "Point", "coordinates": [0.5, 0.5]}))

    def test_resolve_geocodes(self):
        text = read("geocodes_sample.json")
        with clock("2026-10-29T12:00:00"):
            geocodes = resolve_geocodes(*BELFAST, text, "MeteoAlarm_Geocodes_2026_07_31.json")
        self.assertEqual(geocodes, {
            "codes": ["UK108"],
            # every code of the point's country, but not France's
            "known": ["UK108", "UK117"],
            "latitude": BELFAST[0],
            "longitude": BELFAST[1],
            "version": "MeteoAlarm_Geocodes_2026_07_31.json",
            "checked": "2026-10-29T12:00:00+00:00",
        })

        def codes(lat, lon):
            return resolve_geocodes(lat, lon, text, "v")["codes"]
        self.assertEqual(codes(51.48, -3.18), ["UK117"])  # Cardiff
        self.assertEqual(codes(52.25, -4.0), [])  # in Wales's hole
        self.assertEqual(codes(53.3, -4.4), ["UK117"])  # Anglesey
        # a point in no area, such as at sea, knows no codes either
        self.assertEqual(resolve_geocodes(47.0, -5.0, text, "v")["known"], [])

    @requests_mock.mock()
    def test_geocode_match(self, m):
        """uk_sample.xml is an older UK feed, with EMMA_IDs and no polygons"""
        m.get("/feeds/meteoalarm-legacy-atom-united-kingdom", text=read("uk_sample.xml"))
        m.get(re.compile("/api/v1/warnings/feeds-united-kingdom/"),
              text=read("uk_nothern_ireland_sample_detail.xml"))

        # without geocodes a point matches nothing here
        meteo = Meteoalert("United-Kingdom", latitude=BELFAST[0], longitude=BELFAST[1])
        self.assertEqual(meteo.get_alerts(), [])
        self.assertFalse(meteo.geocodes_outdated)

        geocodes = {"codes": ["UK108"], "known": UK_CODES}
        m.reset_mock()
        meteo = Meteoalert("United-Kingdom", latitude=BELFAST[0], longitude=BELFAST[1],
                           geocodes=geocodes)
        self.assertEqual(len(meteo.get_alerts()), 2)
        # the two CAP documents with a Northern Ireland entry
        fetched = sorted(r.url.rsplit("/", 1)[-1][:8] for r in m.request_history
                         if "/api/v1/warnings/" in r.url)
        self.assertEqual(fetched, ["a4647d38", "bca9d20a"])
        self.assertFalse(meteo.geocodes_outdated)
        # geocodes alone are enough
        self.assertEqual(len(Meteoalert("United-Kingdom", geocodes=geocodes).get_alerts()), 2)

        # a code in the feed but not in geocodes
        meteo = Meteoalert("United-Kingdom", geocodes={"codes": ["UK108"], "known": UK_CODES[:-1]})
        self.assertEqual(len(meteo.get_alerts()), 2)
        self.assertTrue(meteo.geocodes_outdated)

    @requests_mock.mock()
    def test_update_geocodes(self, m):
        m.get(GEOCODES_LISTING, json=LISTING)
        newest = m.get(GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_07_31.json",
                       text=read("geocodes_sample.json"))

        # the first call downloads the newest file
        with clock("2026-10-29T12:00:00"):
            geocodes = update_geocodes(*BELFAST)
        self.assertEqual(geocodes["codes"], ["UK108"])
        self.assertEqual(geocodes["version"], "MeteoAlarm_Geocodes_2026_07_31.json")
        self.assertEqual(newest.call_count, 1)

        # within a month nothing is fetched, unless outdated and a day has passed
        m.reset_mock()
        with clock("2026-11-20T12:00:00"):
            self.assertIs(update_geocodes(*BELFAST, geocodes), geocodes)
        with clock("2026-10-29T18:00:00"):
            self.assertIs(update_geocodes(*BELFAST, geocodes, outdated=True), geocodes)
        self.assertFalse(m.called)

        # after that only the listing is read while the version is the same
        with clock("2026-10-31T12:00:00"):
            checked = update_geocodes(*BELFAST, geocodes, outdated=True)
        self.assertEqual(checked["checked"], "2026-10-31T12:00:00+00:00")
        self.assertEqual(checked["codes"], ["UK108"])
        self.assertEqual(m.call_count, 1)
        with clock("2026-12-01T12:00:00"):
            update_geocodes(*BELFAST, geocodes)
        self.assertEqual(newest.call_count, 0)

        # a new version is downloaded
        m.get(GEOCODES_LISTING, json=LISTING + [{"path": "MeteoAlarm_Geocodes_2026_11_30.json"}])
        newer = m.get(GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_11_30.json",
                      text=read("geocodes_sample.json"))
        with clock("2026-12-01T12:00:00"):
            self.assertEqual(update_geocodes(*BELFAST, geocodes)["version"],
                             "MeteoAlarm_Geocodes_2026_11_30.json")
        self.assertEqual(newer.call_count, 1)

        # so is one for a different point, whatever its age
        with clock("2026-10-29T12:05:00"):
            moved = update_geocodes(51.48, -3.18, geocodes)
        self.assertEqual(moved["codes"], ["UK117"])

    @requests_mock.mock()
    def test_update_geocodes_without_listing(self, m):
        m.get(GEOCODES_LISTING, status_code=503)
        ref, name = GEOCODES_FALLBACK
        pinned = m.get(f"{GEOCODES_FILES}{ref}/{name}", text=read("geocodes_sample.json"))

        # a first setup falls back to the pinned version
        geocodes = update_geocodes(*BELFAST)
        self.assertEqual(geocodes["codes"], ["UK108"])
        self.assertEqual(pinned.call_count, 1)

        # a later check raises, so the caller keeps what it has
        with clock("2027-01-01T12:00:00"):
            self.assertRaises(ServerError, update_geocodes, *BELFAST, geocodes)

        m.get(GEOCODES_LISTING, json=[{"path": "README.md"}])
        with clock("2027-01-01T12:00:00"):
            self.assertRaises(UnexpectedError, update_geocodes, *BELFAST, geocodes)

    @requests_mock.mock()
    def test_update_geocodes_bad_file(self, m):
        m.get(GEOCODES_LISTING, json=LISTING)
        m.get(GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_07_31.json", text="<html>")
        self.assertRaises(UnexpectedError, update_geocodes, *BELFAST)
        m.get(GEOCODES_FILES + "master/MeteoAlarm_Geocodes_2026_07_31.json",
              text='{"features": [{"type": "Feature", ')
        self.assertRaises(UnexpectedError, update_geocodes, *BELFAST)

    @staticmethod
    def _register_norway(m):
        m.get("/feeds/meteoalarm-legacy-atom-norway", text=read("norway_sample.xml"))
        for uuid in NORWAY:
            m.get(re.compile("/api/v1/warnings/feeds-norway/" + uuid),
                  text=read(f"norway_sample_detail_{uuid}.xml"))

    @staticmethod
    def _register_multiple(m):
        m.get("/feeds/meteoalarm-legacy-atom-united-kingdom", text=read("uk_multiple_sample.xml"))
        for uuid, name in MULTIPLE:
            m.get("/api/v1/warnings/feeds-united-kingdom/" + uuid,
                  text=read(f"uk_multiple_detail_{name}.xml"))


if __name__ == "__main__":
    unittest.main()
