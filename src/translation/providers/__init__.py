from __future__ import annotations

from config import ProviderConfig

from .anthropic import AnthropicProvider
from .base import BaseProvider, ProviderError, ProviderFatalError, ReplyRejectedError
from .deepl import DeepLProvider
from .gemini import GeminiProvider
from .grok import GrokProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider


_REGISTRY = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
    "grok": GrokProvider,
    "anthropic": AnthropicProvider,
    "deepl": DeepLProvider,
}

# The names a config or the command line may give as a provider.
PROVIDER_NAMES = tuple(sorted(_REGISTRY))


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_cls = _REGISTRY.get(config.name.lower())
    if not provider_cls:
        raise ProviderError(f"Unsupported provider: {config.name}")
    return provider_cls(config)


__all__ = [
    "PROVIDER_NAMES",
    "BaseProvider",
    "ProviderError",
    "ProviderFatalError",
    "ReplyRejectedError",
    "create_provider",
]
