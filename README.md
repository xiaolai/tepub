# TEPUB - Tools for EPUB

**Transform EPUB books into translations, audiobooks, and web pages – automatically.**

TEPUB is a comprehensive toolkit for processing EPUB files. Translate books into any language, create professional audiobooks with natural voices, export to markdown, or publish as interactive websites.

[![Version](https://img.shields.io/badge/version-0.2.0-blue.svg)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Features

### 📖 **Translation**
- **Multi-language support**: Translate to/from any language
- **AI-powered**: OpenAI GPT-4, Anthropic Claude, Google Gemini, xAI Grok, DeepL, or Ollama
- **Dual output modes**:
  - **Bilingual**: Original and translation side-by-side (perfect for learning)
  - **Translation-only**: Professional translated edition
- **Smart processing**: Auto-skip front/back matter, parallel translation, resume capability

### 🎧 **Audiobook Creation**
- **Dual TTS providers**:
  - **Edge TTS** (Free): 57+ voices in multiple languages, no API key required
  - **OpenAI TTS** (Premium): 6 high-quality voices with superior naturalness
- **Professional output**: M4A format with chapter markers and embedded cover art
- **Chapter management**: Export, edit, and update chapter titles and timestamps
- **Flexible control**: Adjustable speed, voice selection, resume support
- **Cost**: Free with Edge TTS, or ~$11-22 per 300-page book with OpenAI TTS

### 📱 **Export Formats**
- **Web**: Interactive HTML viewer with live translation toggle
- **Markdown**: Plain text with preserved formatting and images
- **EPUB**: Bilingual or translation-only editions

---

## Quick Start

### Installation

**Automatic (Mac/Linux)**
```bash
git clone https://github.com/xiaolai/tepub.git
cd tepub
./install.sh
source .venv/bin/activate
```

**Manual (All platforms)**
```bash
git clone https://github.com/xiaolai/tepub.git
cd tepub
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

See [INSTALL.md](INSTALL.md) for detailed platform-specific instructions.

### Translation Setup

**1. Get an API key** from your preferred provider:
- [OpenAI](https://platform.openai.com/) (Recommended: GPT-4, ~$0.50-2.00/book)
- [Anthropic](https://console.anthropic.com/) (Claude, great for literature)
- [Ollama](https://ollama.com/) (Free, runs locally)

**2. Configure TEPUB:**
```bash
# Create .env file with your API key
echo 'OPENAI_API_KEY=sk-your-key-here' > .env
```

### Basic Usage

**Translate a book:**
```bash
tepub extract mybook.epub
tepub translate mybook.epub --to "Simplified Chinese"
tepub export mybook.epub --epub
```

**Create audiobook (Free Edge TTS):**
```bash
tepub extract mybook.epub
tepub audiobook generate mybook.epub
# Interactive voice selection will appear
```

**Create audiobook (Premium OpenAI TTS):**
```bash
tepub audiobook generate mybook.epub --tts-provider openai --voice nova
# Requires OPENAI_API_KEY in environment
```

**All-in-one pipeline:**
```bash
tepub pipeline mybook.epub --to Spanish --epub
```

---

## Common Tasks

### Translation

**Translate to different languages:**
```bash
tepub pipeline book.epub --to "Simplified Chinese" --epub
tepub pipeline book.epub --to Spanish --epub
tepub pipeline book.epub --to French --epub
```

**Choose translation provider:**
```bash
tepub translate book.epub --to Spanish --provider anthropic
tepub translate book.epub --to Spanish --provider ollama
```

**Translation-only output (smaller file):**
```bash
tepub export book.epub --epub --output-mode translated-only
```

### Audiobooks

**Edge TTS (Free, 57+ voices):**
```bash
# Interactive voice selection
tepub audiobook generate book.epub

# Specify voice directly
tepub audiobook generate book.epub --voice en-US-GuyNeural    # Male
tepub audiobook generate book.epub --voice en-US-JennyNeural  # Female
tepub audiobook generate book.epub --voice en-GB-RyanNeural   # British

# See all voices
edge-tts --list-voices
```

**OpenAI TTS (Premium, 6 voices):**
```bash
# Standard quality (tts-1)
tepub audiobook generate book.epub --tts-provider openai --voice nova

# Higher quality (tts-1-hd)
tepub audiobook generate book.epub --tts-provider openai --tts-model tts-1-hd --voice nova

# Adjust speed
tepub audiobook generate book.epub --tts-provider openai --voice nova --tts-speed 1.2

# Available OpenAI voices:
# - alloy: Neutral, balanced
# - echo: Male, authoritative
# - fable: British, expressive
# - onyx: Deep male, professional
# - nova: Female, friendly
# - shimmer: Female, warm
```

**Custom cover image:**
```bash
tepub audiobook generate book.epub --cover-path ~/Pictures/mycover.jpg
```

**Chapter management:**
```bash
# Preview chapter structure before generating audiobook
tepub audiobook export-chapters book.epub
# Edit chapters.yaml to customize chapter titles
tepub audiobook generate book.epub  # Uses custom titles from chapters.yaml

# Extract chapters from existing audiobook
tepub audiobook export-chapters audiobook.m4a

# Update audiobook with edited chapter markers
tepub audiobook update-chapters audiobook.m4a chapters.yaml
```

### Export

**Create web version:**
```bash
tepub export book.epub --web
# Opens browser with interactive viewer
```

**Export to markdown:**
```bash
tepub extract book.epub
# Markdown files created automatically in: book/markdown/
```

---

## Configuration

TEPUB uses a two-level configuration system:

### Global Config: `~/.tepub/config.yaml`

Apply settings to all books:

```yaml
# Translation
source_language: auto
target_language: Simplified Chinese
translation_workers: 3

primary_provider:
  name: openai
  model: gpt-4o

# Audiobook
audiobook_tts_provider: edge    # or: openai
audiobook_workers: 3

# Skip rules
skip_rules:
  - keyword: index
  - keyword: appendix
```

### Per-Book Config: `book/config.yaml`

Created automatically when you run `tepub extract book.epub`. Override global settings:

```yaml
# Choose TTS provider
audiobook_tts_provider: openai
audiobook_tts_model: tts-1-hd
audiobook_voice: nova

# Or use Edge TTS
audiobook_tts_provider: edge
audiobook_voice: en-US-AriaNeural

# Custom cover
cover_image_path: ~/Pictures/mycover.jpg

# Output mode
output_mode: translated_only

# Skip specific sections
skip_rules:
  - keyword: prologue
  - keyword: epilogue
```

See [config.example.yaml](config.example.yaml) for all available options with detailed explanations.

---

## Output Structure

### Translation
```
mybook.epub                      # Original
mybook/                          # Workspace
├── config.yaml                  # Per-book settings
├── segments.json                # Extracted content
├── state.json                   # Translation progress
└── markdown/                    # Markdown export
    ├── 001_chapter-1.md
    └── images/
mybook_bilingual.epub            # Output: both languages
mybook_translated.epub           # Output: translation only
mybook_web/                      # Web viewer
```

### Audiobooks
```
mybook/
├── audiobook@edgetts/           # Edge TTS audiobooks
│   ├── mybook.m4b               # Final audiobook
│   └── segments/                # Cached audio segments
└── audiobook@openaitts/         # OpenAI TTS audiobooks
    ├── mybook.m4b
    └── segments/
```

Provider-specific folders let you create both versions for comparison.

---

## Advanced Features

### Resume Interrupted Work

TEPUB automatically saves progress. To resume:
```bash
# Just run the same command again
tepub translate book.epub --to Spanish
tepub audiobook generate book.epub
```

### Parallel Processing

Speed up translation (uses more API credits):
```yaml
# In config.yaml
translation_workers: 5    # Default: 3
audiobook_workers: 5      # Default: 3
```

### Custom Translation Style

```yaml
# In config.yaml
prompt_preamble: |
  You are a literary translator specializing in preserving artistic voice.
  {language_instruction}
  {mode_instruction}
  Maintain the author's style, tone, metaphors, and cultural nuances.
```

### Selective File Processing

After extraction, edit `book/config.yaml`:
```yaml
# Only translate specific files
translation_files:
  - Text/chapter-001.xhtml
  - Text/chapter-002.xhtml
  # - Text/appendix.xhtml    # Commented = skipped

# Different files for audiobook
audiobook_files:
  - Text/chapter-001.xhtml
  # - Text/copyright.xhtml   # Skip copyright in audiobook
```

### Debug Commands

```bash
tepub debug workspace book.epub    # Show workspace info
tepub debug pending                 # What's left to translate
tepub debug show-skip-list          # What was skipped
```

---

## Cost Estimates

### Translation (300-page book)
- **OpenAI GPT-4o**: ~$0.50-2.00
- **Anthropic Claude**: ~$0.30-1.50
- **Ollama (local)**: Free (requires powerful computer)

### Audiobook (300-page book, ~750,000 characters)
- **Edge TTS**: Free
- **OpenAI tts-1**: ~$11.25
- **OpenAI tts-1-hd**: ~$22.50

### Recommendations
- **Best Quality**: OpenAI GPT-4 + OpenAI TTS-1-HD (~$25 total)
- **Best Value**: OpenAI GPT-4 + Edge TTS (~$1.50 total)
- **Free**: Ollama + Edge TTS (requires local GPU)

---

## Troubleshooting

### "API key not found"
```bash
# Set environment variable
export OPENAI_API_KEY="sk-your-key-here"

# Or create .env file
echo 'OPENAI_API_KEY=sk-your-key-here' > .env
```

### "ModuleNotFoundError: No module named 'openai'"
```bash
pip install -e .[dev]
# Or specifically: pip install openai
```

### Audiobook has no sound
```bash
# Install FFmpeg
brew install ffmpeg           # Mac
sudo apt install ffmpeg       # Linux
# Windows: download from ffmpeg.org
```

### Translation fails
```bash
# Check status
tepub debug pending

# Reset errors and retry
rm book/state.json
tepub translate book.epub --to Spanish
```

More solutions: [GitHub Issues](https://github.com/xiaolai/tepub/issues)

---

## Privacy & Security

- **Local processing**: Books stay on your computer (except API calls)
- **No telemetry**: TEPUB collects no usage data
- **Provider privacy**: Translation APIs see text but don't store it long-term
- **Maximum privacy**: Use Ollama for fully local operation

---

## Requirements

- **Python**: 3.10 or newer (3.11+ recommended)
- **OS**: macOS, Linux, or Windows 10+
- **Disk**: ~500 MB
- **RAM**: 2-4 GB
- **FFmpeg**: Required for audiobooks (auto-installed on Mac/Linux)

---

## Support

- **Documentation**: You're reading it!
- **Installation**: [INSTALL.md](INSTALL.md)
- **Changelog**: [CHANGELOG.md](CHANGELOG.md)
- **Issues**: [GitHub Issues](https://github.com/xiaolai/tepub/issues)
- **Command help**: `tepub --help` or `tepub <command> --help`

---

## Credits

Built with:
- [ebooklib](https://github.com/aerkalov/ebooklib) - EPUB processing
- [edge-tts](https://github.com/rany2/edge-tts) - Free text-to-speech
- [OpenAI](https://openai.com/) - Translation and premium TTS
- [Rich](https://github.com/Textualize/rich) - Beautiful terminal output
- [Click](https://click.palletsprojects.com/) - CLI framework
- [Pydantic](https://pydantic.dev/) - Configuration validation

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## For Developers

<details>
<summary>Development Setup</summary>

```bash
git clone https://github.com/xiaolai/tepub.git
cd tepub
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

**Run tests:**
```bash
pytest
pytest --cov=src --cov-report=html
```

**Code quality:**
```bash
ruff check src tests
black src tests
```

**Project structure:**
```
src/
├── cli/              # Command-line interface
├── extraction/       # EPUB extraction
├── translation/      # Translation pipeline
├── audiobook/        # TTS and audiobook creation
├── injection/        # Insert translations into EPUB
├── web_export/       # Web viewer generation
├── epub_io/          # EPUB reading/writing
├── config/           # Configuration management
└── state/            # Progress tracking
```

</details>

---

**Made with ❤️ for language learners, audiobook enthusiasts, and book lovers everywhere.**

**Version 0.2.0** | [Changelog](CHANGELOG.md) | [Issues](https://github.com/xiaolai/tepub/issues)
