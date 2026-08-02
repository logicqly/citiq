"""
Tests for the M2 per-environment API-key auth.

Covers key parsing (bare + labeled), request-time env reads (rotation without a
redeploy), multi-key validity, and the 401 fail-closed / invalid-key paths.
No real DB or platform calls.
"""
from types import SimpleNamespace

import pytest

from app.api.v1 import dependencies as deps
from app.api.v1.dependencies import (
    V1Error,
    _configured_api_keys,
    _parse_api_keys,
    require_api_key,
)


# ── Parsing ───────────────────────────────────────────────────────────────────

def test_parse_bare_keys():
    assert _parse_api_keys("abc,def") == {"abc": "key1", "def": "key2"}


def test_parse_labeled_keys():
    assert _parse_api_keys("primary:abc, rotating:def") == {
        "abc": "primary",
        "def": "rotating",
    }


def test_parse_mixed_and_blank_entries():
    assert _parse_api_keys("primary:abc, , def, ") == {"abc": "primary", "def": "key3"}


def test_label_prefix_wins_when_prefix_is_a_label_token():
    # "label:key" always splits when the prefix is a clean label token.
    assert _parse_api_keys("k_live:secretpart") == {"secretpart": "k_live"}


def test_key_with_non_label_prefix_is_kept_whole():
    # A base64-ish key containing ':' whose prefix is NOT a label token (has '+')
    # is treated as one bare key, not mis-split into label:key.
    assert _parse_api_keys("ab+cd:ef/gh") == {"ab+cd:ef/gh": "key1"}


def test_parse_empty():
    assert _parse_api_keys("") == {}
    assert _parse_api_keys("   ") == {}


# ── Env read / rotation ───────────────────────────────────────────────────────

@pytest.fixture
def _clean_env(monkeypatch):
    monkeypatch.delenv("AUDIT_API_KEYS", raising=False)
    monkeypatch.setattr(deps.settings, "audit_api_keys", "", raising=False)
    yield monkeypatch


def test_env_var_takes_precedence_over_settings(_clean_env):
    _clean_env.setattr(deps.settings, "audit_api_keys", "settingskey", raising=False)
    _clean_env.setenv("AUDIT_API_KEYS", "envkey")
    assert _configured_api_keys() == {"envkey": "key1"}


def test_falls_back_to_settings_when_env_absent(_clean_env):
    _clean_env.setattr(deps.settings, "audit_api_keys", "fromsettings", raising=False)
    assert _configured_api_keys() == {"fromsettings": "key1"}


def test_rotation_takes_effect_without_reimport(_clean_env):
    """Simulate a live secret update: the new key is honoured on the next read."""
    _clean_env.setenv("AUDIT_API_KEYS", "old")
    assert set(_configured_api_keys()) == {"old"}
    # Overlap window: both valid.
    _clean_env.setenv("AUDIT_API_KEYS", "old,new")
    assert set(_configured_api_keys()) == {"old", "new"}
    # Old retired.
    _clean_env.setenv("AUDIT_API_KEYS", "new")
    assert set(_configured_api_keys()) == {"new"}


# ── require_api_key ───────────────────────────────────────────────────────────

def _request(header_value: str | None):
    headers = {"X-API-Key": header_value} if header_value is not None else {}
    return SimpleNamespace(headers=headers, state=SimpleNamespace())


@pytest.mark.asyncio
async def test_valid_key_returns_label(_clean_env):
    _clean_env.setenv("AUDIT_API_KEYS", "primary:k1, rotating:k2")
    req = _request("k2")
    label = await require_api_key(req)
    assert label == "rotating"
    assert req.state.api_key_label == "rotating"


@pytest.mark.asyncio
async def test_both_keys_valid_during_rotation(_clean_env):
    _clean_env.setenv("AUDIT_API_KEYS", "primary:k1, rotating:k2")
    assert await require_api_key(_request("k1")) == "primary"
    assert await require_api_key(_request("k2")) == "rotating"


@pytest.mark.asyncio
async def test_invalid_key_401(_clean_env):
    _clean_env.setenv("AUDIT_API_KEYS", "primary:k1")
    with pytest.raises(V1Error) as exc:
        await require_api_key(_request("wrong"))
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthorized"


@pytest.mark.asyncio
async def test_missing_header_401(_clean_env):
    _clean_env.setenv("AUDIT_API_KEYS", "primary:k1")
    with pytest.raises(V1Error) as exc:
        await require_api_key(_request(None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unconfigured_fails_closed(_clean_env):
    # No env var, no settings value → every request rejected.
    with pytest.raises(V1Error) as exc:
        await require_api_key(_request("anything"))
    assert exc.value.status_code == 401
