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

from .base import ProviderError, ProviderFatalError

# 429 (rate limited) and 5xx are transient; retrying is the correct response.
# Other 4xx (bad key, unknown model) will not improve and should stop the run.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def post_json(
    provider_label: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_payload: Any = None,
    data: Any = None,
    timeout: int = 60,
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
            time.sleep(2**attempt)
            continue

        if response.status_code in RETRYABLE_STATUSES:
            if last_attempt:
                raise ProviderError(
                    f"{provider_label} API error {response.status_code} after "
                    f"{attempts} attempts: {response.text}"
                )
            time.sleep(2**attempt)
            continue

        if response.status_code >= 400:
            raise ProviderFatalError(
                f"{provider_label} API error {response.status_code}: {response.text}"
            )

        try:
            return response.json()
        except ValueError as exc:
            # Decoding used to sit outside the guarded block in several
            # providers, so a malformed 200 escaped as a raw JSONDecodeError.
            raise ProviderError(f"{provider_label} returned a non-JSON response: {exc}") from exc

    # Unreachable: every branch above returns or raises.
    raise ProviderError(f"{provider_label} request failed without an exception")
