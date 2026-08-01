# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt: off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
# fmt: on

"""Unit tests for the Predbat MCP (Model Context Protocol) server.

The MCP server exposes plan data and configuration writes over HTTP, gated by an OAuth 2.1
flow and JWT bearer tokens. It had 9% line coverage, which meant the authentication code -
token signing, audience validation, PKCE verification and the single-use authorization code
rules - had almost no tests behind it.

These tests drive the request handlers directly with a lightweight request stub rather than
standing up a real aiohttp server, so they stay fast and deterministic while still asserting
on real status codes, headers and JSON bodies.
"""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from tests.test_infra import run_async
from web_mcp import MCPServerWrapper, PredbatMCPServer

MCP_SECRET = "test-mcp-secret"
MCP_PORT = 8199


class MockRequest:
    """Stand-in for an aiohttp request, exposing only what the MCP handlers read."""

    def __init__(self, method="GET", query=None, headers=None, post_data=None, json_data=None, host="predbat.local:8199", path="/mcp", remote="10.0.0.5", raise_on_body=False):
        """Build a request with the given method, parameters, headers and body."""
        self.method = method
        self.query = dict(query or {})
        self.headers = dict(headers or {})
        self._post_data = dict(post_data or {})
        self._json_data = json_data
        self._raise_on_body = raise_on_body
        self.host = host
        self.path = path
        self.remote = remote

    async def post(self):
        """Return the form-encoded body, or fail when the test asked for a broken body."""
        if self._raise_on_body:
            raise RuntimeError("could not read the request body")
        return dict(self._post_data)

    async def json(self):
        """Return the JSON body, raising when there is none, as aiohttp does."""
        if self._raise_on_body:
            raise RuntimeError("could not read the request body")
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data


class MockHAInterface:
    """Records configuration writes made through the Home Assistant interface."""

    def __init__(self):
        """Start with no recorded writes and no injected failure."""
        self.writes = []
        self.fail = False

    async def set_state_external(self, entity_id, value):
        """Record a configuration write, or fail if the test asked for a failure."""
        if self.fail:
            raise RuntimeError("home assistant unavailable")
        self.writes.append((entity_id, value))


class MockMCPBase:
    """Minimal Predbat base for the MCP server wrapper's tool implementations."""

    def __init__(self):
        """Set up the state, entity and configuration data the tools read."""
        self.prefix = "predbat"
        self.plan_interval_minutes = 30
        self.log_messages = []
        self.states = {}
        self.dashboard_values = []
        self.args = {}
        self.CONFIG_ITEMS = []
        self.ha_config = {}
        self.ha_interface = MockHAInterface()
        self.manual_selects = []
        self.update_pending = False
        self.plan_valid = True
        self.now_utc = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        self.soc_kw = 5.0
        self.soc_max = 10.0
        self.reserve = 1.0
        self.carbon_enable = True
        self.iboost_enable = False
        self.forecast_minutes = 1440
        self.grid_power = 100
        self.battery_power = -200
        self.pv_power = 300
        self.load_power = 400
        self.running = True

    def log(self, message):
        """Record a log line."""
        self.log_messages.append(message)

    def is_running(self):
        """Report whether a plan calculation is in progress."""
        return self.running

    def get_ha_config(self, name, default):
        """Return a Home Assistant config value and the default, as the real base does."""
        return self.ha_config.get(name, default), default

    async def async_manual_select(self, config_item, value):
        """Record a manual plan override selection."""
        self.manual_selects.append((config_item, value))

    def get_state_wrapper(self, entity_id, attribute=None, default=None):
        """Return a stored state or attribute, falling back to the supplied default."""
        entry = self.states.get(entity_id)
        if entry is None:
            return default
        if attribute:
            return entry.get("attributes", {}).get(attribute, default)
        return entry.get("state", default)


def make_mcp_server(secret=MCP_SECRET, port=MCP_PORT):
    """Build a PredbatMCPServer without running ComponentBase's constructor.

    ``initialize()`` sets up everything the request handlers need, so bypassing ``__init__``
    avoids dragging in the component registry and health monitoring for these tests.
    """
    server = PredbatMCPServer.__new__(PredbatMCPServer)
    server.base = MockMCPBase()
    server.log_messages = []
    server.log = server.log_messages.append
    # Lifecycle flags normally set by ComponentBase.__init__
    server.api_started = False
    server.api_stop = False
    server.initialize(mcp_enable=True, mcp_secret=secret, mcp_port=port)
    return server


def response_json(response):
    """Decode the JSON body of an aiohttp response object."""
    return json.loads(response.body.decode())


def pkce_pair(verifier="a-code-verifier-that-is-long-enough-for-pkce"):
    """Return a PKCE verifier and its matching S256 challenge."""
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def store_auth_code(server, code="test-auth-code", client_id="client-a", redirect_uri="https://client/callback", challenge=None, expires_in_minutes=10, used=False):
    """Insert an authorization code into the server's store, as the authorize endpoint would."""
    server.authorization_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256" if challenge else None,
        "state": "xyz",
        "expires": datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
        "used": used,
    }
    return code


def _test_jwt_secret_derivation(_my_predbat):
    """The JWT signing key is derived from the MCP secret, never equal to it."""
    print("Test: the JWT signing key is derived from, not equal to, the MCP secret")
    server = make_mcp_server()

    if server.jwt_secret == MCP_SECRET:
        print("ERROR: the signing key must not be the client secret itself")
        return 1
    expected = hashlib.sha256("jwt_signing_key_{}".format(MCP_SECRET).encode()).hexdigest()
    if server.jwt_secret != expected:
        print("ERROR: unexpected signing key derivation")
        return 1

    # A different configured secret must produce a different signing key, otherwise tokens
    # would remain valid across a secret rotation
    other = make_mcp_server(secret="a-different-secret")
    if other.jwt_secret == server.jwt_secret:
        print("ERROR: rotating the MCP secret did not change the signing key")
        return 1
    return 0


def _test_generate_access_token_claims(_my_predbat):
    """Generated tokens carry the subject, audience, scope and expiry the MCP spec requires."""
    print("Test: generate_access_token sets the required claims")
    server = make_mcp_server()

    token = server.generate_access_token("client-a", resource="http://predbat.local:8199", scopes=["mcp:read"])
    payload = pyjwt.decode(token, server.jwt_secret, algorithms=["HS256"], audience="http://predbat.local:8199")

    if payload["sub"] != "client-a" or payload["client_id"] != "client-a":
        print("ERROR: the subject claims do not identify the client")
        return 1
    if payload["aud"] != "http://predbat.local:8199":
        print("ERROR: expected the resource to become the audience, got {}".format(payload["aud"]))
        return 1
    if payload["scope"] != "mcp:read":
        print("ERROR: expected the requested scope, got {}".format(payload["scope"]))
        return 1
    if payload["type"] != "access":
        print("ERROR: expected an access token type, got {}".format(payload["type"]))
        return 1
    if payload["exp"] <= payload["iat"]:
        print("ERROR: the token expiry is not in the future")
        return 1

    # Omitting the scopes grants the full default set against the default audience
    token = server.generate_access_token("client-b")
    payload = pyjwt.decode(token, server.jwt_secret, algorithms=["HS256"], audience="http://localhost:{}".format(MCP_PORT))
    if payload["scope"] != "mcp:read mcp:write mcp:control":
        print("ERROR: expected the default scopes, got {}".format(payload["scope"]))
        return 1
    return 0


def _test_verify_access_token_valid(_my_predbat):
    """A token issued by this server verifies against its own audience and the default one."""
    print("Test: verify_access_token accepts a token it issued")
    server = make_mcp_server()

    token = server.generate_access_token("client-a", resource="http://predbat.local:8199")
    payload = server.verify_access_token(token, expected_audience="http://predbat.local:8199")
    if not payload or payload.get("client_id") != "client-a":
        print("ERROR: a freshly issued token was rejected")
        return 1

    # Tokens minted against the default localhost audience stay valid for compatibility
    default_token = server.generate_access_token("client-b")
    payload = server.verify_access_token(default_token, expected_audience="http://predbat.local:8199")
    if not payload:
        print("ERROR: the default audience should still be accepted")
        return 1
    return 0


def _test_verify_access_token_rejects_foreign_signature(_my_predbat):
    """A token signed with another server's key is rejected."""
    print("Test: verify_access_token rejects a token signed with a different key")
    server = make_mcp_server()
    attacker = make_mcp_server(secret="attacker-secret")

    forged = attacker.generate_access_token("client-a", resource="http://predbat.local:8199")
    if server.verify_access_token(forged, expected_audience="http://predbat.local:8199") is not None:
        print("ERROR: a token signed with a foreign key was accepted")
        return 1

    # A token with a tampered signature must also fail
    header, payload, _signature = server.generate_access_token("client-a").split(".")
    tampered = "{}.{}.{}".format(header, payload, "AAAAAAAAAAAAAAAAAAAAAAAA")
    if server.verify_access_token(tampered) is not None:
        print("ERROR: a token with a broken signature was accepted")
        return 1
    return 0


