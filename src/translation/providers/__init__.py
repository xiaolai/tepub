from __future__ import annotations

from config import ProviderConfig

from .anthropic import AnthropicProvider
from .base import BaseProvider, ProviderError, ProviderFatalError
from .deepl import DeepLProvider
from .gemini import GeminiProvider
from .grok import GrokProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider


def create_provider(config: ProviderConfig) -> BaseProvider:
    registry = {
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
        "gemini": GeminiProvider,
        "grok": GrokProvider,
        "anthropic": AnthropicProvider,
        "deepl": DeepLProvider,
    }

    provider_cls = registry.get(config.name.lower())
    if not provider_cls:
        raise ProviderError(f"Unsupported provider: {config.name}")
    return provider_cls(config)


__all__ = [
    "BaseProvider",
    "ProviderError",
    "ProviderFatalError",
    "create_provider",
]
