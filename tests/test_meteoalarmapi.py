import os
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

import requests_mock

from meteoalarmapi import Meteoalert, ServerError, UnexpectedError, WrongCountry

# uk_multiple_sample.xml (Highlands & Eilean Siar, 24 Oct 2026) lists, in feed order:
MULTIPLE = (
    ("43e42eb0-196b-4fe7-b26e-1e3490954cc9", "yellow_rain"),
    ("ebbdecd5-fa00-40ec-9fa2-a4a5ea13b424", "amber_wind"),
    ("b0e83ff1-0a4b-4f2e-947a-c20b1432417c", "yellow_wind"),
)
# norway_sample.xml (26 Oct 2026): high water in Rogaland and Hordaland, and
# a flood with two entries (two areas). Detail files are named by the CAP
# document's id prefix.
NORWAY = ("ef68e1fd", "7be0d389", "15082db6")


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
    def test_alert_fetched_once(self, m):
        """The flood has two feed entries (one per area) sharing a CAP document"""
        self._register_norway(m)

        alerts = Meteoalert("Norway", "stlandet").get_alerts()
        self.assertEqual([a["event"] for a in alerts], ["Flood"])
        cap_requests = [r for r in m.request_history if "15082db6" in r.url]
        self.assertEqual(len(cap_requests), 1)

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
