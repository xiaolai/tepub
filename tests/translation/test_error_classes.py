"""Every provider error is one of three kinds, decided in one place.

- fatal: retrying cannot help and every segment will fail the same way (a bad
  key, an unknown model); the run stops;
- transient: rate limits, timeouts, overloaded servers; retried, honouring
  Retry-After;
- per segment: the provider rejected this request (too long, malformed); only
  this segment fails.

Before, Anthropic, Gemini and DeepL turned every exception into a fatal error,
so one rate-limit reply ended the whole run, and the HTTP helper treated every
4xx, including a 400 for one oversized segment, as fatal.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from state.models import ExtractMode, Segment, SegmentMetadata
from translation.providers import ProviderError, ProviderFatalError, http
from translation.providers.base import classify_exception, error_for_status


class Response:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {"ok": True}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


def _replies(monkeypatch, *responses):
    queue = list(responses)
    sleeps: list[float] = []
    monkeypatch.setattr(http.requests, "post", lambda *a, **k: queue.pop(0))
    monkeypatch.setattr(http, "_sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_unknown_model_are_fatal(status) -> None:
    assert type(error_for_status("X", status, "")) is ProviderFatalError


@pytest.mark.parametrize("status", [400, 413, 422])
def test_a_rejected_request_fails_only_its_segment(status) -> None:
    assert type(error_for_status("X", status, "")) is ProviderError


def test_retry_after_is_honoured(monkeypatch) -> None:
    sleeps = _replies(monkeypatch, Response(429, headers={"Retry-After": "7"}), Response(200))
    assert http.post_json("X", "http://x") == {"ok": True}
    assert sleeps == [7.0]


def test_retry_after_is_capped(monkeypatch) -> None:
    sleeps = _replies(monkeypatch, Response(503, headers={"Retry-After": "3600"}), Response(200))
    http.post_json("X", "http://x")
    assert sleeps == [http.MAX_RETRY_AFTER_SECONDS]


def test_a_400_is_not_fatal(monkeypatch) -> None:
    _replies(monkeypatch, Response(400, body={"error": "too long"}))
    with pytest.raises(ProviderError) as excinfo:
        http.post_json("X", "http://x")
    assert not isinstance(excinfo.value, ProviderFatalError)


def test_a_401_is_fatal(monkeypatch) -> None:
    _replies(monkeypatch, Response(401))
    with pytest.raises(ProviderFatalError):
        http.post_json("X", "http://x")


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (SimpleNamespace(status_code=429), ProviderError),
        (SimpleNamespace(status_code=529), ProviderError),
        (SimpleNamespace(status_code=401), ProviderFatalError),
        (SimpleNamespace(code=503), ProviderError),
        (SimpleNamespace(code=403), ProviderFatalError),
        (SimpleNamespace(), ProviderError),  # no status: a network failure
    ],
)
def test_sdk_exceptions_are_classified_by_status(exc, kind) -> None:
    error = classify_exception("SDK", Exception("boom"), status=getattr(exc, "status_code", None) or getattr(exc, "code", None))
    assert type(error) is kind


def test_deepl_rate_limit_is_retried(monkeypatch) -> None:
    from config import ProviderConfig
    from translation.providers import create_provider

    sleeps = _replies(
        monkeypatch,
        Response(429, headers={"Retry-After": "2"}),
        Response(200, body={"translations": [{"text": "Bonjour."}]}),
    )
    provider = create_provider(ProviderConfig(name="deepl", model="deepl", api_key="k"))
    segment = Segment(
        segment_id="s1",
        file_path=Path("c.xhtml"),
        xpath="/html/body/p",
        extract_mode=ExtractMode.TEXT,
        source_content="Hello.",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )
    assert provider.translate(segment, source_language="en", target_language="fr") == "Bonjour."
    assert sleeps == [2.0]
