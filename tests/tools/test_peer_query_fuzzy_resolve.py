"""Tests for _fuzzy_resolve_agent in tools/peer_query_tool.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hermes_peer.client import PeerNetworkError
from tools.peer_query_tool import _fuzzy_resolve_agent

MOCK_PEERS = [
    {"name": "owenwhite", "endpoint": "http://owenwhite:8080"},
    {"name": "eugenecho", "endpoint": "http://eugenecho:8080"},
    {"name": "justingriffin", "endpoint": "http://justingriffin:8080"},
    {"name": "ryanwalden", "endpoint": "http://ryanwalden:8080"},
]


@patch("tools.peer_query_tool.list_agents", return_value=MOCK_PEERS)
def test_prefix_match_single(mock_list):
    """Single prefix match returns the peer."""
    result = _fuzzy_resolve_agent("owen", requester="eugenecho", timeout=10)
    assert result["name"] == "owenwhite"


@patch("tools.peer_query_tool.list_agents", return_value=MOCK_PEERS)
def test_substring_fallback(mock_list):
    """Falls back to substring when no prefix match."""
    result = _fuzzy_resolve_agent("walden", requester="eugenecho", timeout=10)
    assert result["name"] == "ryanwalden"


@patch("tools.peer_query_tool.list_agents", return_value=MOCK_PEERS)
def test_ambiguous_prefix_raises(mock_list):
    """Multiple prefix matches raise PeerNetworkError."""
    # Both "justingriffin" and nothing else start with "justin", but let's
    # use a query that hits multiple via substring.
    peers = MOCK_PEERS + [{"name": "justinhromalik", "endpoint": "http://justinhromalik:8080"}]
    mock_list.return_value = peers
    with pytest.raises(PeerNetworkError, match="Ambiguous"):
        _fuzzy_resolve_agent("justin", requester="eugenecho", timeout=10)


@patch("tools.peer_query_tool.list_agents", return_value=MOCK_PEERS)
def test_no_match_raises(mock_list):
    """No matches raise PeerNetworkError with unknown message."""
    with pytest.raises(PeerNetworkError, match="Unknown peer agent"):
        _fuzzy_resolve_agent("nonexistent", requester="eugenecho", timeout=10)


@patch("tools.peer_query_tool.list_agents", return_value=MOCK_PEERS)
def test_requester_excluded(mock_list):
    """Requester is excluded from candidates."""
    with pytest.raises(PeerNetworkError, match="Unknown peer agent"):
        _fuzzy_resolve_agent("eugene", requester="eugenecho", timeout=10)


@patch("tools.peer_query_tool.list_agents", return_value=MOCK_PEERS)
def test_case_insensitive(mock_list):
    """Matching is case-insensitive."""
    result = _fuzzy_resolve_agent("Owen", requester="eugenecho", timeout=10)
    assert result["name"] == "owenwhite"


@patch("tools.peer_query_tool.list_agents", side_effect=Exception("connection refused"))
def test_registry_error_wrapped(mock_list):
    """Non-PeerNetworkError from list_agents is wrapped."""
    with pytest.raises(PeerNetworkError, match="Registry lookup failed"):
        _fuzzy_resolve_agent("owen", requester="eugenecho", timeout=10)


@patch("tools.peer_query_tool.list_agents", side_effect=PeerNetworkError("timeout"))
def test_peer_network_error_passthrough(mock_list):
    """PeerNetworkError from list_agents is re-raised as-is."""
    with pytest.raises(PeerNetworkError, match="timeout"):
        _fuzzy_resolve_agent("owen", requester="eugenecho", timeout=10)
