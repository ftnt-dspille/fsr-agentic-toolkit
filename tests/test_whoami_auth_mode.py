"""whoami and JWT-only tools degrade sanely under API-key auth.

An API key is not a user: it cannot reach the DAS ``/api/auth`` routes. The
agent must still get a working identity read and a clear, actionable signal
when an operation genuinely needs a JWT -- never a raw traceback it will retry.
"""
from __future__ import annotations

import pytest

from fsr_agentic_toolkit.verbs import tools
from pyfsr.exceptions import UnsupportedAuthOperationError


class _ApiKeyClient:
    """Mimics a pyfsr client authenticated with an API key."""

    base_url = "https://fortisoar.example.com"

    class _Users:
        def whoami(self):  # the DAS /api/auth route rejects API keys
            raise UnsupportedAuthOperationError("auth", "APIKeyAuth")

    users = _Users()

    def get(self, path):
        assert path == "/api/3/actors/current"
        return {"@type": "ApiKey", "name": "soar-connector",
                "uuid": "u-1", "userId": "k-1",
                "firstname": None, "lastname": None, "email": None}

    def version(self):
        return "8.0.1-6087"


class _JwtClient:
    base_url = "https://fortisoar.example.com"

    class _Users:
        def whoami(self):
            return {"loginid": "analyst", "person_uuid": "p-1", "user_id": "d-1",
                    "email": "a@example.com", "name": "A Analyst"}

    users = _Users()

    def version(self):
        return "8.0.1-6087"


def test_whoami_stops_at_the_actor_under_api_key():
    out = tools.dispatch(_ApiKeyClient(), "whoami", {})
    assert "error" not in out, out
    assert out["auth_mode"] == "api_key"
    assert out["user"]["principal_type"] == "ApiKey"
    assert out["user"]["name"] == "soar-connector"
    assert out["user"]["loginid"] is None
    assert out["version"] == "8.0.1-6087"


def test_whoami_uses_the_full_profile_under_jwt():
    out = tools.dispatch(_JwtClient(), "whoami", {})
    assert "error" not in out, out
    assert out["auth_mode"] == "jwt"
    assert out["user"]["loginid"] == "analyst"


def test_jwt_only_failure_is_flagged_with_a_hint():
    """A genuinely JWT-only op surfaces jwt_only=True + a hint, not a bare error."""
    err = tools._agent_error(
        UnsupportedAuthOperationError("auth", "APIKeyAuth"), tool="create_user"
    )["error"]
    assert err["jwt_only"] is True
    assert err["auth_operation"] == "auth"
    assert "API key" in err["hint"]
