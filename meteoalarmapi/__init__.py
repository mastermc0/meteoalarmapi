from .meteoalert import (
    Meteoalert,
    ServerError,
    UnexpectedError,
    WrongCountry,
    update_geocodes,
)

__all__ = [
    "AsyncMeteoalert",
    "Meteoalert",
    "ServerError",
    "UnexpectedError",
    "WrongCountry",
    "async_update_geocodes",
    "update_geocodes",
]

_ASYNC = ("AsyncMeteoalert", "async_update_geocodes")


def __getattr__(name):
    # The async client needs aiohttp, an optional extra, so it's imported
    # only when asked for
    if name in _ASYNC:
        try:
            from . import aio
        except ModuleNotFoundError as err:
            if err.name != "aiohttp":
                raise
            raise ImportError(
                f"{name} needs aiohttp: pip install \"meteoalarmapi[async]\"") from err
        return getattr(aio, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
