#!/usr/bin/env python3
"""Proxy helpers for the youtube-content skill."""

from __future__ import annotations

import os


# === ROSENBLATT PATCH START: optional transcript proxies ===
# Reason: YouTube often blocks cloud-provider IPs even when network egress is
# open. Allow this skill to opt into proxies without changing the default path.
# Upstream: T1 candidate
def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _first_env(*names: str) -> str | None:
    for name in names:
        value = _env_value(name)
        if value:
            return value
    return None


def _parse_locations(value: str | None) -> list[str] | None:
    if not value:
        return None
    locations = [item.strip() for item in value.split(",") if item.strip()]
    return locations or None


def _parse_retries(value: str | None) -> int:
    if not value:
        return 10
    retries = int(value)
    if retries < 0:
        raise ValueError("YOUTUBE_TRANSCRIPT_WEBSHARE_RETRIES must be non-negative")
    return retries


def build_proxy_config_from_env():
    from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

    webshare_username = _env_value("YOUTUBE_TRANSCRIPT_WEBSHARE_USERNAME")
    webshare_password = _env_value("YOUTUBE_TRANSCRIPT_WEBSHARE_PASSWORD")
    if webshare_username or webshare_password:
        if not webshare_username or not webshare_password:
            raise ValueError(
                "YOUTUBE_TRANSCRIPT_WEBSHARE_USERNAME and "
                "YOUTUBE_TRANSCRIPT_WEBSHARE_PASSWORD must be set together"
            )
        return WebshareProxyConfig(
            proxy_username=webshare_username,
            proxy_password=webshare_password,
            filter_ip_locations=_parse_locations(
                _env_value("YOUTUBE_TRANSCRIPT_WEBSHARE_LOCATIONS")
            ),
            retries_when_blocked=_parse_retries(
                _env_value("YOUTUBE_TRANSCRIPT_WEBSHARE_RETRIES")
            ),
        )

    http_url = _first_env("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
    https_url = _first_env(
        "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"
    )
    if http_url or https_url:
        return GenericProxyConfig(http_url=http_url, https_url=https_url)

    return None


def blocked_request_hint() -> str:
    return (
        "YouTube is blocking requests from this IP. This is common on AWS, GCP, and Azure. "
        "Set HTTP_PROXY/HTTPS_PROXY for a generic proxy, or configure Webshare with "
        "YOUTUBE_TRANSCRIPT_WEBSHARE_USERNAME and YOUTUBE_TRANSCRIPT_WEBSHARE_PASSWORD."
    )


# === ROSENBLATT PATCH END ===