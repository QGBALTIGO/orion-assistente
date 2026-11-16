"""Cakto granted scopes must be observed before considering a live checkout."""
from app.cakto_api import CaktoClient


def test_missing_scope_metadata_is_unknown_not_granted(monkeypatch):
    monkeypatch.setenv("CAKTO_CLIENT_ID", "synthetic")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET", "synthetic")
    client = CaktoClient()
    assert client.granted_scopes(frozenset({"write", "payments"})) is None


def test_partial_scopes_never_count_as_payments(monkeypatch):
    monkeypatch.setenv("CAKTO_CLIENT_ID", "synthetic")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET", "synthetic")
    client = CaktoClient()
    client._oauth_scopes = frozenset({"read", "offers", "orders"})
    assert client.granted_scopes(frozenset({"orders"})) is True
    assert client.granted_scopes(frozenset({"payments"})) is False
    assert client.granted_scopes(frozenset({"write", "payments"})) is False


def test_full_explicit_grant_is_detected(monkeypatch):
    monkeypatch.setenv("CAKTO_CLIENT_ID", "synthetic")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET", "synthetic")
    client = CaktoClient()
    client._oauth_scopes = frozenset({
        "read", "write", "offers", "orders", "subscriptions", "payments", "card_tokens"
    })
    assert client.granted_scopes(frozenset({"write", "payments"})) is True
    assert client.granted_scopes(frozenset({"card_tokens", "payments"})) is True
