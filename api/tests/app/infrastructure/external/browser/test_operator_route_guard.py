import pytest

from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser


class _Request:
    def __init__(self, url: str) -> None:
        self.url = url


class _Route:
    def __init__(self, url: str) -> None:
        self.request = _Request(url)
        self.continued = False
        self.aborted = None

    async def continue_(self):
        self.continued = True

    async def abort(self, reason):
        self.aborted = reason


@pytest.mark.asyncio
async def test_network_route_blocks_redirects_and_subresources_outside_allowlist():
    browser = PlaywrightBrowser(
        "http://sandbox:9222",
        allowed_domains=frozenset({"ops-console"}),
    )
    allowed = _Route("https://ops-console/assets/app.js")
    blocked = _Route("https://tracker.example/collect")

    await browser._guard_route(allowed)
    await browser._guard_route(blocked)

    assert allowed.continued is True
    assert blocked.aborted == "blockedbyclient"


def test_operator_browser_fails_closed_on_empty_allowlist():
    with pytest.raises(ValueError, match="requires at least one allowed domain"):
        PlaywrightBrowser("http://sandbox:9222", allowed_domains=frozenset())
