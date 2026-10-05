"""Shared HTTP plumbing for the requests-based translation providers.

Grok, Ollama and OpenAI each reimplemented posting, status handling and JSON
decoding, and each got a slightly different subset right — one guarded decoding
inside the request try block, another did not; one retried transient statuses,
the others treated every error as fatal. The behaviour lives here once.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from .base import RETRYABLE_STATUSES, ProviderError, error_for_status

# A server may ask for a long wait; beyond this the retry pass is the better place
# to wait, so one request does not hold a worker for an hour.
MAX_RETRY_AFTER_SECONDS = 60.0

# Indirection so tests can observe waits without sleeping.
_sleep = time.sleep


def _retry_delay(response: Any, attempt: int) -> float:
    """Seconds to wait before retrying: the server's Retry-After if it gave one."""
    header = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    if header:
        try:
            return min(float(header), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass  # An HTTP-date; fall back to exponential backoff.
    return float(2**attempt)


def post_json(
    provider_label: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_payload: Any = None,
    data: Any = None,
    timeout: float = 60,
    attempts: int = 3,
) -> Any:
    """POST and return the decoded JSON body, retrying transient failures.

    Raises:
        ProviderError: transient failure that survived every attempt, or a
            successful response whose body is not valid JSON.
        ProviderFatalError: a non-retryable client error.
    """
    for attempt in range(attempts):
        last_attempt = attempt == attempts - 1

        try:
            response = requests.post(
                url,
                headers=headers,
                json=json_payload,
                data=data,
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            if last_attempt:
                raise ProviderError(
                    f"{provider_label} request failed after {attempts} attempts: {exc}"
                ) from exc
            _sleep(_retry_delay(None, attempt))
            continue

        if response.status_code in RETRYABLE_STATUSES:
            if last_attempt:
                raise ProviderError(
                    f"{provider_label} API error {response.status_code} after "
                    f"{attempts} attempts: {response.text}"
                )
            _sleep(_retry_delay(response, attempt))
            continue

        if response.status_code >= 400:
            raise error_for_status(provider_label, response.status_code, response.text)

        try:
            return response.json()
        except ValueError as exc:
            # Decoding used to sit outside the guarded block in several
            # providers, so a malformed 200 escaped as a raw JSONDecodeError.
            raise ProviderError(f"{provider_label} returned a non-JSON response: {exc}") from exc

    # Unreachable: every branch above returns or raises.
    raise ProviderError(f"{provider_label} request failed without an exception")
