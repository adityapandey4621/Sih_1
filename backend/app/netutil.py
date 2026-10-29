"""Who is really calling? The peer address, or - only behind a configured number of trusted reverse proxies - the address they report.

X-Forwarded-For is client-controllable: a caller can send any value. It is therefore ignored unless TRUSTED_PROXY_COUNT says how many proxies append
to it; then the entry added by the outermost trusted proxy (counted from the right) is used, and only if it is a valid IP address.
"""

import ipaddress


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted_proxy_count: int) -> str | None:
    if trusted_proxy_count <= 0 or not forwarded_for:
        return peer
    parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
    if len(parts) < trusted_proxy_count:
        return peer  # fewer hops than expected: the header was not produced by our proxies
    candidate = parts[-trusted_proxy_count]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer
