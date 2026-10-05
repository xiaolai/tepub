from __future__ import annotations

import abc

from config import ProviderConfig
from state.models import ExtractMode, Segment


class ProviderError(RuntimeError):
    pass


class ProviderFatalError(ProviderError):
    """Fatal provider error that should abort the translation run."""


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
