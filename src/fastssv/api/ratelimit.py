"""Shared slowapi limiter used by route decorators.

Why this exists at module level: ``@limiter.limit(...)`` and
``@limiter.exempt`` are evaluated at import time, before
:func:`fastssv.api.app.create_app` runs, so the limiter instance has to be
addressable from the route modules. Keeping a single shared instance also
avoids per-request setup cost and keeps slowapi's internal route registry
consistent across requests.

The configured limit string (e.g. ``"60/minute"``) lives in a small mutable
holder so :func:`create_app` can rewrite it at startup from
:class:`fastssv.api.config.Settings`. Tests that build an app with a
different ``rate_limit`` value will see it picked up the next time a
decorated route is hit.

Client identity comes from :func:`slowapi.util.get_remote_address`, which
reads ``request.client.host``. When the container runs behind a reverse
proxy, gunicorn's ``--forwarded-allow-ips`` (configured via the
``FORWARDED_ALLOW_IPS`` env var in ``deploy/Dockerfile``) tells uvicorn's
:class:`uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware` to trust
``X-Forwarded-For`` and ``X-Forwarded-Proto`` from that source. Once
trusted, ``request.client.host`` becomes the leftmost XFF entry — i.e.
the original client — so we don't need to walk the XFF chain ourselves.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Default matches the API's documented default in deploy/.env.example.
# Overwritten from settings.rate_limit by create_app().
_DEFAULT_RATE_LIMIT = "60/minute"
_current_rate_limit: str = _DEFAULT_RATE_LIMIT


def configure_rate_limit(value: str) -> None:
    """Set the rate-limit string read by ``rate_limit_value`` callables."""
    global _current_rate_limit
    _current_rate_limit = value


def rate_limit_value() -> str:
    """Return the active rate-limit string. Used as a callable limit_value
    on ``@limiter.limit(...)`` so changing settings between app builds
    (e.g. across tests) takes effect without re-decorating routes."""
    return _current_rate_limit


limiter = Limiter(key_func=get_remote_address)