def _test_verify_access_token_rejects_wrong_audience(_my_predbat):
    """A token minted for another resource is rejected."""
    print("Test: verify_access_token rejects a token for a different audience")
    server = make_mcp_server()

    token = server.generate_access_token("client-a", resource="http://somewhere-else.example")
    if server.verify_access_token(token, expected_audience="http://predbat.local:8199") is not None:
        print("ERROR: a token for another audience was accepted")
        return 1
    return 0


def _test_verify_access_token_rejects_expired(_my_predbat):
    """An expired token is rejected and reported."""
    print("Test: verify_access_token rejects an expired token")
    server = make_mcp_server()
    server.access_token_lifetime = timedelta(seconds=-60)

    token = server.generate_access_token("client-a", resource="http://predbat.local:8199")
    if server.verify_access_token(token, expected_audience="http://predbat.local:8199") is not None:
        print("ERROR: an expired token was accepted")
        return 1
    return 0


def _test_verify_access_token_rejects_wrong_type(_my_predbat):
    """A correctly signed token that is not an access token is rejected.

    Guards against a refresh or authorization token being replayed at the resource endpoint.
    """
    print("Test: verify_access_token rejects a non-access token type")
    server = make_mcp_server()

    payload = {
        "sub": "client-a",
        "client_id": "client-a",
        "aud": "http://predbat.local:8199",
        "scope": "mcp:read",
        "type": "refresh",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
    }
    token = pyjwt.encode(payload, server.jwt_secret, algorithm="HS256")

    if server.verify_access_token(token, expected_audience="http://predbat.local:8199") is not None:
        print("ERROR: a refresh token was accepted as an access token")
        return 1
    return 0


def _test_verify_access_token_rejects_garbage(_my_predbat):
    """A non-JWT string is rejected without raising."""
    print("Test: verify_access_token rejects a malformed token")
    server = make_mcp_server()
    for token in ("", "not-a-jwt", "a.b.c"):
        if server.verify_access_token(token) is not None:
            print("ERROR: malformed token {!r} was accepted".format(token))
            return 1
    return 0


def _test_verify_oauth_client(_my_predbat):
    """Client verification compares against the configured shared secret."""
    print("Test: verify_oauth_client checks the shared secret")
    server = make_mcp_server()

    if not server.verify_oauth_client("any-client", MCP_SECRET):
        print("ERROR: the correct secret was rejected")
        return 1
    for bad in ("wrong", "", None, MCP_SECRET + " "):
        if server.verify_oauth_client("any-client", bad):
            print("ERROR: secret {!r} was accepted".format(bad))
            return 1
    return 0


def _test_canonical_server_uri(_my_predbat):
    """The canonical URI follows the request host, falling back to the configured port."""
    print("Test: get_canonical_server_uri resolves the server identity")
    server = make_mcp_server()

    if server.get_canonical_server_uri("predbat.local:8199") != "http://predbat.local:8199":
        print("ERROR: expected the request host to be used")
        return 1
    if server.get_canonical_server_uri() != "http://localhost:{}".format(MCP_PORT):
        print("ERROR: expected the configured port in the fallback URI")
        return 1
    return 0


def _test_authorize_get_requires_parameters(_my_predbat):
    """A GET authorize request missing OAuth parameters redirects with an error."""
    print("Test: the authorize endpoint rejects a request with missing parameters")
    server = make_mcp_server()

    request = MockRequest(method="GET", query={"redirect_uri": "https://client/callback", "state": "xyz"})
    response = run_async(server.oauth_authorize(request))

    if response.status != 302:
        print("ERROR: expected a redirect, got {}".format(response.status))
        return 1
    if "error=invalid_request" not in response.location:
        print("ERROR: expected an invalid_request error, got {}".format(response.location))
        return 1
    return 0


def _test_authorize_get_requires_pkce(_my_predbat):
    """PKCE is mandatory: a request without a challenge is refused."""
    print("Test: the authorize endpoint requires PKCE")
    server = make_mcp_server()

    request = MockRequest(method="GET", query={"client_id": "client-a", "redirect_uri": "https://client/callback", "response_type": "code", "state": "xyz"})
    response = run_async(server.oauth_authorize(request))

    if response.status != 302 or "error=invalid_request" not in response.location:
        print("ERROR: expected a PKCE rejection redirect, got {}".format(getattr(response, "location", response.status)))
        return 1
    if "PKCE" not in response.location:
        print("ERROR: expected the error description to mention PKCE, got {}".format(response.location))
        return 1
    return 0


def _test_authorize_get_requires_s256(_my_predbat):
    """The deprecated plain PKCE method is refused; only S256 is accepted."""
    print("Test: the authorize endpoint only accepts the S256 PKCE method")
    server = make_mcp_server()

    request = MockRequest(
        method="GET",
        query={"client_id": "client-a", "redirect_uri": "https://client/callback", "response_type": "code", "code_challenge": "abc", "code_challenge_method": "plain", "state": "xyz"},
    )
    response = run_async(server.oauth_authorize(request))

    if response.status != 302 or "S256" not in response.location:
        print("ERROR: expected the plain method to be refused, got {}".format(getattr(response, "location", response.status)))
        return 1
    return 0


def _test_authorize_get_shows_login(_my_predbat):
    """A well-formed GET authorize request renders the sign-in page."""
    print("Test: the authorize endpoint shows the sign-in page")
    server = make_mcp_server()
    _verifier, challenge = pkce_pair()

    request = MockRequest(
        method="GET",
        query={"client_id": "client-a", "redirect_uri": "https://client/callback", "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz"},
    )
    response = run_async(server.oauth_authorize(request))

    if response.status != 200:
        print("ERROR: expected the login page, got status {}".format(response.status))
        return 1
    body = response.text
    if "Predbat MCP" not in body or 'name="secret"' not in body:
        print("ERROR: the login form was not rendered")
        return 1
    # The PKCE parameters must survive the round trip through the form
    if challenge not in body:
        print("ERROR: the code challenge was not carried into the form")
        return 1
    # No code should be issued merely by loading the page
    if server.authorization_codes:
        print("ERROR: an authorization code was issued before authentication")
        return 1
    return 0


def _test_authorize_post_wrong_secret(_my_predbat):
    """Submitting the wrong secret re-renders the form and issues no code."""
    print("Test: the authorize endpoint refuses a bad secret")
    server = make_mcp_server()
    _verifier, challenge = pkce_pair()

    request = MockRequest(
        method="POST",
        post_data={"client_id": "client-a", "redirect_uri": "https://client/callback", "response_type": "code", "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256", "secret": "wrong-secret"},
    )
    response = run_async(server.oauth_authorize(request))

    if response.status != 200:
        print("ERROR: expected the login page to be re-rendered, got {}".format(response.status))
        return 1
    if "Invalid secret" not in response.text:
        print("ERROR: expected an invalid secret message")
        return 1
    if server.authorization_codes:
        print("ERROR: an authorization code was issued despite a bad secret")
        return 1
    return 0


def _test_authorize_post_issues_code(_my_predbat):
    """The correct secret issues a single-use code and redirects back with the state."""
    print("Test: the authorize endpoint issues a code for the correct secret")
    server = make_mcp_server()
    _verifier, challenge = pkce_pair()

    request = MockRequest(
        method="POST",
        post_data={"client_id": "client-a", "redirect_uri": "https://client/callback", "response_type": "code", "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256", "secret": MCP_SECRET},
    )
    response = run_async(server.oauth_authorize(request))

    if response.status != 302:
        print("ERROR: expected a redirect back to the client, got {}".format(response.status))
        return 1
    if not response.location.startswith("https://client/callback?code="):
        print("ERROR: unexpected redirect target {}".format(response.location))
        return 1
    if "state=xyz" not in response.location:
        print("ERROR: the state parameter was not returned")
        return 1

    if len(server.authorization_codes) != 1:
        print("ERROR: expected exactly one stored code, got {}".format(len(server.authorization_codes)))
        return 1
    stored = list(server.authorization_codes.values())[0]
    if stored["code_challenge"] != challenge:
        print("ERROR: the PKCE challenge was not stored with the code")
        return 1
    if stored["used"]:
        print("ERROR: a freshly issued code should not be marked used")
        return 1
    if stored["expires"] <= datetime.now(timezone.utc):
        print("ERROR: the issued code is already expired")
        return 1
    return 0


