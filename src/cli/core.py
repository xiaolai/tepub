"""Shared CLI utilities and common operations."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import click

from config import AppSettings, load_settings_from_cli
from logging_utils.logger import configure_logging
from state.store import load_segments, load_state


def prepare_initial_settings(
    config_file: str | None, work_dir: Path | None, verbose: bool
) -> AppSettings:
    """Initialize settings from CLI arguments.

    Args:
        config_file: Optional path to config file
        work_dir: Optional work directory override
        verbose: Enable verbose logging

    Returns:
        Configured AppSettings instance
    """
    configure_logging()
    if verbose:
        configure_logging(level=logging.DEBUG)
    settings = load_settings_from_cli(config_file)
    if work_dir:
        settings = settings.model_copy(update={"work_dir": work_dir})
    # Note: ensure_directories() is called later in prepare_settings_for_epub()
    # after the workspace is properly configured
    return settings


def prepare_settings_for_epub(
    ctx: click.Context, settings: AppSettings, input_epub: Path, override: Path | None
) -> AppSettings:
    """Prepare settings for a specific EPUB file.

    Args:
        ctx: Click context
        settings: Base settings
        input_epub: Path to EPUB file
        override: Optional work directory override

    Returns:
        Settings configured for the specific EPUB
    """
    base_override = override or ctx.obj.get("work_dir_override_path")

    if base_override:
        settings = settings.with_override_root(base_override, input_epub)
        ctx.obj["work_dir_overridden"] = True
        ctx.obj["work_dir_override_path"] = base_override
    elif not ctx.obj.get("work_dir_overridden", False):
        settings = settings.with_book_workspace(input_epub)

    settings.ensure_directories()
    ctx.obj["settings"] = settings
    return settings


def resolve_export_flags(epub_flag: bool, web_flag: bool) -> tuple[bool, bool]:
    """Determine which exports to run based on user flags.

    Args:
        epub_flag: User requested EPUB export
        web_flag: User requested web export

    Returns:
        Tuple of (export_epub, export_web) booleans
    """
    if not epub_flag and not web_flag:
        # Default: export both
        return True, True
    return epub_flag, web_flag


def derive_epub_paths(
    input_epub: Path, requested: Path | None, work_dir: Path
) -> tuple[Path, Path]:
    """Derive bilingual and translated EPUB output paths.

    Args:
        input_epub: Source EPUB path
        requested: User-requested output path (optional)
        work_dir: Workspace directory

    Returns:
        Tuple of (bilingual_path, translated_path)
    """
    if requested:
        bilingual = requested
    else:
        # Export to workspace directory, not alongside EPUB
        bilingual = work_dir / f"{input_epub.stem}_bilingual{input_epub.suffix}"

    stem = bilingual.stem
    if stem.endswith("_bilingual"):
        base_stem = stem[: -len("_bilingual")]
    else:
        base_stem = stem

    translated = bilingual.with_name(f"{base_stem}_translated{bilingual.suffix}")
    return bilingual, translated


def create_web_archive(web_dir: Path) -> Path:
    """Create ZIP archive of web export.

    Args:
        web_dir: Directory containing web export

    Returns:
        Path to created ZIP archive
    """
    base_name = web_dir.parent / web_dir.name
    archive = shutil.make_archive(
        str(base_name), "zip", root_dir=web_dir.parent, base_dir=web_dir.name
    )
    return Path(archive)


def check_pipeline_artifacts(settings: AppSettings, input_epub: Path) -> bool:
    """Check if valid pipeline artifacts exist for resuming.

    Args:
        settings: Application settings
        input_epub: EPUB file path

    Returns:
        True if valid artifacts exist, False otherwise
    """
    return describe_pipeline_artifacts(settings, input_epub)[0]


def describe_pipeline_artifacts(settings: AppSettings, input_epub: Path) -> tuple[bool, str]:
    """Report whether existing artifacts are reusable, and why not when they aren't.

    The boolean-only check collapsed missing, unreadable, wrong-EPUB and
    incomplete artifacts into a single False, so the pipeline silently re-ran a
    full extraction with no indication of which condition applied.
    """
    segments_path = settings.segments_file
    state_path = settings.state_file

    if not segments_path.exists():
        return False, f"no segments file at {segments_path}"
    if not state_path.exists():
        return False, f"no translation state at {state_path}"

    try:
        segments_doc = load_segments(segments_path)
        state_doc = load_state(state_path)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # A bare `except Exception` also swallowed programming defects and
        # permission errors, silently triggering a full re-extraction instead of
        # surfacing the real problem.
        return False, f"artifacts could not be read ({exc})"

    # Validate EPUB path matches
    try:
        saved_epub_path = Path(segments_doc.epub_path)
    except TypeError:
        saved_epub_path = Path(str(segments_doc.epub_path))

    try:
        mismatched = saved_epub_path.resolve() != input_epub.resolve()
    except OSError:
        mismatched = saved_epub_path != input_epub
    if mismatched:
        return False, f"artifacts belong to a different EPUB ({saved_epub_path})"

    # Validate segments match state.
    # Requiring *every* extracted segment id to appear in state was too strict:
    # translation filtering and skip rules legitimately leave some segments out,
    # so valid workspaces were reported invalid and re-extracted. A shared subset
    # is enough to prove the two artifacts came from the same extraction, while
    # no overlap at all still catches genuinely mismatched files.
    segment_ids = {segment.segment_id for segment in segments_doc.segments}
    if not segment_ids:
        return False, "segments file contains no segments"

    if not segment_ids & state_doc.segments.keys():
        return False, "segments and translation state share no segment ids"

    return True, "reusable"
