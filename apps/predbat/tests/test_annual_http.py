# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
# fmt on

"""
Tests for the shared JSON-over-HTTP helper the annual prediction fetchers use.

annual_http.fetch_json is the single implementation behind the weather, tariff and consumption
downloads, so its failure behaviour decides what all three do when a provider is slow, rate-limited
or returns something that is not JSON: return None and log, never raise into the caller's loop.
"""

import asyncio

import aiohttp

from annual_http import fetch_json


class FakeResponse:
    """Minimal aiohttp response stand-in with a status and a JSON body."""

    def __init__(self, status=200, payload=None, json_error=None):
        self.status = status
        self.payload = payload
        self.json_error = json_error

    async def __aenter__(self):
        """Enter the response context."""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """Leave the response context."""
        return False

    async def json(self):
        """Return the decoded body, or raise the configured decode error."""
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    """Session stand-in that records the requests made and replays queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.closed = False

    def get(self, url, headers=None, timeout=None):
        """Record one request and return the next queued response."""
        self.requests.append({"url": url, "headers": headers, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def __aenter__(self):
        """Enter the session context."""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """Leave the session context, marking it closed."""
        self.closed = True
        return False


def _check(name, value, expected):
    """Compare a value to its expectation, printing a diagnostic and returning 1 on mismatch."""
    if value != expected:
        print("ERROR: {}: got {!r}, expected {!r}".format(name, value, expected))
        return 1
    return 0


def _check_true(name, value):
    """Assert a value is truthy, printing a diagnostic and returning 1 when it is not."""
    if not value:
        print("ERROR: {}: expected a truthy value, got {!r}".format(name, value))
        return 1
    return 0


def _fetch(session, url="https://example.invalid/data", log_prefix="Test request", headers=None, timeout_seconds=5):
    """Run one fetch_json call against a fake session, returning (result, log lines)."""
    lines = []
    result = asyncio.run(fetch_json(url, lines.append, log_prefix, headers or {}, timeout_seconds, session=session))
    return result, lines


def test_a_good_response_is_decoded(my_predbat):
    """A 200 with a JSON body comes back decoded, with the headers and timeout passed through."""
    print("*** Running test: a good response is decoded")
    failed = 0
    session = FakeSession([FakeResponse(200, {"hourly": [1, 2, 3]})])

    result, lines = _fetch(session, headers={"Authorization": "Basic abc"}, timeout_seconds=11)

    failed |= _check("good: body decoded", result, {"hourly": [1, 2, 3]})
    failed |= _check("good: nothing logged", lines, [])
    failed |= _check("good: one request made", len(session.requests), 1)
    failed |= _check("good: headers passed through", session.requests[0]["headers"], {"Authorization": "Basic abc"})
    failed |= _check("good: timeout applied", session.requests[0]["timeout"].total, 11)

    # 201 counts as success too - some providers answer a query with a created resource
    session = FakeSession([FakeResponse(201, {"ok": True})])
    result, lines = _fetch(session)
    failed |= _check("good: 201 accepted", result, {"ok": True})
    return failed


def test_a_bad_status_returns_nothing_and_says_which_caller(my_predbat):
    """
    A non-success status returns None and names the caller in the warning.

    The three fetchers share this implementation, so the prefix is the only thing in the log that
    says whether it was weather, tariff or consumption that failed.
    """
    print("*** Running test: a bad status returns nothing and names the caller")
    failed = 0

    for status in (400, 401, 429, 500, 503):
        session = FakeSession([FakeResponse(status)])
        result, lines = _fetch(session, log_prefix="Octopus rate request")
        failed |= _check("status {}: no data".format(status), result, None)
        failed |= _check("status {}: one warning".format(status), len(lines), 1)
        if lines:
            failed |= _check_true("status {}: names the caller".format(status), "Octopus rate request" in lines[0])
            failed |= _check_true("status {}: reports the status".format(status), str(status) in lines[0])
            failed |= _check_true("status {}: reports the url".format(status), "https://example.invalid/data" in lines[0])
    return failed


def test_transport_failures_are_swallowed(my_predbat):
    """A refused connection, a timeout or an undecodable body all return None rather than raise."""
    print("*** Running test: transport failures are swallowed")
    failed = 0

    for description, failure in (
        ("client error", aiohttp.ClientError("connection refused")),
        ("timeout", TimeoutError("timed out")),
    ):
        session = FakeSession([failure])
        result, lines = _fetch(session)
        failed |= _check("{}: no data".format(description), result, None)
        failed |= _check("{}: one warning".format(description), len(lines), 1)
        if lines:
            failed |= _check_true("{}: warning explains the failure".format(description), "failed" in lines[0])

    # A 200 carrying something that is not JSON is the same class of problem
    session = FakeSession([FakeResponse(200, json_error=ValueError("not json"))])
    result, lines = _fetch(session)
    failed |= _check("bad json: no data", result, None)
    failed |= _check("bad json: one warning", len(lines), 1)
    return failed


def test_a_supplied_session_is_reused_and_left_open(my_predbat):
    """
    Passing a session in reuses one connection across calls, and the helper must not close it.

    The consumption fetcher relies on this to keep a single connection across its meter-resolution
    call and every page of the download that follows.
    """
    print("*** Running test: a supplied session is reused and left open")
    failed = 0
    session = FakeSession([FakeResponse(200, {"page": 1}), FakeResponse(200, {"page": 2})])

    first, _ = _fetch(session)
    second, _ = _fetch(session)

    failed |= _check("reuse: first page", first, {"page": 1})
    failed |= _check("reuse: second page", second, {"page": 2})
    failed |= _check("reuse: both went through one session", len(session.requests), 2)
    failed |= _check("reuse: session left open for the caller", session.closed, False)
    return failed


def test_without_a_session_one_is_opened_and_closed(my_predbat):
    """With no session supplied the helper opens its own and closes it around the request."""
    print("*** Running test: without a session one is opened and closed")
    failed = 0
    opened = []

    def fake_client_session():
        """Hand back a fake session and remember it, standing in for aiohttp.ClientSession()."""
        session = FakeSession([FakeResponse(200, {"standalone": True})])
        opened.append(session)
        return session

    import annual_http

    saved = annual_http.aiohttp.ClientSession
    annual_http.aiohttp.ClientSession = fake_client_session
    try:
        lines = []
        result = asyncio.run(fetch_json("https://example.invalid/data", lines.append, "Open-Meteo request", {}, 30))
    finally:
        annual_http.aiohttp.ClientSession = saved

    failed |= _check("standalone: body decoded", result, {"standalone": True})
    failed |= _check("standalone: one session opened", len(opened), 1)
    if opened:
        failed |= _check("standalone: session closed again", opened[0].closed, True)
    return failed


def test_annual_http(my_predbat):
    """Run the annual HTTP helper tests, returning non-zero if any failed."""
    print("**** Running annual HTTP helper tests ****")
    failed = 0
    for test in (
        test_a_good_response_is_decoded,
        test_a_bad_status_returns_nothing_and_says_which_caller,
        test_transport_failures_are_swallowed,
        test_a_supplied_session_is_reused_and_left_open,
        test_without_a_session_one_is_opened_and_closed,
    ):
        failed |= test(my_predbat)
    return failed