def _test_authorize_post_missing_parameters(_my_predbat):
    """A POST without the required OAuth parameters never reaches the secret check."""
    print("Test: the authorize endpoint rejects a POST with missing parameters")
    server = make_mcp_server()

    request = MockRequest(method="POST", post_data={"secret": MCP_SECRET})
    response = run_async(server.oauth_authorize(request))

    if response.status != 200:
        print("ERROR: expected the login page, got {}".format(response.status))
        return 1
    if "Missing required OAuth parameters" not in response.text:
        print("ERROR: expected the missing parameter message")
        return 1
    if server.authorization_codes:
        print("ERROR: no code should be issued for an incomplete request")
        return 1
    return 0


def _test_token_unsupported_grant(_my_predbat):
    """An unknown grant type is refused with unsupported_grant_type."""
    print("Test: the token endpoint refuses an unknown grant type")
    server = make_mcp_server()

    request = MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data={"grant_type": "password"})
    response = run_async(server.oauth_token(request))

    if response.status != 400:
        print("ERROR: expected a 400, got {}".format(response.status))
        return 1
    if response_json(response)["error"] != "unsupported_grant_type":
        print("ERROR: expected unsupported_grant_type, got {}".format(response_json(response)))
        return 1
    return 0


def _test_token_client_credentials_success(_my_predbat):
    """A valid client_credentials exchange returns a usable bearer token."""
    print("Test: the token endpoint issues a token for valid client credentials")
    server = make_mcp_server()

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "client_credentials", "client_id": "client-a", "client_secret": MCP_SECRET, "resource": "http://predbat.local:8199", "scope": "mcp:read"},
    )
    response = run_async(server.oauth_token(request))

    if response.status != 200:
        print("ERROR: expected a 200, got {}".format(response.status))
        return 1
    body = response_json(response)
    if body["token_type"] != "Bearer":
        print("ERROR: expected a Bearer token")
        return 1
    if body["scope"] != "mcp:read":
        print("ERROR: expected the requested scope back, got {}".format(body["scope"]))
        return 1
    # The issued token must actually verify against this server
    payload = server.verify_access_token(body["access_token"], expected_audience="http://predbat.local:8199")
    if not payload or payload["client_id"] != "client-a":
        print("ERROR: the issued token does not verify")
        return 1
    return 0


def _test_token_client_credentials_bad_secret(_my_predbat):
    """A wrong client secret is refused with 401 and issues no token."""
    print("Test: the token endpoint refuses invalid client credentials")
    server = make_mcp_server()

    request = MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data={"grant_type": "client_credentials", "client_id": "client-a", "client_secret": "wrong"})
    response = run_async(server.oauth_token(request))

    if response.status != 401:
        print("ERROR: expected a 401, got {}".format(response.status))
        return 1
    body = response_json(response)
    if body["error"] != "invalid_client":
        print("ERROR: expected invalid_client, got {}".format(body))
        return 1
    if "access_token" in body:
        print("ERROR: a token was issued despite invalid credentials")
        return 1
    return 0


def _test_token_client_credentials_missing(_my_predbat):
    """Missing credentials are refused with 400 rather than treated as empty."""
    print("Test: the token endpoint requires both client credentials")
    server = make_mcp_server()

    for payload in ({"grant_type": "client_credentials", "client_id": "client-a"}, {"grant_type": "client_credentials", "client_secret": MCP_SECRET}):
        request = MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data=payload)
        response = run_async(server.oauth_token(request))
        if response.status != 400:
            print("ERROR: expected a 400 for {}, got {}".format(payload, response.status))
            return 1
        if response_json(response)["error"] != "invalid_request":
            print("ERROR: expected invalid_request for {}".format(payload))
            return 1
    return 0


def _test_token_form_encoded_body(_my_predbat):
    """The token endpoint accepts a form-encoded body as well as JSON."""
    print("Test: the token endpoint accepts a form-encoded body")
    server = make_mcp_server()

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        post_data={"grant_type": "client_credentials", "client_id": "client-a", "client_secret": MCP_SECRET},
    )
    response = run_async(server.oauth_token(request))

    if response.status != 200:
        print("ERROR: expected a 200 for a form-encoded request, got {}".format(response.status))
        return 1
    return 0


def _test_token_authorization_code_success(_my_predbat):
    """A valid code plus matching PKCE verifier exchanges for a token and burns the code."""
    print("Test: the token endpoint exchanges an authorization code with PKCE")
    server = make_mcp_server()
    verifier, challenge = pkce_pair()
    code = store_auth_code(server, challenge=challenge)

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://client/callback", "code_verifier": verifier, "resource": "http://predbat.local:8199"},
    )
    response = run_async(server.oauth_token(request))

    if response.status != 200:
        print("ERROR: expected a 200, got {} body {}".format(response.status, response_json(response)))
        return 1
    body = response_json(response)
    payload = server.verify_access_token(body["access_token"], expected_audience="http://predbat.local:8199")
    if not payload or payload["client_id"] != "client-a":
        print("ERROR: the issued token does not verify")
        return 1
    # The code must be burnt so a stolen code cannot be replayed
    if not server.authorization_codes[code]["used"]:
        print("ERROR: the authorization code was not marked as used")
        return 1
    return 0


def _test_token_authorization_code_replay(_my_predbat):
    """Replaying a used code is refused and the code is discarded."""
    print("Test: an authorization code cannot be replayed")
    server = make_mcp_server()
    verifier, challenge = pkce_pair()
    code = store_auth_code(server, challenge=challenge, used=True)

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://client/callback", "code_verifier": verifier},
    )
    response = run_async(server.oauth_token(request))

    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: expected an invalid_grant refusal, got {} {}".format(response.status, response_json(response)))
        return 1
    if code in server.authorization_codes:
        print("ERROR: the replayed code should have been discarded")
        return 1
    return 0


def _test_token_authorization_code_expired(_my_predbat):
    """An expired code is refused and discarded."""
    print("Test: an expired authorization code is refused")
    server = make_mcp_server()
    verifier, challenge = pkce_pair()
    code = store_auth_code(server, challenge=challenge, expires_in_minutes=-1)

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://client/callback", "code_verifier": verifier},
    )
    response = run_async(server.oauth_token(request))

    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: expected an invalid_grant refusal, got {}".format(response.status))
        return 1
    if code in server.authorization_codes:
        print("ERROR: the expired code should have been discarded")
        return 1
    return 0


def _test_token_authorization_code_binding(_my_predbat):
    """A code is bound to the client and redirect URI it was issued for."""
    print("Test: an authorization code is bound to its client and redirect URI")
    server = make_mcp_server()
    verifier, challenge = pkce_pair()

    # Wrong client_id
    code = store_auth_code(server, code="code-client", challenge=challenge)
    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "other-client", "redirect_uri": "https://client/callback", "code_verifier": verifier},
    )
    response = run_async(server.oauth_token(request))
    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: a code redeemed by the wrong client was accepted")
        return 1

    # Wrong redirect_uri
    code = store_auth_code(server, code="code-redirect", challenge=challenge)
    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://attacker/callback", "code_verifier": verifier},
    )
    response = run_async(server.oauth_token(request))
    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: a code redeemed against the wrong redirect URI was accepted")
        return 1
    return 0


def _test_token_authorization_code_pkce_enforced(_my_predbat):
    """PKCE is enforced: a missing or mismatched verifier is refused."""
    print("Test: PKCE verification is enforced on the code exchange")
    server = make_mcp_server()
    _verifier, challenge = pkce_pair()

    # Missing verifier
    code = store_auth_code(server, code="code-no-verifier", challenge=challenge)
    request = MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://client/callback"})
    response = run_async(server.oauth_token(request))
    if response.status != 400 or response_json(response)["error"] != "invalid_request":
        print("ERROR: a missing code_verifier was accepted")
        return 1

    # Wrong verifier
    code = store_auth_code(server, code="code-bad-verifier", challenge=challenge)
    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://client/callback", "code_verifier": "the-wrong-verifier"},
    )
    response = run_async(server.oauth_token(request))
    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: a mismatched code_verifier was accepted")
        return 1

    # A code stored without a challenge at all is refused rather than skipping PKCE
    code = store_auth_code(server, code="code-no-challenge", challenge=None)
    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": code, "client_id": "client-a", "redirect_uri": "https://client/callback", "code_verifier": "anything"},
    )
    response = run_async(server.oauth_token(request))
    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: a code without a PKCE challenge was accepted")
        return 1
    return 0


def _test_token_authorization_code_unknown(_my_predbat):
    """An unknown code is refused."""
    print("Test: an unknown authorization code is refused")
    server = make_mcp_server()

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={"grant_type": "authorization_code", "code": "never-issued", "client_id": "client-a", "redirect_uri": "https://client/callback", "code_verifier": "x"},
    )
    response = run_async(server.oauth_token(request))

    if response.status != 400 or response_json(response)["error"] != "invalid_grant":
        print("ERROR: an unknown code was accepted")
        return 1
    return 0


