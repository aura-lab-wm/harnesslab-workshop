"""Host allow-list and cross-site write protection for the local server.

Two checks, both enforced before routing (registered so they run OUTERMOST -- see the wiring note
in app.py -- so a rejected request never reaches the private-results guard, the SPA catch-all, or
any route handler):

1. **Host allow-list** -- defeats DNS rebinding. An attacker who gets `attacker.example` to resolve
   to 127.0.0.1 can make a real, logged-in browser send a request whose *connection* is local but
   whose `Host` header still reads `attacker.example` (that is what the browser puts there, because
   that is the URL it thinks it loaded). Without a Host check, that request is indistinguishable
   from one typed into this machine's own address bar, and DNS rebinding turns "the app only listens
   on 127.0.0.1" into no protection at all.
2. **Same-origin write check** -- defeats CSRF against every unsafe method (POST/PUT/PATCH/DELETE).
   `Origin` cannot be set by page script (it is a forbidden header for fetch/XHR, and a browser sets
   it itself on a cross-origin `<form>` POST too), so if it is present it is trustworthy: reject it
   unless its host is on the same allow-list as (1). `Sec-Fetch-Site` is the same idea from a newer,
   even harder to spoof signal, checked the same way when a browser sends it. Both checks are
   skipped when the header is simply ABSENT -- that is what a same-machine `curl`, this repo's own
   TestClient-based tests, or another CLI look like, and none of them can be a hostile *web page*.
   Finally, a strict `application/json` Content-Type requirement on writes closes the one class of
   cross-site request a browser can still fire WITHOUT a CORS preflight and, in principle, without
   an Origin an unusual/older client bothers to send: a "simple request" -- a plain `<form>` POST or
   `fetch(..., {mode: 'no-cors'})` -- can only ever carry `application/x-www-form-urlencoded`,
   `multipart/form-data` or `text/plain`, NEVER `application/json`. All three of those safelisted
   simple-request types are rejected here -- not just two of them -- because no route on this app
   reads a form body, so there is nothing multipart buys a legitimate caller that JSON does not.

Fails closed: anything not affirmatively allowed is rejected, with a short plain-text reason.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse

#: httpx's/Starlette's TestClient talks to an app under exactly this Host by default, and every
#: existing test in this repo that hits the real `app` singleton (test_capture_control.py,
#: test_capture_scope.py, test_assist_scope.py, test_harness_tools.py, test_fork.py,
#: test_static_export_redaction.py, test_packaging.py, test_capture_api.py -- none of them pass a
#: custom `base_url`) relies on that default.
#:
#: Trusting the literal string "testserver" as a Host UNCONDITIONALLY would reopen the exact
#: DNS-rebinding hole this guard exists to close: anyone who gets the operator's resolver to answer
#: that one-label name with 127.0.0.1 (a poisoned DHCP search-domain, an mDNS responder, a
#: corporate/Docker-internal DNS zone that already defines it) could ride a real browser tab past the
#: Host check with `Host: testserver` on a genuine, real-socket request. So this name is trusted only
#: when the request did NOT arrive over a real socket at all: Starlette's TestClient talks to the
#: ASGI app in-process over a fake transport that hard-codes `client=("testclient", 50000)` as the
#: peer address for every call unless a test overrides it (see `_is_in_process_test_client` below,
#: and `starlette.testclient.TestClient.__init__`'s `client` parameter). No real TCP connection can
#: ever report that as its peer -- "testclient" is not an address any socket resolves to -- so it
#: distinguishes "this is the app's own test suite talking to itself" from "this is a live request
#: that merely claims to be testserver" the same way a Host header alone cannot. A production process
#: (started via `harnesslab.__main__`, reached over an actual socket) never matches it, so `Host:
#: testserver` is correctly rejected there. Doing it this way keeps every pre-existing test honest
#: without editing files this agent does not own to add a `base_url=` this task never asked for.
_TEST_CLIENT_HOST = "testserver"
_TEST_CLIENT_PEER = ("testclient", 50000)


def _is_in_process_test_client(request: Request) -> bool:
    client = request.client
    return client is not None and (client.host, client.port) == _TEST_CLIENT_PEER


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Comma-separated extra hostnames (no scheme, no port) trusted in addition to loopback and the
#: bound --host. Read fresh on every request (not cached at import time) so it can be changed
#: without restarting the process -- and so a test can set it mid-test.
ENV_EXTRA_HOSTS = "HARNESSLAB_ALLOWED_HOSTS"

#: The interface uvicorn actually bound, so Docker's `Host: 127.0.0.1:<port>` (already covered by
#: the loopback set above) and a deliberate `--host 192.168.x.x` (not covered by anything else) both
#: pass. Set once, from __main__.py, right before `uvicorn.run`; read lazily (a plain module-level
#: dict, not a constant captured at import time) so it can be set after `app` already exists -- the
#: same pattern `private_results_guard` uses for `M.RUNS_ROOT`.
_BOUND_HOST = {"value": "127.0.0.1"}

#: A bound interface that means "every interface" -- never itself a Host/Origin to trust.
_WILDCARD_BINDS = frozenset({"", "0.0.0.0", "::"})

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_JSON_CT = "application/json"


def set_bound_host(host: str) -> None:
    """Record the --host uvicorn is about to bind. Call this before `uvicorn.run`, not after."""
    _BOUND_HOST["value"] = (host or "").strip()


def _normalize_host(value: str) -> str:
    """`host[:port]` or `[ipv6][:port]` -> the bare, lower-cased host, with one trailing "." (a
    fully-qualified domain name's root label) stripped. Exact-match only against this -- never a
    suffix/substring check -- which is precisely what makes `127.0.0.1.evil.example` (not EQUAL to
    `127.0.0.1`) and `localhost.evil.example` (not equal to `localhost`) fail closed rather than
    needing their own special-case rule.
    """
    v = (value or "").strip()
    if v.startswith("["):
        end = v.find("]")
        v = v[1:end] if end != -1 else v[1:]
    elif ":" in v:
        head, _, tail = v.rpartition(":")
        if head and tail.isdigit():
            v = head
    v = v.lower()
    if v.endswith(".") and v != ".":
        v = v[:-1]
    return v


#: The port uvicorn is about to listen on; only used to name the one GitHub Codespaces forwarding
#: host that points at it (see codespaces_host). Set from __main__.py together with the bound host.
_BOUND_PORT = {"value": None}


def set_bound_port(port: int | None) -> None:
    _BOUND_PORT["value"] = port


def codespaces_host(port: int | None = None, env=None) -> str | None:
    """The forwarded hostname GitHub Codespaces gives this server's port, or None outside one.

    A codespace reaches a forwarded port at `https://<CODESPACE_NAME>-<port>.<forwarding domain>`,
    and its proxy passes that name through as the Host header, so without it every request from the
    student's browser would be refused as an untrusted Host. Exactly this one name is trusted --
    derived from the environment variables Codespaces itself sets inside the container, for the
    port actually bound -- never a suffix such as `*.app.github.dev`, which would trust every other
    codespace on GitHub. The forwarded port is private to the codespace's owner by default (GitHub
    authenticates the request before it reaches this process), which is what makes naming it safe.
    """
    env = os.environ if env is None else env
    if env.get("CODESPACES") != "true":
        return None
    name = (env.get("CODESPACE_NAME") or "").strip()
    domain = (env.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN") or "").strip().strip(".")
    port = _BOUND_PORT["value"] if port is None else port
    if not name or not domain or not port:
        return None
    return _normalize_host(f"{name}-{int(port)}.{domain}")


def _allowed_hosts() -> set[str]:
    hosts = set(_LOOPBACK_HOSTS)
    bound = _normalize_host(_BOUND_HOST["value"])
    if bound and bound not in _WILDCARD_BINDS:
        hosts.add(bound)
    cs = codespaces_host()
    if cs:
        hosts.add(cs)
    for h in os.environ.get(ENV_EXTRA_HOSTS, "").split(","):
        h = _normalize_host(h)
        if h:
            hosts.add(h)
    return hosts


def _origin_host(origin: str) -> str | None:
    """The Origin header's hostname, or None when it is not a plain http(s) origin -- an opaque
    `null` origin (a sandboxed iframe, a `data:`/`file:` page, a cross-scheme POST), a non-http(s)
    scheme, or something unparsable all come back None, and None is never in the allow-list."""
    if not origin or origin == "null":
        return None
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower()


def security_guard():
    """The ASGI middleware factory. `app.middleware("http")(security_guard())` -- see the wiring
    note in app.py for why it must be registered AFTER private_results_guard to run BEFORE it."""

    async def guard(request: Request, call_next):
        allowed = _allowed_hosts()
        if _is_in_process_test_client(request):
            allowed = allowed | {_TEST_CLIENT_HOST}

        host_header = request.headers.get("host")
        if not host_header or _normalize_host(host_header) not in allowed:
            return PlainTextResponse("Forbidden: untrusted Host", status_code=400)

        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None:
                oh = _origin_host(origin)
                if oh is None or oh not in allowed:
                    return PlainTextResponse("Forbidden: untrusted Origin", status_code=403)

            sec_fetch_site = request.headers.get("sec-fetch-site")
            if sec_fetch_site is not None and sec_fetch_site not in ("same-origin", "none"):
                return PlainTextResponse("Forbidden: cross-site request", status_code=403)

            content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type != _JSON_CT:
                return PlainTextResponse("Forbidden: writes need a JSON body", status_code=403)

        return await call_next(request)

    return guard
