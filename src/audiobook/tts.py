from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path

import edge_tts

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


class TTSEngine(ABC):
    """Base class for text-to-speech engines."""

    @abstractmethod
    def synthesize(self, text: str, output_path: Path) -> None:
        """Synthesize text to audio and save to output_path.

        Args:
            text: Text to convert to speech
            output_path: Path where audio file should be saved
        """
        pass


class EdgeTTSEngine(TTSEngine):
    """Microsoft Edge TTS engine (free, 57+ voices)."""

    def __init__(self, voice: str, rate: str | None = None, volume: str | None = None) -> None:
        self.voice = voice
        self.rate = rate
        self.volume = volume

    async def _synthesize_async(self, text: str, output_path: Path) -> None:
        kwargs = {"voice": self.voice}
        if self.rate is not None:
            kwargs["rate"] = self.rate
        if self.volume is not None:
            kwargs["volume"] = self.volume
        communicator = edge_tts.Communicate(text, **kwargs)
        await communicator.save(str(output_path))

    def synthesize(self, text: str, output_path: Path) -> None:
        asyncio.run(self._synthesize_async(text, output_path))


class OpenAITTSEngine(TTSEngine):
    """OpenAI TTS engine (paid, 6 premium voices).

    Voices: alloy, echo, fable, onyx, nova, shimmer
    Models: tts-1 (cheaper), tts-1-hd (higher quality)
    Speed: 0.25 to 4.0 (1.0 = normal)
    """

    def __init__(
        self,
        voice: str,
        model: str = "tts-1",
        speed: float = 1.0,
        api_key: str | None = None,
    ) -> None:
        if not HAS_OPENAI:
            raise ImportError(
                "OpenAI package required for OpenAI TTS. "
                "Install with: pip install openai"
            )

        self.voice = voice
        self.model = model
        self.speed = max(0.25, min(4.0, speed))  # Clamp to valid range

        # Use provided API key or fall back to environment variable
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self.client = OpenAI(api_key=self.api_key)

    def synthesize(self, text: str, output_path: Path) -> None:
        """Synthesize text using OpenAI TTS API.

        Outputs AAC format directly for optimal quality and performance.
        """
        response = self.client.audio.speech.create(
            model=self.model,
            voice=self.voice,  # type: ignore
            input=text,
            speed=self.speed,
            response_format="aac",  # Direct AAC output
        )

        # Save to file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        response.stream_to_file(str(output_path))


def create_tts_engine(
    provider: str,
    voice: str,
    **kwargs
) -> TTSEngine:
    """Factory function to create TTS engine based on provider.

    Args:
        provider: "edge" or "openai"
        voice: Voice name (provider-specific)
        **kwargs: Additional provider-specific parameters
            Edge TTS: rate, volume
            OpenAI TTS: model, speed, api_key

    Returns:
        TTSEngine instance

    Raises:
        ValueError: If provider is unknown

    Examples:
        >>> engine = create_tts_engine("edge", "en-US-GuyNeural", rate="+5%")
        >>> engine = create_tts_engine("openai", "nova", model="tts-1-hd", speed=1.1)
    """
    provider = provider.lower()

    if provider == "edge":
        return EdgeTTSEngine(
            voice=voice,
            rate=kwargs.get("rate"),
            volume=kwargs.get("volume"),
        )
    elif provider == "openai":
        return OpenAITTSEngine(
            voice=voice,
            model=kwargs.get("model", "tts-1"),
            speed=kwargs.get("speed", 1.0),
            api_key=kwargs.get("api_key"),
        )
    else:
        raise ValueError(
            f"Unknown TTS provider: {provider}. "
            f"Supported providers: edge, openai"
        )