def _test_token_authorization_code_missing_params(_my_predbat):
    """The code exchange requires the code, client and redirect URI."""
    print("Test: the code exchange requires its mandatory parameters")
    server = make_mcp_server()

    request = MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data={"grant_type": "authorization_code", "client_id": "client-a"})
    response = run_async(server.oauth_token(request))

    if response.status != 400 or response_json(response)["error"] != "invalid_request":
        print("ERROR: expected an invalid_request refusal, got {}".format(response.status))
        return 1
    return 0


def _test_register_requires_json(_my_predbat):
    """Dynamic client registration insists on a JSON body."""
    print("Test: client registration requires a JSON content type")
    server = make_mcp_server()

    request = MockRequest(method="POST", headers={"Content-Type": "text/plain"})
    response = run_async(server.oauth_register(request))

    if response.status != 400 or response_json(response)["error"] != "invalid_request":
        print("ERROR: expected a 400 invalid_request, got {}".format(response.status))
        return 1
    return 0


def _test_register_returns_credentials(_my_predbat):
    """Registration returns a unique client id against the shared secret."""
    print("Test: client registration returns credentials")
    server = make_mcp_server()

    request = MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data={"client_name": "Test Client", "redirect_uris": ["https://client/callback"]})
    response = run_async(server.oauth_register(request))

    if response.status != 201:
        print("ERROR: expected a 201, got {}".format(response.status))
        return 1
    body = response_json(response)
    if not body["client_id"].startswith("mcp-client-"):
        print("ERROR: unexpected client_id {}".format(body["client_id"]))
        return 1
    if body["client_secret"] != MCP_SECRET:
        print("ERROR: registration should hand back the shared secret")
        return 1
    if body["client_name"] != "Test Client":
        print("ERROR: the client name was not echoed back")
        return 1

    # Each registration must mint a distinct client id
    second = run_async(server.oauth_register(MockRequest(method="POST", headers={"Content-Type": "application/json"}, json_data={})))
    if response_json(second)["client_id"] == body["client_id"]:
        print("ERROR: two registrations produced the same client id")
        return 1
    return 0


def _test_metadata_endpoints(_my_predbat):
    """The discovery endpoints advertise this host with CORS headers."""
    print("Test: the OAuth discovery endpoints advertise the server")
    server = make_mcp_server()
    request = MockRequest(method="GET", host="predbat.local:8199")

    for handler, required_keys in (
        (server.oauth_metadata, ("issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint")),
        (server.oauth_metadata_mcp, ("issuer", "authorization_endpoint", "token_endpoint")),
        (server.oauth_protected_resource_metadata, ("resource", "authorization_servers", "scopes_supported")),
    ):
        response = run_async(handler(request))
        if response.status != 200:
            print("ERROR: {} returned {}".format(handler.__name__, response.status))
            return 1
        body = response_json(response)
        for key in required_keys:
            if key not in body:
                print("ERROR: {} is missing {}".format(handler.__name__, key))
                return 1
        if response.headers.get("Access-Control-Allow-Origin") != "*":
            print("ERROR: {} is missing CORS headers".format(handler.__name__))
            return 1

    # Only S256 may be advertised - plain is deprecated in OAuth 2.1
    body = response_json(run_async(server.oauth_metadata(request)))
    if body["code_challenge_methods_supported"] != ["S256"]:
        print("ERROR: expected only S256 to be advertised, got {}".format(body["code_challenge_methods_supported"]))
        return 1
    if body["issuer"] != "http://predbat.local:8199":
        print("ERROR: expected the request host as the issuer, got {}".format(body["issuer"]))
        return 1
    return 0


def _test_options_and_static_routes(_my_predbat):
    """CORS preflight, the favicon and the catch-all route behave as expected."""
    print("Test: preflight, favicon and unmatched routes")
    server = make_mcp_server()

    response = run_async(server.handle_options(MockRequest(method="OPTIONS")))
    if response.status != 200 or response.headers.get("Access-Control-Allow-Origin") != "*":
        print("ERROR: the CORS preflight response is wrong")
        return 1
    if "Authorization" not in response.headers.get("Access-Control-Allow-Headers", ""):
        print("ERROR: the preflight must allow the Authorization header")
        return 1

    response = run_async(server.favicon(MockRequest(method="GET")))
    if response.status != 200 or response.content_type != "image/svg+xml":
        print("ERROR: unexpected favicon response")
        return 1

    response = run_async(server.default_route(MockRequest(method="GET", path="/nope")))
    if response.status != 404:
        print("ERROR: expected a 404 from the catch-all route, got {}".format(response.status))
        return 1
    return 0


def _install_mcp_server_stub(server):
    """Attach a stub MCP server that records the requests it handled."""
    handled = []

    class StubMCPServer:
        """Records handled requests in place of the real MCP protocol handler."""

        async def handle_mcp_request(self, request, mcp_server):
            """Record the request and return a fixed JSON-RPC result."""
            handled.append(request)
            return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    server.mcp_server = StubMCPServer()
    return handled


def _test_mcp_endpoints_require_bearer(_my_predbat):
    """Both MCP endpoints refuse a request with no bearer token and advertise how to get one."""
    print("Test: the MCP endpoints require a bearer token")
    server = make_mcp_server()
    handled = _install_mcp_server_stub(server)

    for handler in (server.html_mcp_get, server.html_mcp_post):
        for headers in ({}, {"Authorization": "Basic abc"}):
            response = run_async(handler(MockRequest(method="POST", headers=headers)))
            if response.status != 401:
                print("ERROR: {} returned {} for headers {}".format(handler.__name__, response.status, headers))
                return 1
            www_auth = response.headers.get("WWW-Authenticate", "")
            if "resource_metadata" not in www_auth:
                print("ERROR: {} must advertise resource_metadata, got {!r}".format(handler.__name__, www_auth))
                return 1
    if handled:
        print("ERROR: an unauthenticated request reached the MCP handler")
        return 1
    return 0


def _test_mcp_endpoints_reject_bad_token(_my_predbat):
    """A bearer token that is neither a valid JWT nor the shared secret is refused."""
    print("Test: the MCP endpoints refuse an invalid bearer token")
    server = make_mcp_server()
    handled = _install_mcp_server_stub(server)
    attacker = make_mcp_server(secret="attacker-secret")
    forged = attacker.generate_access_token("client-a", resource="http://predbat.local:8199")

    for handler in (server.html_mcp_get, server.html_mcp_post):
        for token in ("garbage", forged, MCP_SECRET + "x"):
            response = run_async(handler(MockRequest(method="POST", headers={"Authorization": "Bearer " + token})))
            if response.status != 401:
                print("ERROR: {} accepted token {!r} with status {}".format(handler.__name__, token[:20], response.status))
                return 1
            if "invalid_token" not in response.headers.get("WWW-Authenticate", ""):
                print("ERROR: {} did not report an invalid token".format(handler.__name__))
                return 1
    if handled:
        print("ERROR: an invalid token reached the MCP handler")
        return 1
    return 0


def _test_mcp_endpoints_accept_oauth_token(_my_predbat):
    """A valid OAuth token reaches the MCP handler."""
    print("Test: the MCP endpoints accept a valid OAuth token")
    server = make_mcp_server()
    handled = _install_mcp_server_stub(server)
    token = server.generate_access_token("client-a", resource="http://predbat.local:8199")

    response = run_async(server.html_mcp_post(MockRequest(method="POST", headers={"Authorization": "Bearer " + token})))

    if response.status != 200:
        print("ERROR: expected a 200, got {}".format(response.status))
        return 1
    if len(handled) != 1:
        print("ERROR: expected the request to reach the MCP handler")
        return 1
    if response_json(response)["result"] != {"ok": True}:
        print("ERROR: the handler result was not returned")
        return 1
    return 0


def _test_mcp_endpoints_accept_legacy_secret(_my_predbat):
    """The shared secret is still accepted directly as a bearer token."""
    print("Test: the MCP endpoints accept the legacy shared secret")
    server = make_mcp_server()
    handled = _install_mcp_server_stub(server)

    response = run_async(server.html_mcp_get(MockRequest(method="POST", headers={"Authorization": "Bearer " + MCP_SECRET})))

    if response.status != 200:
        print("ERROR: expected a 200, got {}".format(response.status))
        return 1
    if len(handled) != 1:
        print("ERROR: the legacy secret did not reach the MCP handler")
        return 1
    return 0


def _test_mcp_endpoints_unavailable(_my_predbat):
    """With no MCP server running the endpoints report 503 before checking credentials."""
    print("Test: the MCP endpoints report an unavailable server")
    server = make_mcp_server()
    server.mcp_server = None

    for handler in (server.html_mcp_get, server.html_mcp_post):
        response = run_async(handler(MockRequest(method="POST", headers={"Authorization": "Bearer " + MCP_SECRET})))
        if response.status != 503:
            print("ERROR: {} returned {} instead of 503".format(handler.__name__, response.status))
            return 1
    return 0


