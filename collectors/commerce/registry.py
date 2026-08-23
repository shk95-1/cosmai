"""Which sources exist, by key.

origin: service/trend-radar/src/trend_radar/registry.py -- ported for #7. Checked at registration, not
at first use: a source missing an attribute fails at import (tool/checks/test), not as a log line in the
middle of an unattended run.
"""

from __future__ import annotations

from collectors.commerce.contract import Source

SOURCES: dict[str, type[Source]] = {}

_REQUIRED_ATTRS = ("key", "policy", "datasets")
_REQUIRED_METHODS = ("seeds", "parse")


def register[T: type](cls: T) -> T:
    for name in _REQUIRED_ATTRS + _REQUIRED_METHODS:
        if not hasattr(cls, name):
            raise TypeError(f"{cls.__name__} cannot be registered: it has no {name!r}")

    key = cls.key  # pyright: ignore[reportAttributeAccessIssue]
    if not cls.datasets:  # pyright: ignore[reportAttributeAccessIssue]
        raise ValueError(f"{cls.__name__} declares no datasets, so it would collect nothing")
    if key in SOURCES:
        raise ValueError(f"two sources claim the key {key!r}: {SOURCES[key].__name__} and {cls.__name__}")

    SOURCES[key] = cls  # pyright: ignore[reportArgumentType]
    return cls


def get_source(key: str) -> type[Source]:
    try:
        return SOURCES[key]
    except KeyError:
        known = ", ".join(sorted(SOURCES)) or "none registered"
        raise KeyError(f"no source named {key!r}; known: {known}") from None
