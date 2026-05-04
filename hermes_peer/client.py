from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from hermes_peer.common import redact_secrets


class PeerNetworkError(RuntimeError):
    """Raised when a peer registry or peer request fails."""


class PeerNotFoundError(PeerNetworkError):
    """Raised when a specific peer agent is not found."""


def get_registry_url() -> str:
    registry_url = os.getenv("REGISTRY_URL", "").rstrip("/")
    if not registry_url:
        raise PeerNetworkError("REGISTRY_URL is not configured")
    return registry_url


def list_agents(timeout_seconds: int = 5) -> list[dict[str, Any]]:
    registry_url = get_registry_url()
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(f"{registry_url}/agents")
        response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise PeerNetworkError("Registry returned an invalid agent list")
    return data


def get_agent(name: str, timeout_seconds: int = 5) -> dict[str, Any]:
    registry_url = get_registry_url()
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(f"{registry_url}/agents/{name}")
        if response.status_code == 404:
            raise PeerNetworkError(f"Unknown peer agent: {name}")
        response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise PeerNetworkError(f"Registry returned invalid data for peer {name}")
    return data


def query_peer(
    endpoint: str,
    *,
    question: str,
    requester: str,
    timeout_seconds: int,
) -> str:
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{endpoint.rstrip('/')}/query",
            json={
                "question": question,
                "requester": requester,
                "timeout_seconds": timeout_seconds,
            },
        )
        response.raise_for_status()

    data = response.json()
    answer = data.get("answer") if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        raise PeerNetworkError(f"Peer at {endpoint} returned an empty answer")
    return redact_secrets(answer.strip())


def broadcast_query(
    peers: list[dict[str, Any]],
    *,
    question: str,
    requester: str,
    timeout_seconds: int,
    max_peers: int,
) -> list[dict[str, str]]:
    if not peers:
        return []

    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(len(peers), max_peers)) as executor:
        future_to_peer = {
            executor.submit(
                query_peer,
                peer["endpoint"],
                question=question,
                requester=requester,
                timeout_seconds=timeout_seconds,
            ): peer
            for peer in peers
            if peer.get("endpoint")
        }

        for future in as_completed(future_to_peer):
            peer = future_to_peer[future]
            try:
                answer = future.result()
            except Exception:
                continue
            results.append({"agent": peer["name"], "response": answer})

    results.sort(key=lambda item: item["agent"])
    return results