def _test_mcp_endpoints_handler_error(_my_predbat):
    """A failure inside the MCP handler becomes a JSON-RPC internal error, not a stack trace."""
    print("Test: the MCP endpoints wrap handler failures")
    server = make_mcp_server()

    class FailingMCPServer:
        """Raises from the request handler to exercise the error path."""

        async def handle_mcp_request(self, request, mcp_server):
            """Fail as a broken tool implementation would."""
            raise RuntimeError("tool exploded")

    server.mcp_server = FailingMCPServer()

    response = run_async(server.html_mcp_post(MockRequest(method="POST", headers={"Authorization": "Bearer " + MCP_SECRET})))

    if response.status != 500:
        print("ERROR: expected a 500, got {}".format(response.status))
        return 1
    body = response_json(response)
    if body["error"]["code"] != -32603:
        print("ERROR: expected the JSON-RPC internal error code, got {}".format(body))
        return 1
    return 0


def _test_endpoint_error_handling(_my_predbat):
    """A body that cannot be read becomes a 500 error response, not an unhandled exception.

    These endpoints are reachable before authentication, so an unhandled exception here would be
    an availability problem rather than just an untidy response.
    """
    print("Test: the OAuth endpoints handle a broken request body")
    server = make_mcp_server()

    response = run_async(server.oauth_token(MockRequest(method="POST", headers={"Content-Type": "application/json"}, raise_on_body=True)))
    if response.status != 500 or response_json(response)["error"] != "server_error":
        print("ERROR: the token endpoint did not report a server error, got {}".format(response.status))
        return 1

    response = run_async(server.oauth_register(MockRequest(method="POST", headers={"Content-Type": "application/json"}, raise_on_body=True)))
    if response.status != 500 or response_json(response)["error"] != "server_error":
        print("ERROR: the register endpoint did not report a server error, got {}".format(response.status))
        return 1

    response = run_async(server.oauth_authorize(MockRequest(method="POST", raise_on_body=True)))
    if response.status != 500:
        print("ERROR: the authorize endpoint did not report a server error, got {}".format(response.status))
        return 1
    if server.authorization_codes:
        print("ERROR: a failed authorize request issued a code")
        return 1
    return 0


def _test_token_authorization_code_scope(_my_predbat):
    """A scope requested at the code exchange is carried into the issued token."""
    print("Test: the code exchange honours a requested scope")
    server = make_mcp_server()
    verifier, challenge = pkce_pair()
    code = store_auth_code(server, code="code-scoped", challenge=challenge)

    request = MockRequest(
        method="POST",
        headers={"Content-Type": "application/json"},
        json_data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "client-a",
            "redirect_uri": "https://client/callback",
            "code_verifier": verifier,
            "resource": "http://predbat.local:8199",
            "scope": "mcp:read mcp:write",
        },
    )
    response = run_async(server.oauth_token(request))

    body = response_json(response)
    if body["scope"] != "mcp:read mcp:write":
        print("ERROR: expected the requested scope back, got {}".format(body["scope"]))
        return 1
    payload = server.verify_access_token(body["access_token"], expected_audience="http://predbat.local:8199")
    if payload["scope"] != "mcp:read mcp:write":
        print("ERROR: the token does not carry the requested scope, got {}".format(payload["scope"]))
        return 1
    return 0


def _test_mcp_get_oauth_and_errors(_my_predbat):
    """The GET endpoint accepts an OAuth token and wraps handler failures."""
    print("Test: the MCP GET endpoint with an OAuth token and a failing handler")
    server = make_mcp_server()
    handled = _install_mcp_server_stub(server)
    token = server.generate_access_token("client-a", resource="http://predbat.local:8199")

    response = run_async(server.html_mcp_get(MockRequest(method="POST", headers={"Authorization": "Bearer " + token})))
    if response.status != 200 or len(handled) != 1:
        print("ERROR: a valid OAuth token did not reach the GET handler")
        return 1

    class FailingMCPServer:
        """Raises from the request handler to exercise the GET error path."""

        async def handle_mcp_request(self, request, mcp_server):
            """Fail as a broken tool implementation would."""
            raise RuntimeError("tool exploded")

    server.mcp_server = FailingMCPServer()
    response = run_async(server.html_mcp_get(MockRequest(method="POST", headers={"Authorization": "Bearer " + MCP_SECRET})))
    if response.status != 500 or response_json(response)["error"]["code"] != -32603:
        print("ERROR: the GET endpoint did not wrap the handler failure, got {}".format(response.status))
        return 1
    return 0


class StubRunner:
    """Stands in for aiohttp's AppRunner without binding a socket."""

    def __init__(self, app):
        """Record the application whose routes are being served."""
        self.app = app
        self.cleaned_up = False

    async def setup(self):
        """Pretend the runner is ready."""

    async def cleanup(self):
        """Record that the runner was torn down."""
        self.cleaned_up = True


class StubSite:
    """Stands in for aiohttp's TCPSite without listening on a port."""

    def __init__(self, runner, host, port):
        """Record the bind parameters."""
        self.runner = runner
        self.host = host
        self.port = port

    async def start(self):
        """Pretend the site is listening."""


def run_server_start(server):
    """Run the server's start() against stubbed aiohttp plumbing, returning the runners created."""
    import web_mcp

    runners = []

    def make_runner(app):
        """Build a stub runner and remember it for assertions."""
        runner = StubRunner(app)
        runners.append(runner)
        return runner

    async def instant_sleep(_seconds):
        """Skip the keep-alive delay so the test does not wait on real time."""

    real_app_runner = web_mcp.web.AppRunner
    real_tcp_site = web_mcp.web.TCPSite
    real_sleep = web_mcp.asyncio.sleep
    web_mcp.web.AppRunner = make_runner
    web_mcp.web.TCPSite = StubSite
    web_mcp.asyncio.sleep = instant_sleep
    try:
        run_async(server.start())
    finally:
        web_mcp.web.AppRunner = real_app_runner
        web_mcp.web.TCPSite = real_tcp_site
        web_mcp.asyncio.sleep = real_sleep
    return runners


EXPECTED_MCP_ROUTES = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/oauth/authorize",
    "/oauth/token",
    "/oauth/register",
    "/mcp",
    "/",
    "/favicon.ico",
)


def _test_server_start_registers_routes(_my_predbat):
    """Starting the server wires every documented endpoint onto the aiohttp application.

    The routing table is the only place the OAuth endpoints are bound to their handlers, so a
    typo there would silently disable authentication discovery without any test noticing.
    """
    print("Test: starting the MCP server registers its routes")
    server = make_mcp_server()

    heartbeats = []

    def record_heartbeat():
        """Record a liveness heartbeat and ask the keep-alive loop to stop."""
        heartbeats.append(True)
        server.api_stop = True

    server.update_success_timestamp = record_heartbeat
    runners = run_server_start(server)

    if not runners:
        print("ERROR: the server never created a runner")
        return 1
    if not heartbeats:
        print("ERROR: the keep-alive loop never reported liveness")
        return 1
    if not runners[0].cleaned_up:
        print("ERROR: the runner was not cleaned up on shutdown")
        return 1
    if server.api_started:
        print("ERROR: api_started should be cleared once the server stops")
        return 1

    paths = {resource.canonical for resource in runners[0].app.router.resources()}
    missing = [route for route in EXPECTED_MCP_ROUTES if route not in paths]
    if missing:
        print("ERROR: routes not registered: {}".format(missing))
        return 1

    # The MCP server itself must have been created and then stopped again
    if server.mcp_server is None or server.mcp_server.is_running:
        print("ERROR: the MCP protocol server was not started and stopped cleanly")
        return 1
    return 0


def _test_server_start_disabled(_my_predbat):
    """A disabled MCP server does nothing when started."""
    print("Test: a disabled MCP server does not start")
    server = make_mcp_server()
    server.mcp_enable = False

    run_async(server.start())

    if server.mcp_server is not None:
        print("ERROR: a disabled server should not create an MCP protocol server")
        return 1
    if server.api_started:
        print("ERROR: a disabled server should not report itself as started")
        return 1
    return 0


def make_wrapper():
    """Build an MCPServerWrapper over a mock base."""
    base = MockMCPBase()
    wrapper = MCPServerWrapper(base, log_func=base.log)
    return wrapper, base


