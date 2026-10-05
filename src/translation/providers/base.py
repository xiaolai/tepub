from __future__ import annotations

import abc

from config import ProviderConfig
from state.models import ExtractMode, Segment


class ProviderError(RuntimeError):
    pass


class ProviderFatalError(ProviderError):
    """Fatal provider error that should abort the translation run."""


# Statuses after which every remaining segment would fail the same way: a key the
# provider rejects, or a model or endpoint that does not exist.
FATAL_STATUSES = frozenset({401, 403, 404})

# Statuses that mean "try again later": rate limits, timeouts, overloaded or
# failing servers (Anthropic reports overload as 529).
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def error_for_status(provider_label: str, status: int, detail: str) -> ProviderError:
    """The one place an HTTP status becomes an error kind.

    Fatal stops the run; anything else fails only this segment, and the run's
    retry pass tries it again. A 400 used to be fatal, so one segment the
    provider rejected as too long ended the whole run.
    """
    message = f"{provider_label} API error {status}: {detail}"
    if status in FATAL_STATUSES:
        return ProviderFatalError(message)
    return ProviderError(message)


def classify_exception(
    provider_label: str, exc: Exception, *, status: int | None
) -> ProviderError:
    """Classify an SDK exception by the HTTP status it carries, if any.

    Anthropic, Gemini and DeepL turned every exception into a fatal error, so a
    single rate-limit reply ended the run. With no status the failure happened
    before a reply arrived, a network problem, which is worth retrying.
    """
    if status is None:
        return ProviderError(f"{provider_label} request failed: {exc}")
    return error_for_status(provider_label, status, str(exc))


class BaseProvider(abc.ABC):
    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def model(self) -> str:
        return self.config.model

    #: Whether this provider preserves HTML markup in a segment's content.
    #: Enforced by ensure_segment_supported below; it was previously declared and
    #: overridden but never read, so a provider that could not handle HTML
    #: silently received HTML anyway.
    supports_html: bool = True

    def ensure_segment_supported(self, segment: Segment) -> None:
        """Raise when this provider cannot faithfully handle the segment."""
        if segment.extract_mode == ExtractMode.HTML and not self.supports_html:
            raise ProviderFatalError(
                f"Provider {self.name!r} cannot translate HTML segments, but "
                f"segment {segment.segment_id} is HTML. Choose a provider that "
                f"preserves markup, or re-extract in text mode."
            )

    @abc.abstractmethod
    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        raise NotImplementedError


def ensure_translation_available(text: str | None) -> str:
    # `not text` accepts a whitespace-only response, which was then stored as a
    # completed translation and silently emptied that segment in the output.
    if text is None or not text.strip():
        raise ProviderError("Provider returned empty translation")
    return text


def ensure_not_truncated(truncated: bool, provider_label: str, segment: Segment) -> None:
    """Refuse a reply the provider says it cut off at its output limit.

    Each API reports this its own way; callers translate their signal into a
    boolean. Only Anthropic used to check, so the other providers stored the
    first part of a long segment as its complete translation.
    """
    if truncated:
        raise ProviderError(
            f"{provider_label} truncated its translation of segment {segment.segment_id} "
            f"at the output limit. Raise the provider's output limit, or split the segment."
        )
