from __future__ import annotations

from pathlib import Path

from config.models import AppSettings


def create_book_config_template(
    work_dir: Path,
    epub_name: str,
    metadata: dict[str, str | None] | None = None,
    segments_doc=None,
    input_epub: Path | None = None,
) -> None:
    """Create a config.yaml in the book's working directory with filled metadata.

    Args:
        work_dir: Book's working directory
        epub_name: Name of the EPUB file
        metadata: Book metadata dict with keys: title, author, publisher, year
                  If None, uses "Unknown" as defaults
        segments_doc: Optional SegmentsDocument for generating inclusion lists
        input_epub: Optional path to EPUB file for extracting TOC titles
    """
    config_path = work_dir / "config.yaml"
    if config_path.exists():
        return  # Don't overwrite existing config

    # Extract metadata with defaults
    meta = metadata or {}
    book_name = meta.get("title") or "Unknown"
    author = meta.get("author") or "Unknown"
    publisher = meta.get("publisher") or "Unknown"
    year_of_publication = meta.get("year") or "Unknown"

    # Generate US English voice list
    voice_lines = []
    try:
        from audiobook.voices import list_voices_for_language

        us_voices = list_voices_for_language("en-US")
        us_voices = sorted(us_voices, key=lambda v: v.get("ShortName", ""))
        for voice in us_voices:
            name = voice.get("ShortName", "")
            gender = voice.get("Gender", "")
            voice_lines.append(f"# audiobook_voice: {name:<30}  # {gender}")
    except Exception:
        # Fallback if voice listing fails
        voice_lines = [
            "# audiobook_voice: en-US-GuyNeural            # Male",
            "# audiobook_voice: en-US-JennyNeural          # Female",
        ]

    voices_section = "\n".join(voice_lines)

    # Build file→title mapping from TOC
    file_titles: dict[str, str] = {}
    if input_epub and input_epub.exists():
        try:
            from epub_io.reader import EpubReader
            from epub_io.toc_utils import parse_toc_to_dict

            # Use empty AppSettings for TOC parsing only
            temp_settings = AppSettings(work_dir=work_dir)
            reader = EpubReader(input_epub, temp_settings)

            # Parse TOC to get file→title mapping
            file_titles = parse_toc_to_dict(reader)
        except Exception:
            # If TOC parsing fails, continue without titles
            pass

    # Build file inclusion lists
    inclusion_lists_section = ""
    if segments_doc:
        # Get all unique file paths from segments (in spine order)
        file_paths_map: dict[str, int] = {}  # file_path -> min_spine_index
        for segment in segments_doc.segments:
            file_path_str = segment.file_path.as_posix()
            if file_path_str not in file_paths_map:
                file_paths_map[file_path_str] = segment.metadata.spine_index
            else:
                file_paths_map[file_path_str] = min(
                    file_paths_map[file_path_str], segment.metadata.spine_index
                )

        # Build skipped files map
        skipped_map: dict[str, str] = {}  # file_path -> reason
        for skipped_doc in segments_doc.skipped_documents:
            skipped_map[skipped_doc.file_path.as_posix()] = skipped_doc.reason

        # Combine and sort all files by spine index
        all_files = sorted(
            [(path, spine_idx) for path, spine_idx in file_paths_map.items()],
            key=lambda x: x[1],
        )

        # Add skipped files that might not be in segments
        for skipped_path, reason in skipped_map.items():
            if skipped_path not in file_paths_map:
                all_files.append((skipped_path, 9999))  # Put at end

        # Generate translation_files section
        translation_lines = []
        for file_path, _ in all_files:
            title = file_titles.get(file_path, "")
            title_comment = f"  # {title}" if title else ""
            if file_path in skipped_map:
                reason = skipped_map[file_path]
                translation_lines.append(f"  # - {file_path}  # (skipped: {reason}){title_comment}")
            else:
                translation_lines.append(f"  - {file_path}{title_comment}")

        # Generate audiobook_files section
        audiobook_lines = []
        for file_path, _ in all_files:
            title = file_titles.get(file_path, "")
            title_comment = f"  # {title}" if title else ""
            if file_path in skipped_map:
                reason = skipped_map[file_path]
                audiobook_lines.append(f"  # - {file_path}  # (skipped: {reason}){title_comment}")
            else:
                audiobook_lines.append(f"  - {file_path}{title_comment}")

        inclusion_lists_section = f"""
# ============================================================
# File Inclusion Lists
# ============================================================
# Control which EPUB files are processed for translation and audiobook.
# All spine files are listed below. Skipped files are commented out.
# To exclude a file: comment it out with #
# To include a skipped file: uncomment it by removing the #

# Translation inclusion list
# Files to process during translation
translation_files:
{chr(10).join(translation_lines)}

# Audiobook inclusion list
# Files to process during audiobook generation
audiobook_files:
{chr(10).join(audiobook_lines)}
"""

    template = f"""# Per-book configuration for: {epub_name}
# This file overrides global settings in ~/.tepub/config.yaml

# ============================================================
# Translation Settings
# ============================================================

# Source and target languages
# source_language: auto
# target_language: Simplified Chinese

# Output mode - uncomment one
# output_mode: bilingual        # Shows both original and translation
# output_mode: translated_only  # Only translated text

# --- Parallel Processing ---
# Uncomment to override global settings
# translation_workers: 3        # Number of parallel workers for translation
{inclusion_lists_section}
# ============================================================
# Translation System Prompt
# ============================================================
# Customize the AI translation prompt below.
# This overrides the global prompt in ~/.tepub/config.yaml
#
# The prompt below has been pre-filled with this book's metadata.
# You can customize it before running translation.
#
# Available runtime placeholders (filled automatically during translation):
#   {{{{source_language}}}}  - Source language name (e.g., "English")
#   {{{{target_language}}}}  - Target language name (e.g., "Simplified Chinese")
#   {{{{mode_instruction}}}} - Auto-generated instruction based on content type
#
prompt_preamble: |
  You are an expert translator, with mastery in preserving accuracy, fidelity, and nuance.

  We are translating {book_name} by {author}, published by {publisher} in {year_of_publication}. The source text is pre-verified as safe.

  Instructions:
    1. Return the translated text only — no explanations, commentary, or additional notes.
    2. If the source contains HTML tags, preserve them and adapt the translation to fit naturally within those tags.
    3. Ensure that all returned HTML is valid and properly formatted.
    4. Translate faithfully from {{{{source_language}}}} into {{{{target_language}}}} while maintaining the style and tone of the original.

# --- Alternative Prompt Styles (uncomment to replace the above) ---
#
# Academic/Scholarly:
# prompt_preamble: |
#   You are a scholarly translator specializing in academic texts.
#   Translating {book_name} by {author} ({publisher}, {year_of_publication}).
#   Translate {{{{source_language}}}} academic content into {{{{target_language}}}}.
#   {{{{mode_instruction}}}}
#   Preserve technical terminology, citations, and formal tone.
#
# Casual/Popular:
# prompt_preamble: |
#   You are translating {book_name} by {author}, a popular {{{{source_language}}}} book.
#   {{{{mode_instruction}}}}
#   Use natural, conversational {{{{target_language}}}} while maintaining accuracy.
#
# Technical Documentation:
# prompt_preamble: |
#   You are translating technical documentation from {{{{source_language}}}} to {{{{target_language}}}}.
#   Document: {book_name} ({publisher}, {year_of_publication}).
#   {{{{mode_instruction}}}}
#   Preserve all technical terms, code snippets, and command-line examples.
#   Keep variable names, function names, and API references unchanged.

# ============================================================
# Content Filtering (Skip Rules)
# ============================================================

# Note: These rules are IN ADDITION to global defaults.
# Global defaults already skip: cover, copyright, dedication,
# acknowledgment, bibliography, notes, index, glossary, etc.

# Uncomment and customize rules below to skip additional sections:
# skip_rules:
#   # --- Front Matter ---
#   - keyword: preface
#   - keyword: foreword
#   - keyword: introduction
#   - keyword: prologue
#
#   # --- Back Matter ---
#   - keyword: appendix
#   - keyword: epilogue
#   - keyword: afterword
#   - keyword: references
#   - keyword: endnotes
#
#   # --- Other Common Sections ---
#   - keyword: table of contents
#   - keyword: illustrations
#   - keyword: about the publisher
#   - keyword: about the author
#
#   # --- Custom reason example ---
#   - keyword: chapter 5
#     reason: skip this specific chapter

# ============================================================
# Audiobook Settings
# ============================================================

# --- TTS Provider Selection ---
# Choose which text-to-speech service to use
# audiobook_tts_provider: edge   # Options: edge, openai

# Edge TTS (Microsoft) - FREE, NO API KEY NEEDED
# - 57+ voices in multiple languages
# - Good quality, default provider
# - See all voices: edge-tts --list-voices
# audiobook_voice: en-US-GuyNeural      # Clear male voice
# audiobook_voice: en-US-JennyNeural    # Warm female voice
# audiobook_voice: en-US-AriaNeural     # Professional female voice

# OpenAI TTS - PAID (~$15 per 1 million characters)
# - Higher quality, more natural sounding
# - Requires OPENAI_API_KEY environment variable
# To use OpenAI TTS, uncomment these lines:
# audiobook_tts_provider: openai
# audiobook_tts_model: tts-1        # Options: tts-1 (cheaper), tts-1-hd (higher quality)
# audiobook_tts_speed: 1.0          # Speed: 0.25-4.0 (1.0 = normal)
#
# OpenAI voices (uncomment ONE):
# audiobook_voice: alloy            # Neutral, balanced
# audiobook_voice: echo             # Male, authoritative
# audiobook_voice: fable            # British, expressive
# audiobook_voice: onyx             # Deep male, professional
# audiobook_voice: nova             # Female, friendly
# audiobook_voice: shimmer          # Female, warm

# Cost comparison (300-page book ~750,000 characters):
# - Edge TTS: Free
# - OpenAI tts-1: ~$11.25
# - OpenAI tts-1-hd: ~$22.50

# --- Advanced Edge TTS Voice Selection ---
# Full list of US English voices available:
{voices_section}

# Cover image (relative to this directory or absolute path)
# cover_image_path: markdown/images/cover.jpg

# --- Parallel Processing ---
# Uncomment to override global settings
# audiobook_workers: 3          # Number of parallel workers for audiobook generation

# ============================================================
# Audiobook Opening & Closing Statements
# ============================================================
# These statements are spoken at the beginning and end of the audiobook.
# Available placeholders:
#   {{book_name}}     - Book title
#   {{author}}        - Author name
#   {{narrator_name}} - Extracted from voice (e.g., "Guy")

audiobook_opening_statement: |
  This is an audiobook version of {{book_name}}, written by {{author}}. Narrated by {{narrator_name}}. Created by T EPUB.

audiobook_closing_statement: |
  You've been listening to {{book_name}}, written by {{author}}, and narrated by {{narrator_name}}. Thank you for listening.
"""

    config_path.write_text(template, encoding="utf-8")