def _test_wrapper_lifecycle(_my_predbat):
    """The wrapper tracks its running state across start and stop."""
    print("Test: the MCP server wrapper start/stop lifecycle")
    wrapper, _base = make_wrapper()

    if wrapper.is_running:
        print("ERROR: the wrapper should not start out running")
        return 1
    run_async(wrapper.start())
    if not wrapper.is_running:
        print("ERROR: start() did not mark the wrapper as running")
        return 1
    run_async(wrapper.stop())
    if wrapper.is_running:
        print("ERROR: stop() did not clear the running flag")
        return 1
    return 0


def _test_wrapper_rejects_non_post(_my_predbat):
    """Only POST carries JSON-RPC; a GET is answered with an invalid request error."""
    print("Test: the wrapper refuses non-POST JSON-RPC requests")
    wrapper, _base = make_wrapper()

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="GET"), wrapper))

    if result["error"]["code"] != -32600:
        print("ERROR: expected an invalid request error, got {}".format(result))
        return 1
    return 0


def _test_wrapper_parse_error(_my_predbat):
    """A body that is not JSON produces a JSON-RPC parse error."""
    print("Test: the wrapper reports a parse error for a non-JSON body")
    wrapper, _base = make_wrapper()

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="POST"), wrapper))

    if result["error"]["code"] != -32700:
        print("ERROR: expected a parse error, got {}".format(result))
        return 1
    return 0


def _test_wrapper_method_routing(_my_predbat):
    """initialize, tools/list and notifications route correctly; unknown methods 404."""
    print("Test: the wrapper routes JSON-RPC methods")
    wrapper, _base = make_wrapper()

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 1, "method": "initialize"}), wrapper))
    if result["result"]["serverInfo"]["name"] != "Predbat MCP Server":
        print("ERROR: initialize did not return the server info, got {}".format(result))
        return 1
    if result["id"] != 1:
        print("ERROR: the request id was not echoed back")
        return 1

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}), wrapper))
    tool_names = [tool["name"] for tool in result["result"]["tools"]]
    for expected in ("get_plan", "get_status", "get_config", "get_entities", "set_config", "set_plan_override"):
        if expected not in tool_names:
            print("ERROR: tools/list is missing {}".format(expected))
            return 1

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 3, "method": "notifications/initialized"}), wrapper))
    if result.get("result") != {}:
        print("ERROR: a notification should return an empty result, got {}".format(result))
        return 1

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 4, "method": "no/such/method"}), wrapper))
    if result["error"]["code"] != -32601:
        print("ERROR: expected a method not found error, got {}".format(result))
        return 1
    return 0


def _test_wrapper_get_plan(_my_predbat):
    """get_plan returns the stored raw plan, and reports cleanly when there is none."""
    print("Test: the get_plan tool")
    wrapper, base = make_wrapper()

    result = run_async(wrapper._execute_get_plan({}))
    if result["success"] or result["data"] is not None:
        print("ERROR: expected a failure when no plan is available, got {}".format(result))
        return 1

    base.states["predbat.plan_html"] = {"state": "on", "attributes": {"raw": [{"minute": 0, "soc": 50}]}}
    result = run_async(wrapper._execute_get_plan({}))
    if not result["success"] or result["data"] != [{"minute": 0, "soc": 50}]:
        print("ERROR: expected the raw plan data, got {}".format(result))
        return 1
    return 0


def _test_wrapper_get_entities_filter(_my_predbat):
    """get_entities returns entity metadata and honours the regex filter."""
    print("Test: the get_entities tool and its filter")
    wrapper, base = make_wrapper()
    base.dashboard_values = [
        {"entity_id": "predbat.soc_kw", "state": "5.0", "friendly_name": "SOC", "unit_of_measurement": "kWh"},
        {"entity_id": "predbat.status", "state": "Idle", "friendly_name": "Status"},
        "predbat.plain_string",
    ]

    result = run_async(wrapper._execute_get_entities({}))
    if not result["success"] or len(result["data"]) != 3:
        print("ERROR: expected all three entities, got {}".format(result))
        return 1
    if result["data"][0].get("unit_of_measurement") != "kWh":
        print("ERROR: the unit was not carried through, got {}".format(result["data"][0]))
        return 1

    result = run_async(wrapper._execute_get_entities({"filter": "soc"}))
    if len(result["data"]) != 1 or result["data"][0]["entity_id"] != "predbat.soc_kw":
        print("ERROR: the filter did not narrow the results, got {}".format(result["data"]))
        return 1

    # An invalid regex must be reported rather than raising out of the tool
    result = run_async(wrapper._execute_get_entities({"filter": "["}))
    if result["success"]:
        print("ERROR: expected an invalid regex to be reported as a failure")
        return 1
    return 0


def _test_wrapper_get_apps(_my_predbat):
    """get_apps returns the apps.yaml arguments and honours the regex filter."""
    print("Test: the get_apps tool and its filter")
    wrapper, base = make_wrapper()
    base.args = {"battery_size": 9.5, "battery_rate_max": 3.6, "inverter_type": "GE"}

    result = run_async(wrapper._execute_get_apps({}))
    if not result["success"] or len(result["data"]) != 3:
        print("ERROR: expected all three settings, got {}".format(result))
        return 1

    result = run_async(wrapper._execute_get_apps({"filter": "^battery"}))
    if sorted(result["data"].keys()) != ["battery_rate_max", "battery_size"]:
        print("ERROR: the filter did not narrow the settings, got {}".format(result["data"]))
        return 1

    result = run_async(wrapper._execute_get_apps({"filter": "("}))
    if result["success"]:
        print("ERROR: an invalid regex should be reported as a failure")
        return 1
    return 0


def _test_wrapper_get_config(_my_predbat):
    """get_config returns the live config items and honours the entity filter."""
    print("Test: the get_config tool and its filter")
    wrapper, base = make_wrapper()
    base.CONFIG_ITEMS = [
        {"name": "mode", "entity": "select.predbat_mode", "value": "Control charge"},
        {"name": "battery_size", "entity": "input_number.predbat_battery_size", "value": 9.5},
        {"name": "no_entity_item", "value": 1},
    ]

    result = run_async(wrapper._execute_get_config({}))
    if not result["success"] or len(result["data"]) != 3:
        print("ERROR: expected all config items, got {}".format(result))
        return 1

    result = run_async(wrapper._execute_get_config({"filter": "battery"}))
    if len(result["data"]) != 1 or result["data"][0]["name"] != "battery_size":
        print("ERROR: the filter did not narrow the config items, got {}".format(result["data"]))
        return 1

    # Items with no entity are skipped by a filtered query rather than raising
    result = run_async(wrapper._execute_get_config({"filter": "no_entity_item"}))
    if result["data"]:
        print("ERROR: an item with no entity should not match an entity filter")
        return 1

    result = run_async(wrapper._execute_get_config({"filter": "("}))
    if result["success"]:
        print("ERROR: an invalid regex should be reported as a failure")
        return 1
    return 0


def _test_wrapper_get_status(_my_predbat):
    """get_status reports the live system state including a derived SoC percentage."""
    print("Test: the get_status tool")
    wrapper, base = make_wrapper()
    base.ha_config = {"mode": "Control charge", "num_cars": 1, "debug_enable": False, "set_read_only": True}
    base.states["predbat.status"] = {"state": "Idle", "attributes": {"last_updated": "2026-01-15T10:00:00+00:00"}}

    result = run_async(wrapper._execute_get_status({}))

    if not result["success"]:
        print("ERROR: get_status failed: {}".format(result))
        return 1
    data = result["data"]
    if data["status"] != "Idle":
        print("ERROR: expected the status entity state, got {}".format(data["status"]))
        return 1
    # 5.0 kWh of a 10.0 kWh battery
    if data["soc_percent"] != 50:
        print("ERROR: expected 50%, got {}".format(data["soc_percent"]))
        return 1
    if data["mode"] != "Control charge" or data["num_cars"] != 1:
        print("ERROR: the Home Assistant config was not reported, got {}".format(data))
        return 1
    if data["read_only"] is not True:
        print("ERROR: read-only mode must be reported, got {}".format(data["read_only"]))
        return 1
    for key in ("grid_power", "battery_power", "pv_power", "load_power", "is_running", "reserve", "forecast_minutes"):
        if key not in data:
            print("ERROR: status is missing {}".format(key))
            return 1

    # A base that raises is reported as a tool failure rather than propagating
    base.states = None
    result = run_async(wrapper._execute_get_status({}))
    if result["success"]:
        print("ERROR: expected a failure to be reported")
        return 1
    return 0


