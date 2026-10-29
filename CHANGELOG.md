# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## meteoalarmapi 0.5.0 - unreleased
- Added `latitude`/`longitude` to match alerts whose area polygon contains a point, for feeds without provinces such as Norway (meteoalert-api#35). `province` is now optional.
- Added `update_geocodes()` and `Meteoalert(geocodes=...)` to match a point against areas given only as EMMA_ID codes, as most feeds do. It downloads MeteoAlarm's geocodes file once, keeps only the point's codes, and checks for a newer file at most monthly (daily when `geocodes_outdated` is set).

## meteoalarmapi 0.4.0 - unreleased
First release of the maintained fork, published as `meteoalarmapi`.

- Renamed the package to `meteoalarmapi` (`from meteoalarmapi import Meteoalert`)
- `get_alert()` returns the most severe current alert instead of the first match in the feed
- Added `get_alerts()`, which returns every matching alert, most severe first
- `get_alerts(include_expired=False)` leaves out expired alerts, which some feeds keep for a while; future alerts are kept
- Fixed CAP parameters (awareness_level, awareness_type, ...) being dropped for single-language feeds
- Alerts include the CAP message fields `identifier`, `message_type`, `references` and `sent`
- Alerts with several feed entries (per language or area) are fetched and returned once
- A warning whose CAP document has gone (404/410) is skipped instead of raising `WrongCountry`, and the feed's 404 message mentions that MeteoAlarm may be temporarily unavailable (home-assistant/core#177632)
- Dropped Python 2 and versions older than 3.10; packaging moved to pyproject.toml
- Releases are built and published to PyPI by GitHub Actions

## 0.3.1 - 2024-02-07
###
- Match provinces by their EMMA_ID code as well as by name

## 0.0.1 - 2019-04-29
###
- Initial release

## 0.1.6 - 2019-07-12
###
- Fix issue with xml to look also over attributes.

## 0.1.7 - 2020-01-21
###
- Handle feed with one entry

## 0.1.8 - 2021-06-02
###
This is a breaking version, please modify your country!!!
It's now possible to add all european country that is on the website on
meteoalarm.org.

- Change the api from meteoalarm.eu to meteoalarm.orge

## 0.1.9 - 2021-06-07
###
Several (bug) fixes

- Add timeout for the api to meteoalarm


## 0.2.0 - 2021-06-07
###
Bump to version 0.2.0

## 0.3.0 - 2022-07-27
###
Bug fix

- Thank @attila123456 to fix the fetching bug
