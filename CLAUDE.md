# meteoalarmapi

A Python wrapper for MeteoAlarm's legacy Atom feeds (`feeds.meteoalarm.org`) and the CAP documents they link to. A maintained fork of [meteoalertapi](https://github.com/rolfberkenbosch/meteoalert-api), meant for Home Assistant's `meteoalarm` integration. Maintainers: @mastermc0 and @jjprz.

## Layout
- `meteoalarmapi/meteoalert.py`: the library and the sync client. `Meteoalert(country, province=None, language="en-GB", latitude=None, longitude=None)` (the location arguments are 0.5.0), with `get_alert()` (most severe, or `{}`) and `get_alerts()` (all, ranked). Matching, CAP parsing and ranking live in `_MeteoalertBase`, which does no I/O.
- `meteoalarmapi/aio.py`: the asyncio client (0.6.0). `AsyncMeteoalert(..., session=...)` with `async_get_alert()`/`async_get_alerts()`, and `async_update_geocodes(session, ...)`, on aiohttp. It reuses `_MeteoalertBase` and the pure geocodes helpers, so only the HTTP layer differs. `meteoalarmapi/__init__.py` imports it lazily, so the sync API works without aiohttp.
- `tests/`: pytest with `requests_mock` (sync) and `aioresponses` (async, `test_async.py`, with a small aiohttp 3.14 shim to drop once aioresponses releases pnuckowski/aioresponses#288 and #292). Fixtures are real feeds saved as XML (`uk_multiple_*`, `norway_sample*`). Patch `_utcnow` to fix the clock; never depend on today's date.

## Checks
```bash
pip install -e ".[test]" ruff
ruff check .
pytest tests
```
CI runs both on Python 3.10–3.14 (`.github/workflows/tests.yml`).

`python scripts/sweep_geocodes.py` checks point matching against MeteoAlarm's live geocodes file and every country feed. It needs the network, so it isn't in CI: run it before a release and when MeteoAlarm publishes a new geocodes file.

## Rules for changes
- `main` is protected: every change goes through a pull request, and all five `test (3.x)` jobs must pass. Don't push to `main` or force-push it.
- Keep the public API compatible: `Meteoalert`, `get_alert()`, `get_alerts()`, the alert keys, and the exceptions `WrongCountry`, `ServerError` and `UnexpectedError`. Home Assistant depends on them. The same goes for `AsyncMeteoalert` once released, and a change to one client goes in the other too.
- Semantic versioning in 0.x: new features bump the minor version, fixes the patch version. A breaking change needs a minor bump and a CHANGELOG entry saying so.
- Add a CHANGELOG entry under the unreleased version for any user-visible change, and update the README's "What changed from meteoalertapi" list if relevant.
- Every network call has a timeout (`TIMEOUT`; with aiohttp, as `sock_connect`/`sock_read`, not a total, so the 33 MB geocodes file isn't cut off). Errors raise the library's exceptions, never print.
- Python 3.10+. Required dependencies are only `requests` and `xmltodict`; `aiohttp` is the optional `async` extra (`pip install "meteoalarmapi[async]"`) and is in the `test` extra.

## Releases
- Publishing a GitHub release `vX.Y.Z` runs `.github/workflows/release.yml`: tests, build, then upload to PyPI by trusted publishing (environment `pypi`). No tokens are stored.
- Before the release, set the version in `pyproject.toml` and the date in `CHANGELOG.md`, through a PR.
- `.github/workflows/testpypi.yml` (manual: Actions → testpypi → Run workflow) does a dry run to test.pypi.org. TestPyPI rejects a version it already has, so use a dev version such as `0.4.1.dev1` for repeat runs.
- Install from TestPyPI with `pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ meteoalarmapi`.

## Plan
The roadmap is in the GitHub issues and the [project board](https://github.com/users/mastermc0/projects/1). 0.4.0 (#1), 0.5.0 (#3, location matching) and 0.6.0 (#24, async client) are released. Next is the Home Assistant work: the Bronze UI setup (#5), then switching the integration to the async client (#25), then #13–#15 and #6–#8.
