# TEPUB Changelog

All notable changes to TEPUB are documented in this file.

---

## [0.2.0] - 2025-01-XX

### 🎉 Major New Features

#### **Dual TTS Provider Support**
- **OpenAI TTS Integration**: Premium text-to-speech with 6 high-quality voices
  - Voices: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
  - Two quality tiers: `tts-1` (standard) and `tts-1-hd` (premium)
  - Adjustable speed: 0.25x to 4.0x
  - Direct AAC output for optimal quality
  - Cost: ~$11-22 per 300-page book
- **Edge TTS** (Microsoft): Free, 57+ voices in multiple languages (remains default)
- Provider-specific output directories: `audiobook@edgetts/` and `audiobook@openaitts/`
- CLI options: `--tts-provider`, `--tts-model`, `--tts-speed`, `--voice`
- Settings persist across sessions for easy resumption

#### **Enhanced Configuration System**
- **Comprehensive Documentation**: Completely rewritten `config.example.yaml`
  - Accurate system prompt documentation matching actual codebase
  - Clear explanations of all placeholders and auto-generated instructions
  - Directory structure clarification (work_root, work_dir, cache_dir)
  - TTS provider comparison with cost breakdowns
  - Real-world examples for different book types
- **Environment Variables**: Expanded support with better organization
  - Added `TEPUB_CACHE_DIR` for custom cache locations
  - Organized into API Keys, Service URLs, Directories, and Audiobook sections

### ⚡ Performance Improvements

#### **OpenAI TTS Optimization**
- Direct AAC output format (instead of MP3 → AAC conversion)
- Eliminates intermediate conversion step for better quality
- Faster processing with lower memory usage
- Matches M4A container format natively

### 📚 Documentation

#### **Complete Documentation Overhaul**
- **README.md**: Rewritten for clarity and completeness
  - Comprehensive OpenAI TTS documentation
  - Updated cost comparisons for all services
  - Clear examples for both TTS providers
  - Provider-specific folder structure explained
- **INSTALL.md**: Updated with OpenAI TTS setup instructions
- **config.example.yaml**: Thoroughly updated to match codebase implementation

### 🔧 Configuration Changes

**New Settings:**
- `audiobook_tts_provider`: Choose between "edge" or "openai" (default: "edge")
- `audiobook_tts_model`: OpenAI model selection ("tts-1" or "tts-1-hd")
- `audiobook_tts_speed`: Speech speed for OpenAI TTS (0.25-4.0, default: 1.0)

**Enhanced Settings:**
- `work_root`: Global TEPUB directory (default: `~/.tepub/`)
- `work_dir`: Per-book workspace (default: next to EPUB file)
- `cache_dir`: Temporary files (default: `work_root/cache`)

### 🛠️ Technical Improvements

- **TTS Abstraction Layer**: Clean provider interface for easy extensibility
- **Factory Pattern**: `create_tts_engine()` for provider instantiation
- **State Management**: TTS provider settings saved in audiobook state
- **Graceful Degradation**: Optional OpenAI dependency with clear error messages
- **Provider Detection**: Auto-selects file format based on TTS engine (.aac for OpenAI, .mp3 for Edge)

### 📦 Dependencies

**Added:**
- `openai>=1.0`: Required for OpenAI TTS support (included by default)

---

## [0.1.0] - 2024-XX-XX

### 🎉 Initial Public Release

#### **Core Features**

**Translation**
- Multi-language book translation using AI services
- Support for OpenAI, Anthropic Claude, Google Gemini, xAI Grok, DeepL, and Ollama
- Two output modes:
  - **Bilingual**: Original and translation side-by-side
  - **Translation-only**: Professional translated edition
- Automatic language detection
- Parallel processing with configurable workers
- Resume capability for interrupted translations
- Smart skip rules for front/back matter
- Customizable translation prompts

**Audiobook Generation**
- Text-to-speech using Microsoft Edge TTS
- 57+ voices in multiple languages
- Chapter-based navigation with TOC markers
- Automatic cover art detection and embedding
- M4B format with chapter metadata
- Resume capability for long books
- Configurable voice, rate, and volume

**Export Formats**
- **EPUB**: Bilingual and translation-only editions
- **Web**: Interactive HTML viewer with live translation toggle
- **Markdown**: Plain text export with images and formatting

**Configuration**
- Two-level config system (global and per-book)
- YAML-based configuration
- Environment variable support
- Custom skip rules
- Provider failover (automatic fallback)
- Retry logic with exponential backoff

#### **CLI Commands**

```bash
tepub extract <epub>           # Extract book structure
tepub translate <epub>         # Translate content
tepub export <epub>            # Generate output files
tepub audiobook <epub>         # Create audiobook
tepub pipeline <epub>          # All-in-one workflow
tepub debug                    # Diagnostic tools
```

#### **Technical Stack**
- Python 3.10+ required (3.11+ recommended)
- Pydantic for configuration validation
- Rich for terminal UI
- Click for CLI framework
- ebooklib for EPUB handling
- FFmpeg for audiobook assembly
- Edge TTS for text-to-speech

---

## Version History

- **0.2.0** (2025-01-XX): OpenAI TTS support, enhanced configuration
- **0.1.0** (2024-XX-XX): Initial public release

For detailed commit history, run: `git log --oneline --decorate`

---

## Upgrade Notes

### 0.1.0 → 0.2.0

**Breaking Changes:**
- None! Fully backward compatible.

**New Features You Can Use:**
- Set `audiobook_tts_provider: openai` in config to use OpenAI TTS
- Use `--tts-provider openai` flag for one-time OpenAI audiobook creation
- Audiobooks now save to provider-specific folders (allows creating both versions)

**Configuration Migration:**
- Old configs work without changes
- Add `OPENAI_API_KEY` environment variable to enable OpenAI TTS
- Review new `config.example.yaml` for enhanced documentation

**Directory Structure:**
- Old: `mybook/audiobook/mybook.m4b`
- New: `mybook/audiobook@edgetts/mybook.m4b` or `mybook/audiobook@openaitts/mybook.m4b`
- Legacy `audiobook/` folders remain compatible

---

**Questions?** Check [README.md](README.md) or open an issue on [GitHub](https://github.com/xiaolai/tepub/issues)
