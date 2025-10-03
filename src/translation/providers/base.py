from __future__ import annotations

import abc

from config import ProviderConfig
from state.models import Segment


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

    supports_html: bool = True

    @abc.abstractmethod
    def translate(self, segment: Segment, source_language: str, target_language: str) -> str:
        raise NotImplementedError


def ensure_translation_available(text: str | None) -> str:
    if not text:
        raise ProviderError("Provider returned empty translation")
    return text