def _test_wrapper_set_config(_my_predbat):
    """set_config writes through to Home Assistant and validates its arguments."""
    print("Test: the set_config tool")
    wrapper, base = make_wrapper()

    result = run_async(wrapper._execute_set_config({"entity_id": "select.predbat_mode", "value": "Monitor"}))
    if not result["success"]:
        print("ERROR: the write failed: {}".format(result))
        return 1
    if base.ha_interface.writes != [("select.predbat_mode", "Monitor")]:
        print("ERROR: the configuration write did not reach Home Assistant, got {}".format(base.ha_interface.writes))
        return 1

    # Both arguments are required, and nothing is written when one is missing
    for arguments in ({"entity_id": "select.predbat_mode"}, {"value": "Monitor"}, {}):
        result = run_async(wrapper._execute_set_config(arguments))
        if result["success"]:
            print("ERROR: expected a failure for arguments {}".format(arguments))
            return 1
    if len(base.ha_interface.writes) != 1:
        print("ERROR: an incomplete request performed a write")
        return 1

    # A backend failure is reported rather than raised
    base.ha_interface.fail = True
    result = run_async(wrapper._execute_set_config({"entity_id": "select.predbat_mode", "value": "Monitor"}))
    if result["success"] or "home assistant unavailable" not in result["error"]:
        print("ERROR: expected the backend failure to be reported, got {}".format(result))
        return 1
    return 0


def _test_wrapper_set_plan_override(_my_predbat):
    """set_plan_override maps each action onto the matching manual selection."""
    print("Test: the set_plan_override tool")

    actions = {
        "demand": "manual_demand",
        "charge": "manual_charge",
        "export": "manual_export",
        "freeze_charge": "manual_freeze_charge",
        "freeze_export": "manual_freeze_export",
    }
    for action, config_item in actions.items():
        wrapper, base = make_wrapper()
        result = run_async(wrapper._execute_set_plan_override({"action": action, "time": "12:00"}))
        if not result["success"]:
            print("ERROR: action {} failed: {}".format(action, result))
            return 1
        if len(base.manual_selects) != 1 or base.manual_selects[0][0] != config_item:
            print("ERROR: action {} selected {}".format(action, base.manual_selects))
            return 1
        if base.manual_selects[0][1] != "12:00:00":
            print("ERROR: expected a 12:00:00 slot, got {}".format(base.manual_selects[0][1]))
            return 1
        # Applying an override must invalidate the plan so it gets recomputed
        if not base.update_pending or base.plan_valid:
            print("ERROR: action {} did not request a plan refresh".format(action))
            return 1

    # An action with spaces and mixed case is normalised
    wrapper, base = make_wrapper()
    result = run_async(wrapper._execute_set_plan_override({"action": "Freeze Charge", "time": "12:00"}))
    if not result["success"] or base.manual_selects[0][0] != "manual_freeze_charge":
        print("ERROR: the action was not normalised, got {}".format(base.manual_selects))
        return 1

    # Clear selects and then deselects the slot
    wrapper, base = make_wrapper()
    result = run_async(wrapper._execute_set_plan_override({"action": "clear", "time": "12:00"}))
    if not result["success"]:
        print("ERROR: the clear action failed: {}".format(result))
        return 1
    if base.manual_selects != [("manual_demand", "12:00:00"), ("manual_demand", "[12:00:00]")]:
        print("ERROR: unexpected clear sequence {}".format(base.manual_selects))
        return 1
    return 0


def _test_wrapper_set_plan_override_rejections(_my_predbat):
    """set_plan_override refuses missing, unparseable, distant and unknown requests."""
    print("Test: the set_plan_override tool refuses invalid requests")

    cases = [
        ({}, "missing arguments"),
        ({"action": "charge"}, "missing time"),
        ({"time": "12:00"}, "missing action"),
        ({"action": "charge", "time": "not a time"}, "unparseable time"),
        ({"action": "charge", "time": "09:00"}, "more than 17 hours ahead"),
        ({"action": "explode", "time": "12:00"}, "unknown action"),
    ]

    for arguments, description in cases:
        wrapper, base = make_wrapper()
        result = run_async(wrapper._execute_set_plan_override(arguments))
        if result["success"]:
            print("ERROR: {} should have been refused, got {}".format(description, result))
            return 1
        if base.manual_selects:
            print("ERROR: {} still applied an override: {}".format(description, base.manual_selects))
            return 1
        if base.update_pending:
            print("ERROR: {} still requested a plan refresh".format(description))
            return 1
    return 0


def _test_wrapper_tools_call_dispatch(_my_predbat):
    """tools/call routes to each tool and wraps the result in MCP content."""
    print("Test: tools/call dispatches to every tool")
    wrapper, base = make_wrapper()
    base.args = {"battery_size": 9.5}
    base.CONFIG_ITEMS = [{"name": "mode", "entity": "select.predbat_mode"}]
    base.states["predbat.plan_html"] = {"state": "on", "attributes": {"raw": [{"minute": 0}]}}

    for tool_name, arguments in (
        ("get_plan", {}),
        ("get_status", {}),
        ("get_apps", {}),
        ("get_config", {}),
        ("get_entities", {}),
        ("set_config", {"entity_id": "select.predbat_mode", "value": "Monitor"}),
        ("set_plan_override", {"action": "charge", "time": "12:00"}),
    ):
        response = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}), wrapper))
        result = response["result"]
        if result.get("isError"):
            print("ERROR: tool {} reported an error: {}".format(tool_name, result))
            return 1
        payload = json.loads(result["content"][0]["text"])
        if not payload.get("success"):
            print("ERROR: tool {} was not successful: {}".format(tool_name, payload))
            return 1

    # An unknown tool is refused rather than silently ignored
    response = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "drop_tables"}}), wrapper))
    result = response["result"]
    if not result.get("isError"):
        print("ERROR: an unknown tool was accepted, got {}".format(result))
        return 1
    if "Unknown tool" not in result["content"][0]["text"]:
        print("ERROR: expected an unknown tool message, got {}".format(result))
        return 1
    return 0


def _test_wrapper_tools_call_error(_my_predbat):
    """A tool that raises is reported as an MCP error rather than crashing the request."""
    print("Test: tools/call wraps a failing tool")
    wrapper, _base = make_wrapper()

    async def exploding_tool(arguments):
        """Raise as a broken tool implementation would."""
        raise RuntimeError("tool exploded")

    wrapper._execute_get_plan = exploding_tool

    result = run_async(wrapper._handle_tools_call({"name": "get_plan", "arguments": {}}))

    if not result.get("isError"):
        print("ERROR: expected an error result, got {}".format(result))
        return 1
    if "tool exploded" not in result["content"][0]["text"]:
        print("ERROR: the underlying failure was not reported, got {}".format(result))
        return 1
    return 0


def _test_wrapper_handle_request_alias(_my_predbat):
    """handle_request forwards to the JSON-RPC handler with the wrapper as the server."""
    print("Test: the wrapper's handle_request alias")
    wrapper, _base = make_wrapper()

    result = run_async(wrapper.handle_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 7, "method": "initialize"})))

    if result["id"] != 7 or "result" not in result:
        print("ERROR: handle_request did not forward the call, got {}".format(result))
        return 1
    return 0


def _test_wrapper_internal_error(_my_predbat):
    """A failure inside the JSON-RPC dispatcher becomes an internal error response."""
    print("Test: the wrapper reports an internal dispatch error")
    wrapper, _base = make_wrapper()

    async def exploding_initialize(params):
        """Raise from a protocol handler."""
        raise RuntimeError("handler exploded")

    wrapper._handle_initialize = exploding_initialize

    result = run_async(wrapper.handle_mcp_request(MockRequest(method="POST", json_data={"jsonrpc": "2.0", "id": 9, "method": "initialize"}), wrapper))

    if result["error"]["code"] != -32603:
        print("ERROR: expected an internal error, got {}".format(result))
        return 1
    if result["id"] != 9:
        print("ERROR: the request id should be preserved in the error, got {}".format(result))
        return 1
    return 0


def _test_wrapper_get_plan_error(_my_predbat):
    """A failure reading the plan state is reported rather than raised."""
    print("Test: the get_plan tool reports a state lookup failure")
    wrapper, base = make_wrapper()

    def exploding_state(entity_id, attribute=None, default=None):
        """Raise as an unavailable state backend would."""
        raise RuntimeError("state backend down")

    base.get_state_wrapper = exploding_state

    result = run_async(wrapper._execute_get_plan({}))

    if result["success"] or "state backend down" not in result["error"]:
        print("ERROR: expected the failure to be reported, got {}".format(result))
        return 1
    return 0


def _test_wrapper_get_entities_metadata(_my_predbat):
    """Optional entity metadata is passed through when present."""
    print("Test: the get_entities tool carries optional metadata")
    wrapper, base = make_wrapper()
    base.dashboard_values = [
        {
            "entity_id": "predbat.soc_kw",
            "state": "5.0",
            "friendly_name": "SOC",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "measurement",
            "icon": "mdi:battery",
        }
    ]

    result = run_async(wrapper._execute_get_entities({}))

    entity = result["data"][0]
    for key, value in (("device_class", "energy"), ("state_class", "measurement"), ("icon", "mdi:battery")):
        if entity.get(key) != value:
            print("ERROR: expected {}={}, got {}".format(key, value, entity.get(key)))
            return 1
    return 0


