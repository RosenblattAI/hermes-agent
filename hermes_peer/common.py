from __future__ import annotations

import os
import re
import subprocess
from typing import Sequence


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ENV_SECRET_RE = re.compile(
    r"(?im)^([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY)[A-Z0-9_]*\s*[:=]\s*)(.+)$"
)
_SECRET_PATTERNS = [
    re.compile(r"\b(?:ghp|gho|ghu|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:sk|sk-proj|rk|xai|xai-api)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b(?:Bearer\s+)?eyJ[A-Za-z0-9._-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[-_ ]?key|token|secret|password|passwd|authorization)\b\s*[:=]\s*['\"]?[^\s,'\"]{8,}"
    ),
]


def sanitize_for_log(value: str) -> str:
    return value.replace("\r", "\\r").replace("\n", "\\n")


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def redact_secrets(text: str) -> str:
    if not text:
        return ""

    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = _ENV_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    return redacted


def get_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value == "":
        return default

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    return max(minimum, min(parsed, maximum))


def run_hermes_query(
    question: str,
    *,
    instruction: str,
    timeout_seconds: int,
    toolsets: Sequence[str] | None = None,
    source: str,
) -> str:
    prompt = f"{instruction.strip()}\n\nQuestion:\n{question.strip()}"
    command = ["hermes", "chat", "-q", prompt, "-Q", "--source", source]
    if toolsets:
        command.extend(["-t", ",".join(toolsets)])

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    raw_output = (completed.stdout or completed.stderr or "").strip()
    output = redact_secrets(strip_ansi(raw_output))

    if completed.returncode != 0:
        raise RuntimeError(output or f"Hermes query failed with exit code {completed.returncode}")
    if not output:
        raise RuntimeError("Hermes query returned no output")

    return output