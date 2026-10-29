# meteoalarmapi

![PyPI](https://img.shields.io/pypi/v/meteoalarmapi.svg?style=for-the-badge)

An unofficial Python wrapper for [MeteoAlarm.org](https://meteoalarm.org) (European weather alerts), which can be used to check whether a given province in your country currently has a weather alert.

This is a maintained fork of [meteoalertapi](https://github.com/rolfberkenbosch/meteoalert-api) by Rolf Berkenbosch, which has had no releases since February 2024. It keeps the same `Meteoalert` class and alert fields. See [What changed from meteoalertapi](#what-changed-from-meteoalertapi).

## Installation

```console
pip install meteoalarmapi
```

## Code example

```python
from meteoalarmapi import Meteoalert

# Find your country and province on https://meteoalarm.org/ or https://feeds.meteoalarm.org/
# The province can be its name (matched as a regular expression) or its EMMA_ID code.
meteo = Meteoalert('united-kingdom', 'Highlands & Eilean Siar')

# The most severe current alert, or {} when there is none
print(meteo.get_alert())

# Every alert for the province, most severe first
for alert in meteo.get_alerts():
    print(alert['event'], alert['severity'], alert['onset'], alert['expires'])
```

Some countries, such as Norway, don't name provinces in their feed and only give each alert's area as a polygon. For those, give a latitude and longitude instead. You get the alerts whose area contains that point:

```python
meteo = Meteoalert('norway', latitude=60.392, longitude=5.324)  # Bergen
print(meteo.get_alerts())
```

This works for any feed that includes polygons, including the UK's. With both a province and a point, you get alerts matching either.

Each alert also has the CAP message fields `identifier`, `message_type` (`Alert`, `Update` or `Cancel`), `references` and `sent`, so you can follow a warning across updates and tell when one is cancelled.

Some feeds keep warnings for a while after they end. Pass `include_expired=False` to leave expired alerts out. Future alerts, which haven't started yet, are kept:

```python
for alert in meteo.get_alerts(include_expired=False):
    print(alert['event'], alert['onset'], alert['expires'])
```

Alerts are ranked with current ones first, then by awareness level (`awareness_level`, e.g. `3; Orange; Severe`), then by CAP severity. Alerts that rank equally keep their order in the feed.

## What changed from meteoalertapi

- The package is now imported as `meteoalarmapi` instead of `meteoalertapi`.
- `get_alert()` returns the most severe current alert. Before, it returned the first match in the feed, so a yellow warning could hide an amber one.
- New `get_alerts()` returns every matching alert, most severe first.
- `get_alerts(include_expired=False)` leaves out expired alerts, which some feeds keep for a while. Future alerts are kept.
- Alerts can be matched by a latitude and longitude inside the alert's area, for feeds without provinces (meteoalert-api#35) (0.5.0).
- `awareness_level`, `awareness_type` and the other CAP parameters are now included for feeds with a single language, such as the UK's. Before, they were silently dropped.
- An alert listed several times in the feed (once per language or area) is returned and fetched only once.
- Each alert includes the CAP `identifier`, `message_type`, `references` and `sent`.
- Python 3.10 or newer is required.

## Changelog

See the [CHANGELOG](./CHANGELOG.md) file.

## License

MIT. Original work copyright (c) 2018 Rolf Berkenbosch.