def _test_wrapper_plan_override_backend_error(_my_predbat):
    """A failure applying the override is reported and the plan is left alone."""
    print("Test: the set_plan_override tool reports a backend failure")
    wrapper, base = make_wrapper()

    async def exploding_select(config_item, value):
        """Raise as a failing manual selection would."""
        raise RuntimeError("selection rejected")

    base.async_manual_select = exploding_select

    result = run_async(wrapper._execute_set_plan_override({"action": "charge", "time": "12:00"}))

    if result["success"] or "selection rejected" not in result["error"]:
        print("ERROR: expected the failure to be reported, got {}".format(result))
        return 1
    if base.update_pending:
        print("ERROR: a failed override should not request a plan refresh")
        return 1
    return 0


def _test_create_mcp_server_factory(_my_predbat):
    """The factory returns a wrapper bound to the supplied base."""
    print("Test: create_mcp_server builds a wrapper")
    from web_mcp import create_mcp_server

    base = MockMCPBase()
    wrapper = create_mcp_server(base, base.log)

    if not isinstance(wrapper, MCPServerWrapper):
        print("ERROR: expected an MCPServerWrapper, got {}".format(type(wrapper)))
        return 1
    if wrapper.base is not base or wrapper.prefix != base.prefix:
        print("ERROR: the wrapper is not bound to the supplied base")
        return 1
    if not base.log_messages:
        print("ERROR: expected the factory to log its creation")
        return 1

    # Without a log function the wrapper falls back to print rather than failing
    quiet = create_mcp_server(MockMCPBase())
    if quiet.log is not print:
        print("ERROR: expected print as the default logger")
        return 1
    return 0


def test_web_mcp(my_predbat=None):
    """Run the MCP server test suite, returning non-zero if any sub-test failed."""
    sub_tests = [
        ("jwt_secret", _test_jwt_secret_derivation, "JWT signing key derivation"),
        ("token_claims", _test_generate_access_token_claims, "Access token claims"),
        ("token_valid", _test_verify_access_token_valid, "Valid token accepted"),
        ("token_foreign_sig", _test_verify_access_token_rejects_foreign_signature, "Foreign signature rejected"),
        ("token_audience", _test_verify_access_token_rejects_wrong_audience, "Wrong audience rejected"),
        ("token_expired", _test_verify_access_token_rejects_expired, "Expired token rejected"),
        ("token_type", _test_verify_access_token_rejects_wrong_type, "Non-access token rejected"),
        ("token_garbage", _test_verify_access_token_rejects_garbage, "Malformed token rejected"),
        ("client_verify", _test_verify_oauth_client, "Client secret verification"),
        ("canonical_uri", _test_canonical_server_uri, "Canonical server URI"),
        ("authorize_params", _test_authorize_get_requires_parameters, "Authorize parameter validation"),
        ("authorize_pkce", _test_authorize_get_requires_pkce, "PKCE required"),
        ("authorize_s256", _test_authorize_get_requires_s256, "S256 required"),
        ("authorize_login", _test_authorize_get_shows_login, "Sign-in page"),
        ("authorize_bad_secret", _test_authorize_post_wrong_secret, "Bad secret refused"),
        ("authorize_issue_code", _test_authorize_post_issues_code, "Authorization code issued"),
        ("authorize_post_params", _test_authorize_post_missing_parameters, "POST parameter validation"),
        ("token_grant_type", _test_token_unsupported_grant, "Unsupported grant type"),
        ("token_cc_success", _test_token_client_credentials_success, "Client credentials success"),
        ("token_cc_bad", _test_token_client_credentials_bad_secret, "Client credentials refused"),
        ("token_cc_missing", _test_token_client_credentials_missing, "Client credentials required"),
        ("token_form_body", _test_token_form_encoded_body, "Form-encoded token request"),
        ("token_ac_success", _test_token_authorization_code_success, "Code exchange success"),
        ("token_ac_replay", _test_token_authorization_code_replay, "Code replay refused"),
        ("token_ac_expired", _test_token_authorization_code_expired, "Expired code refused"),
        ("token_ac_binding", _test_token_authorization_code_binding, "Code client/redirect binding"),
        ("token_ac_pkce", _test_token_authorization_code_pkce_enforced, "PKCE enforcement"),
        ("token_ac_unknown", _test_token_authorization_code_unknown, "Unknown code refused"),
        ("token_ac_params", _test_token_authorization_code_missing_params, "Code exchange parameters"),
        ("token_ac_scope", _test_token_authorization_code_scope, "Code exchange scope"),
        ("endpoint_errors", _test_endpoint_error_handling, "Broken request bodies"),
        ("register_json", _test_register_requires_json, "Registration content type"),
        ("register_creds", _test_register_returns_credentials, "Registration credentials"),
        ("metadata", _test_metadata_endpoints, "OAuth discovery metadata"),
        ("static_routes", _test_options_and_static_routes, "Preflight, favicon and 404"),
        ("mcp_needs_bearer", _test_mcp_endpoints_require_bearer, "MCP endpoints require a token"),
        ("mcp_bad_token", _test_mcp_endpoints_reject_bad_token, "MCP endpoints refuse bad tokens"),
        ("mcp_oauth_token", _test_mcp_endpoints_accept_oauth_token, "MCP endpoints accept OAuth tokens"),
        ("mcp_legacy_secret", _test_mcp_endpoints_accept_legacy_secret, "MCP endpoints accept the shared secret"),
        ("mcp_unavailable", _test_mcp_endpoints_unavailable, "MCP server unavailable"),
        ("mcp_handler_error", _test_mcp_endpoints_handler_error, "MCP handler failure"),
        ("mcp_get_oauth", _test_mcp_get_oauth_and_errors, "MCP GET OAuth and failures"),
        ("server_start", _test_server_start_registers_routes, "Server start route registration"),
        ("server_disabled", _test_server_start_disabled, "Disabled server does not start"),
        ("wrapper_lifecycle", _test_wrapper_lifecycle, "Wrapper start/stop"),
        ("wrapper_non_post", _test_wrapper_rejects_non_post, "Wrapper refuses non-POST"),
        ("wrapper_parse_error", _test_wrapper_parse_error, "Wrapper parse error"),
        ("wrapper_routing", _test_wrapper_method_routing, "Wrapper JSON-RPC routing"),
        ("wrapper_get_plan", _test_wrapper_get_plan, "get_plan tool"),
        ("wrapper_get_entities", _test_wrapper_get_entities_filter, "get_entities tool"),
        ("wrapper_get_apps", _test_wrapper_get_apps, "get_apps tool"),
        ("wrapper_get_config", _test_wrapper_get_config, "get_config tool"),
        ("wrapper_get_status", _test_wrapper_get_status, "get_status tool"),
        ("wrapper_set_config", _test_wrapper_set_config, "set_config tool"),
        ("wrapper_plan_override", _test_wrapper_set_plan_override, "set_plan_override actions"),
        ("wrapper_plan_override_bad", _test_wrapper_set_plan_override_rejections, "set_plan_override validation"),
        ("wrapper_tools_call", _test_wrapper_tools_call_dispatch, "tools/call dispatch"),
        ("wrapper_tools_error", _test_wrapper_tools_call_error, "tools/call failure handling"),
        ("wrapper_handle_request", _test_wrapper_handle_request_alias, "Wrapper handle_request alias"),
        ("wrapper_internal_error", _test_wrapper_internal_error, "Wrapper dispatch failure"),
        ("wrapper_plan_error", _test_wrapper_get_plan_error, "get_plan failure handling"),
        ("wrapper_entity_metadata", _test_wrapper_get_entities_metadata, "get_entities metadata"),
        ("wrapper_override_error", _test_wrapper_plan_override_backend_error, "set_plan_override failure"),
        ("factory", _test_create_mcp_server_factory, "create_mcp_server factory"),
    ]

    print("\n" + "=" * 70)
    print("MCP SERVER TEST SUITE")
    print("=" * 70)

    failed = 0
    passed = 0

    for test_name, test_func, test_desc in sub_tests:
        print("\n[{}] {}".format(test_name, test_desc))
        print("-" * 70)
        try:
            test_result = test_func(my_predbat)
        except Exception as e:
            import traceback

            traceback.print_exc()
            print("✗ FAILED: {} raised {}".format(test_name, e))
            failed += 1
            continue

        if test_result:
            print("✗ FAILED: {}".format(test_name))
            failed += 1
        else:
            print("✓ PASSED: {}".format(test_name))
            passed += 1

    print("\n" + "=" * 70)
    print("MCP RESULTS: {} passed, {} failed".format(passed, failed))
    print("=" * 70)
    return failed
