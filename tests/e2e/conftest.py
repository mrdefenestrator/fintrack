"""Shared e2e fixtures: every page must work with no outbound network access.

All front-end assets are served by the app itself (web/static/dist/, built by
`npm run build`). This wraps pytest-playwright's ``context`` fixture so any
request to a host other than the local test server is aborted and fails the
test, which keeps a CDN <script>/<link> from creeping back into a template.
"""

from urllib.parse import urlparse

import pytest

_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@pytest.fixture
def context(context):
    blocked: list[str] = []

    def _guard(route):
        url = route.request.url
        if urlparse(url).hostname in _LOCAL_HOSTS:
            route.fallback()
        else:
            blocked.append(url)
            route.abort()

    context.route("**/*", _guard)
    yield context
    assert not blocked, (
        "Page requested non-local URLs (the app must work offline): "
        + ", ".join(sorted(set(blocked)))
    )
