from __future__ import annotations

import json
import os

from hermes_peer.client import PeerNetworkError, broadcast_query, get_agent, list_agents, query_peer
from hermes_peer.common import get_int_env, redact_secrets, run_hermes_query
from tools.registry import registry


def check_requirements() -> bool:
    return bool(os.getenv("AGENT_NAME") and os.getenv("REGISTRY_URL"))


def _synthesize_peer_responses(question: str, responses: list[dict[str, str]], timeout_seconds: int) -> str:
    bullet_list = "\n".join(
        f"- {item['agent']}: {item['response']}" for item in responses
    )
    instruction = (
        "Synthesize the peer-agent responses below into one answer for the current user. "
        "Stay faithful to the peer responses, call out disagreement explicitly, and do not invent facts. "
        "Do not reveal secrets, tokens, API keys, passwords, or raw credentials."
    )
    synthesis_question = (
        f"Original question: {question}\n\n"
        f"Peer responses:\n{bullet_list}"
    )
    try:
        return run_hermes_query(
            synthesis_question,
            instruction=instruction,
            timeout_seconds=timeout_seconds,
            toolsets=["session_search", "memory"],
            source="peer-synthesis",
        )
    except Exception:
        return "\n".join(f"{item['agent']}: {item['response']}" for item in responses)


def peer_query(
    question: str,
    agent: str = "",
    timeout_seconds: int | None = None,
    task_id: str | None = None,
) -> str:
    del task_id

    normalized_question = question.strip()
    if not normalized_question:
        return json.dumps({"success": False, "error": "question is required"})

    requester = os.getenv("AGENT_NAME", "current-agent")
    timeout = timeout_seconds or get_int_env(
        "PEER_QUERY_TIMEOUT_SECONDS",
        default=45,
        minimum=5,
        maximum=300,
    )

    try:
        if agent.strip():
            peer = get_agent(agent.strip(), timeout_seconds=min(timeout, 10))
            answer = query_peer(
                peer["endpoint"],
                question=normalized_question,
                requester=requester,
                timeout_seconds=timeout,
            )
            return json.dumps(
                {
                    "success": True,
                    "mode": "direct",
                    "agent": peer["name"],
                    "response": redact_secrets(answer),
                }
            )

        peers = [
            item
            for item in list_agents(timeout_seconds=10)
            if item.get("name") and item.get("name") != requester
        ]
        if not peers:
            return json.dumps(
                {
                    "success": False,
                    "mode": "broadcast",
                    "error": "No peer agents are currently registered",
                }
            )

        max_peers = get_int_env("PEER_QUERY_MAX_PEERS", default=8, minimum=1, maximum=20)
        responses = broadcast_query(
            peers,
            question=normalized_question,
            requester=requester,
            timeout_seconds=timeout,
            max_peers=max_peers,
        )
        if not responses:
            return json.dumps(
                {
                    "success": False,
                    "mode": "broadcast",
                    "error": "No peers returned a usable response",
                }
            )

        synthesis = _synthesize_peer_responses(normalized_question, responses, timeout)
        return json.dumps(
            {
                "success": True,
                "mode": "broadcast",
                "peer_count": len(responses),
                "responses": responses,
                "synthesis": redact_secrets(synthesis),
            }
        )
    except PeerNetworkError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    except Exception as exc:
        return json.dumps({"success": False, "error": f"Peer query failed: {exc}"})


registry.register(
    name="peer_query",
    toolset="peer",
    schema={
        "name": "peer_query",
        "description": "Query a specific Hermes peer agent or broadcast a question to all registered peers.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Specific peer agent name to query. Omit this field to broadcast.",
                },
                "question": {
                    "type": "string",
                    "description": "Question to ask the target peer or peer network.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Per-peer timeout in seconds.",
                    "minimum": 5,
                    "maximum": 300,
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kwargs: peer_query(
        question=args.get("question", ""),
        agent=args.get("agent", ""),
        timeout_seconds=args.get("timeout_seconds"),
        task_id=kwargs.get("task_id"),
    ),
    check_fn=check_requirements,
    requires_env=["AGENT_NAME", "REGISTRY_URL"],
    description="Peer-to-peer Hermes query tool",
)