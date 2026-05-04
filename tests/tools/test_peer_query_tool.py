"""Regression tests for tools.peer_query_tool."""

import json
from unittest.mock import patch

from tools.peer_query_tool import peer_query


@patch("tools.peer_query_tool.query_peer", return_value="peer answer")
@patch(
    "tools.peer_query_tool.get_agent",
    return_value={"name": "owenwhite", "endpoint": "http://owenwhite:8080"},
)
def test_peer_query_direct_mode_returns_redacted_response(mock_get_agent, mock_query_peer):
    payload = json.loads(peer_query("status update", agent="owenwhite", timeout_seconds=30))

    assert payload == {
        "success": True,
        "mode": "direct",
        "agent": "owenwhite",
        "response": "peer answer",
    }
    mock_get_agent.assert_called_once_with("owenwhite", timeout_seconds=10)
    mock_query_peer.assert_called_once_with(
        "http://owenwhite:8080",
        question="status update",
        requester="current-agent",
        timeout_seconds=30,
    )