from starlette.requests import Request

from app.interfaces.client_ip import get_client_ip


def _request(peer: str, forwarded_for: str = "") -> Request:
    headers = []
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("api", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_untrusted_peer_cannot_spoof_forwarded_for():
    request = _request("203.0.113.10", "1.2.3.4")

    assert (
        get_client_ip(
            request,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "203.0.113.10"
    )


def test_trusted_proxy_chain_is_walked_from_nearest_to_farthest():
    request = _request("10.0.0.2", "1.2.3.4, 10.0.0.3")

    assert (
        get_client_ip(
            request,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "1.2.3.4"
    )


def test_malformed_forwarded_chain_fails_closed_to_socket_peer():
    request = _request("10.0.0.2", "not-an-ip")

    assert (
        get_client_ip(
            request,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "10.0.0.2"
    )


def test_single_trusted_layer_ignores_forged_leading_hops():
    # One ingress proxy (10.0.0.9) appends the real peer it saw. A client that
    # prepends extra forged XFF entries cannot impersonate them: the walk stops
    # at the first untrusted hop from the right (the address the proxy appended).
    request = _request("10.0.0.9", "8.8.8.8, 9.9.9.9, 203.0.113.7")

    assert (
        get_client_ip(
            request,
            trusted_proxy_cidrs=("10.0.0.9/32",),
        )
        == "203.0.113.7"
    )


def test_no_forwarded_header_uses_socket_peer():
    request = _request("10.0.0.2")

    assert (
        get_client_ip(
            request,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        == "10.0.0.2"
    )


def test_untrusted_peer_with_narrow_config_uses_socket_peer():
    # A sandbox dialing the API directly is not within the (narrow) ingress CIDR,
    # so its forged X-Forwarded-For is disregarded and the socket peer wins.
    request = _request("172.20.0.5", "1.2.3.4")

    assert (
        get_client_ip(
            request,
            trusted_proxy_cidrs=("10.0.0.9/32",),
        )
        == "172.20.0.5"
    )
