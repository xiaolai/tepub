# TEPUB Changelog

All notable changes to TEPUB are documented in this file.

---

## [0.3.2] - 2026-08-02

### 🐛 Fixed

- **The CLI would not start from certain directories.** nltk's import guard
  refuses to load any module whose file lives under the current working
  directory. Tools installed with `uv tool` live under `$HOME`, so running
  `tepub` from `~`, `~/.local` or `/` aborted before the CLI came up. nltk was
  reaching the startup path only because command registration imports the
  audiobook module, which imported it at module scope.

  nltk is now imported on demand, inside the sentence-splitting helpers that
  actually need it. Verified working from `/`, `~`, `~/.local`, `/tmp` and a
  project directory.

### ⚡ Performance

- Roughly 76 ms shaved off every invocation: nltk is no longer imported for
  commands that never use it, which is all of them except audiobook synthesis.

---

## [0.3.1] - 2026-08-02

### 🐛 Fixed

- **tepub could not run on Python 3.13.** PEP 594 removed `audioop` from the
  standard library in 3.13, and `pydub` imports it, so `import cli.main` failed
  outright — the CLI would not start. `requires-python` had no upper bound, so
  installers were free to pick 3.13 and produce a broken install. The maintained
  `audioop-lts` backport is now required on 3.13 and above.

  This affected 0.3.0 on Python 3.13 only; 3.10-3.12 were unaffected.

### 🔧 Internal

- CI now tests Python 3.13 alongside 3.10-3.12. The matrix stopping at 3.12 is
  why this reached PyPI.

---

## [0.3.0] - 2026-08-02

Remediation of a 236-finding code audit. All 44 high-severity findings closed.

Released as a minor version rather than a patch to signal the breadth of change
(83 files) and the two newly-required dependencies. **No migration is needed and
no re-extraction is forced** — existing workspaces continue to work.

### 🔒 Security

These affect anyone who processes an EPUB from an untrusted source.

- **Path traversal on EPUB extraction.** Archive member names were joined
  directly onto the output directory. An absolute member name discards the base
  path entirely, and `..` components walk out of it, so a crafted EPUB could
  write anywhere the process had permission. Member names are now validated
  before anything is written (`UnsafeArchiveMemberError`).
- **Path traversal in the web exporter.** The same class of bug, unguarded, when
  copying manifest resources into the export directory.
- **Stored XSS in the web export.** `<script>` elements and inline event handlers
  (`onerror`, `onclick`, …) were never removed from book content, and
  `javascript:` URLs were explicitly preserved. Active content is now stripped,
  including SVG/MathML foreign content: literal `xlink:href`, animation elements
  that assign an href at runtime (`<animate>`, `<set>`), and URL-bearing
  attributes in any namespace or prefix.
- **Script breakout via book metadata.** Book data is embedded in a `<script>`
  element, but `<` was not escaped, so a title containing `</script>` closed the
  element and the remainder became live markup.
- **Sanitisation was conditional.** URL cleaning ran only when an optional
  argument was supplied, so the default code path sanitised nothing.

### 🐛 Data-loss fixes

- **`tepub extract` destroyed translations.** Re-running extraction wrote a fresh
  all-pending state, discarding every completed translation. It now merges.
- **Concurrent writes could clobber each other.** `atomic_write` locked a shared
  temporary path and replaced the target after releasing the lock. Three
  commands (`format`, `debug purge-refusals`, pre-injection polish) also did
  unlocked read-modify-write cycles that overwrote concurrent progress.
- **Segment id collisions.** Two files with the same basename in different
  directories produced identical segment ids, so one segment's state silently
  overwrote the other's. Only genuinely colliding segments are re-keyed, so
  existing workspaces keep working and no completed work is lost.

### 🔧 Correctness

- Chapter YAML could never round-trip: segment lists were written as a truncated
  comment, and unescaped titles produced invalid YAML.
- Chapter audio was reused based on file existence alone, serving stale audio
  after a voice, speed or text change. Changing any audio-affecting setting now
  invalidates completed segments.
- Audiobook synthesis crashed on a clean install (NLTK 3.9 moved the Punkt data).
- CJK text was tokenised with the English sentence splitter and came back as one
  unbroken sentence.
- `--work-dir` was silently ignored; `--quiet` had no effect on most output;
  `--verbose` was reset by the next logger created.
- Skip keywords matched as substrings, so "cover" matched "Discovering" and
  ordinary chapters were excluded from translation.
- A provider apology alone counted as a refusal, resetting good translations.
- `{language_instruction}`, documented as a prompt placeholder, raised KeyError.
- Traditional Chinese was silently translated as Simplified by DeepL.
- Anthropic responses longer than the token limit were silently truncated.
- A single 429 rate-limit response was treated as fatal instead of retried.

### 📦 Packaging

- **`html2text` and `PyYAML` are now declared dependencies.** Both were imported
  but never listed, so `tepub extract` and config parsing failed on a clean
  install. **No action needed on upgrade** — pip installs them.
- Optional provider extras added: `pip install tepub[anthropic]`,
  `tepub[gemini]`, `tepub[all-providers]`.
- Test coverage measured only six of eleven packages; now measures all of `src`.

### ⚠️ Notes

- No migration is required and no re-extraction is forced.
- Providers that cannot preserve HTML now fail loudly rather than silently
  mangling markup.
- `config validate` now reports unrecognised keys and actually validates
  per-book configs (it previously reported success regardless).

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
