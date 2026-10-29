# meteoalarmapi

A Python wrapper for MeteoAlarm's legacy Atom feeds (`feeds.meteoalarm.org`) and the CAP documents they link to. A maintained fork of [meteoalertapi](https://github.com/rolfberkenbosch/meteoalert-api), meant for Home Assistant's `meteoalarm` integration. Maintainers: @mastermc0 and @jjprz.

## Layout
- `meteoalarmapi/meteoalert.py`: the whole library. `Meteoalert(country, province=None, language="en-GB", latitude=None, longitude=None)` (the location arguments are 0.5.0), with `get_alert()` (most severe, or `{}`) and `get_alerts()` (all, ranked).
- `tests/`: pytest with `requests_mock`. Fixtures are real feeds saved as XML (`uk_multiple_*`, `norway_sample*`). Patch `_utcnow` to fix the clock; never depend on today's date.

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
- Keep the public API compatible: `Meteoalert`, `get_alert()`, `get_alerts()`, the alert keys, and the exceptions `WrongCountry`, `ServerError` and `UnexpectedError`. Home Assistant depends on them.
- Semantic versioning in 0.x: new features bump the minor version, fixes the patch version. A breaking change needs a minor bump and a CHANGELOG entry saying so.
- Add a CHANGELOG entry under the unreleased version for any user-visible change, and update the README's "What changed from meteoalertapi" list if relevant.
- Every network call has a timeout (`TIMEOUT`). Errors raise the library's exceptions, never print.
- Python 3.10+ and only `requests` and `xmltodict` as dependencies.

## Releases
- Publishing a GitHub release `vX.Y.Z` runs `.github/workflows/release.yml`: tests, build, then upload to PyPI by trusted publishing (environment `pypi`). No tokens are stored.
- Before the release, set the version in `pyproject.toml` and the date in `CHANGELOG.md`, through a PR.
- `.github/workflows/testpypi.yml` (manual: Actions → testpypi → Run workflow) does a dry run to test.pypi.org. TestPyPI rejects a version it already has, so use a dev version such as `0.4.1.dev1` for repeat runs.
- Install from TestPyPI with `pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ meteoalarmapi`.

## Plan
The roadmap is in the GitHub issues and the [project board](https://github.com/users/mastermc0/projects/1). Releases go in order: 0.4.0 (#1), then 0.5.0 (location matching, PR #16, which stays a draft until 0.4.0 is out). The Home Assistant work (#2, #5, #13–#15) builds on them.
