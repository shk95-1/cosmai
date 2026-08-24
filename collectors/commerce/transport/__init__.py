"""The live transport for collectors/commerce (#10).

origin: service/trend-radar/src/trend_radar/transport/ -- ported de-asynced, minus its `base.py`,
whose five error types and `Fetcher` protocol #7 had already put in collectors/commerce/engine.py so
the retry loop and the gate could branch on them before either transport existed.
"""

from collectors.commerce.transport.browser import DEFAULT_PROFILE_DIR, BrowserFetcher
from collectors.commerce.transport.challenge import challenge_reason, raise_for_refusal
from collectors.commerce.transport.factory import DispatchingFetcher, LiveFetchers, build_fetcher
from collectors.commerce.transport.http import HttpFetcher

__all__ = [
    "DEFAULT_PROFILE_DIR",
    "BrowserFetcher",
    "DispatchingFetcher",
    "HttpFetcher",
    "LiveFetchers",
    "build_fetcher",
    "challenge_reason",
    "raise_for_refusal",
]
