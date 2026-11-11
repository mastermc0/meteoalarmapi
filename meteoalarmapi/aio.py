"""The asyncio client, on aiohttp (pip install "meteoalarmapi[async]").

AsyncMeteoalert and async_update_geocodes() work like Meteoalert and
update_geocodes(), with the same alerts, ranking and exceptions, but fetch
with an aiohttp.ClientSession that the caller owns and passes in.
"""

import asyncio
import json

import aiohttp

from . import meteoalert as _core
from .meteoalert import (
    GEOCODES_FALLBACK,
    GEOCODES_FILE,
    GEOCODES_LISTING,
    ServerError,
    UnexpectedError,
    _check_status,
    _is_fresh,
    _is_same_point,
    _MeteoalertBase,
    _newest_geocodes_name,
    _rechecked,
    resolve_geocodes,
)


def _timeout():
    # Like requests' timeout=TIMEOUT: a limit on connecting and on each read,
    # not on the whole download, which for the geocodes file is 33 MB
    return aiohttp.ClientTimeout(sock_connect=_core.TIMEOUT, sock_read=_core.TIMEOUT)


async def _request(session, url):
    """(status code, body), with the body read only for a 200"""
    try:
        async with session.get(url, timeout=_timeout()) as response:
            body = await response.read() if response.status == 200 else None
            return response.status, body
    except Exception as err:
        # asyncio.TimeoutError has no message of its own
        raise UnexpectedError(str(err) or f"{type(err).__name__} fetching {url}") from err


async def _get(session, url):
    """The body for url, raising ServerError/UnexpectedError unless it's a 200"""
    status, body = await _request(session, url)
    _check_status(status)
    return body


async def _latest_geocodes_name(session):
    try:
        listing = json.loads(await _get(session, GEOCODES_LISTING))
    except ValueError as err:
        raise UnexpectedError(f"Unexpected geocodes listing: {err}") from err
    return _newest_geocodes_name(listing)


def _decode_and_resolve(point, body, version):
    return resolve_geocodes(*point, body.decode("utf-8"), version)


async def async_update_geocodes(session, latitude, longitude, geocodes=None, outdated=False):
    """update_geocodes() for asyncio, fetching with session (an aiohttp.ClientSession).

    Reading the 33 MB geocodes file runs in the event loop's default executor,
    so it doesn't block the loop.
    """
    point = (float(latitude), float(longitude))
    same_point = _is_same_point(point, geocodes)
    if same_point and _is_fresh(geocodes, outdated):
        return geocodes

    ref = "master"
    try:
        version = await _latest_geocodes_name(session)
    except (ServerError, UnexpectedError):
        if same_point:
            raise
        # A first setup shouldn't depend on the listing
        ref, version = GEOCODES_FALLBACK

    if same_point and geocodes.get("version") == version:
        return _rechecked(geocodes)
    body = await _get(session, GEOCODES_FILE.format(ref=ref, name=version))
    return await asyncio.get_running_loop().run_in_executor(
        None, _decode_and_resolve, point, body, version)


class AsyncMeteoalert(_MeteoalertBase):
    """Alerts from MeteoAlarm for asyncio, fetched with an aiohttp.ClientSession.

    Takes the same arguments as Meteoalert, plus session, which the caller
    owns (this never closes it). In Home Assistant, pass
    async_get_clientsession(hass).
    """

    def __init__(self, country, province=None, language="en-GB",
                 latitude=None, longitude=None, geocodes=None, *, session):
        super().__init__(country, province, language, latitude, longitude, geocodes)
        self.session = session

    async def async_get_alert(self):
        """The most severe alert for the location, or {} when there is none"""
        alerts = await self.async_get_alerts()
        return alerts[0] if alerts else {}

    async def async_get_alerts(self, include_expired=True):
        """Every alert for the location, most severe first. See Meteoalert.get_alerts().

        The alerts' CAP documents are fetched concurrently.
        """
        urls = self._cap_urls(await self._async_fetch(self._feed_url()))
        # Wait for every fetch before raising, so none is left running
        results = await asyncio.gather(
            *(self._async_fetch(url, missing_ok=True) for url in urls), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return self._ranked(results, include_expired)

    async def _async_fetch(self, url, missing_ok=False):
        """The response text, or None for a 404/410 when missing_ok"""
        status, body = await _request(self.session, url)
        if self._is_missing(status, missing_ok):
            return None
        return body.decode("utf-8")
