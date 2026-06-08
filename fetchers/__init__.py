"""Venue fetcher registry. build.py iterates over REGISTRY."""
from . import dnalounge, chapel, symphony, opera, ballet, sfjazz, audio, tipples

REGISTRY = [
    dnalounge,
    chapel,
    symphony,
    opera,
    ballet,
    sfjazz,
    audio,
    tipples,
]
