"""Config command implementation."""

from __future__ import annotations

import shutil
from pathlib import Path

import click
from pydantic import ValidationError
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from cli.core import prepare_settings_for_epub
from config import AppSettings
from config.loader import _parse_yaml_file
from console_singleton import get_console

console = get_console()


@click.group()
def config() -> None:
    """Configuration management commands."""
    pass


@config.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path), required=False)
@click.option(
    "--global",
    "use_global",
    is_flag=True,
    help="Validate global config instead of per-book config.",
)
@click.option(
    "--file",
    "config_file_path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to specific config file to validate.",
)
@click.pass_context
def validate(ctx: click.Context, input_epub: Path | None, use_global: bool, config_file_path: Path | None) -> None:
    """Validate configuration file syntax and values.

    Examples:
      tepub config validate                    # Validate global config
      tepub config validate --global           # Validate global config (explicit)
      tepub config validate book.epub          # Validate per-book config
      tepub config validate --file path.yaml   # Validate specific file
    """
    # Determine which config to validate
    if config_file_path:
        config_path = config_file_path
        config_type = "Custom"
    elif use_global or input_epub is None:
        config_path = _get_global_config_path()
        config_type = "Global"
    else:
        # Get per-book config path
        # We need settings, but it might not be available if global config is broken
        # So we'll construct the path manually
        settings: AppSettings = ctx.obj.get("settings")
        if settings:
            temp_settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
            config_path = temp_settings.work_dir / "config.yaml"
        else:
            # Fallback: construct path manually
            config_path = input_epub.parent / input_epub.stem / "config.yaml"
        config_type = "Per-book"

    # Check if config exists
    if not config_path.exists():
        console.print(Panel(
            f"[yellow]Config file not found:[/yellow]\n{config_path}",
            title="⚠ Configuration Not Found",
            border_style="yellow",
        ))
        if not use_global and input_epub:
            console.print(f"\n[dim]Hint: Run [bold]tepub extract {input_epub.name}[/bold] to create the config file.[/dim]")
        raise click.Abort()

    # Display header
    console.print()
    console.print(Panel(
        f"[bold]{config_type} Configuration Validation[/bold]\n"
        f"[dim]File: {config_path}[/dim]",
        border_style="cyan",
    ))
    console.print()

    # Parse YAML
    try:
        yaml_data = _parse_yaml_file(config_path)
    except Exception as e:
        console.print(Panel(
            f"[red]YAML Syntax Error:[/red]\n{str(e)}",
            title="✗ Validation Failed",
            border_style="red",
        ))
        raise click.Abort()

    if not yaml_data:
        yaml_data = {}

    # Validate with Pydantic
    validation_results = []
    errors_by_field = {}

    try:
        # Try to create AppSettings from the YAML data
        if use_global or input_epub is None:
            # For global config, validate as standalone settings
            # Use a temporary work_dir to satisfy required fields
            temp_data = yaml_data.copy()
            if "work_dir" not in temp_data:
                temp_data["work_dir"] = "~/.tepub"
            validated_settings = AppSettings(**temp_data)
        else:
            # For per-book config, overlay on existing settings
            validated_settings = settings.model_copy(update=yaml_data)

        # If we get here, validation passed
        success = True
    except ValidationError as e:
        success = False
        # Parse validation errors
        for error in e.errors():
            field_path = ".".join(str(loc) for loc in error["loc"])
            errors_by_field[field_path] = error["msg"]
    except Exception as e:
        console.print(Panel(
            f"[red]Validation Error:[/red]\n{str(e)}",
            title="✗ Validation Failed",
            border_style="red",
        ))
        raise click.Abort()

    # Additional validation: Check voice spelling
    if "audiobook_voice" in yaml_data and yaml_data["audiobook_voice"]:
        provider = yaml_data.get("audiobook_tts_provider", "edge")
        voice_value = yaml_data["audiobook_voice"]

        try:
            from audiobook.voices import list_voices_for_provider

            voices = list_voices_for_provider(provider)
            valid_names = [v["ShortName"] for v in voices]

            if voice_value not in valid_names:
                errors_by_field["audiobook_voice"] = (
                    f"Invalid voice '{voice_value}' for provider '{provider}'. "
                    f"Valid voices: {', '.join(valid_names)}"
                )
                success = False
        except Exception as voice_err:
            # If voice listing fails, add warning but don't fail validation
            console.print(f"[yellow]Warning: Could not validate voice: {voice_err}[/yellow]")

    # Build validation results tree
    tree = Tree("📝 Configuration Fields", guide_style="dim")

    # Group fields by category
    categories = {
        "Translation Settings": [
            "source_language", "target_language", "translation_workers",
            "prompt_preamble", "output_mode", "translation_files"
        ],
        "Audiobook Settings": [
            "audiobook_tts_provider", "audiobook_tts_model", "audiobook_tts_speed",
            "audiobook_voice", "audiobook_workers", "audiobook_files",
            "audiobook_opening_statement", "audiobook_closing_statement",
            "cover_image_path"
        ],
        "Provider Settings": [
            "primary_provider", "fallback_provider", "providers"
        ],
        "Skip Rules": [
            "skip_rules", "skip_after_back_matter"
        ],
        "Directories": [
            "work_root", "work_dir"
        ],
    }

    valid_count = 0
    invalid_count = 0

    for category, field_names in categories.items():
        # Check if any fields in this category are present in yaml_data
        category_fields = []
        for field_name in field_names:
            if field_name in yaml_data:
                category_fields.append(field_name)

        if not category_fields:
            continue  # Skip empty categories

        category_branch = tree.add(f"[bold cyan]{category}[/bold cyan]")

        for field_name in category_fields:
            field_value = yaml_data[field_name]

            # Check if this field has errors
            if field_name in errors_by_field:
                invalid_count += 1
                error_msg = errors_by_field[field_name]
                field_branch = category_branch.add(
                    f"[red]✗ {field_name}[/red]: {_format_value(field_value)}"
                )
                field_branch.add(f"[red]└─ Error: {error_msg}[/red]")
            else:
                valid_count += 1
                category_branch.add(
                    f"[green]✓ {field_name}[/green]: {_format_value(field_value)}"
                )

    # Show the tree
    console.print(tree)
    console.print()

    # Show summary
    total_fields = valid_count + invalid_count

    if success:
        summary_panel = Panel(
            f"[bold]Total fields:[/bold] {total_fields}\n"
            f"[green]Valid:[/green] {valid_count} ✓\n"
            f"[red]Invalid:[/red] {invalid_count} ✗\n\n"
            f"[bold green]Status: PASSED ✓[/bold green]",
            title="Validation Summary",
            border_style="green",
        )
    else:
        summary_panel = Panel(
            f"[bold]Total fields:[/bold] {total_fields}\n"
            f"[green]Valid:[/green] {valid_count} ✓\n"
            f"[red]Invalid:[/red] {invalid_count} ✗\n\n"
            f"[bold red]Status: FAILED ✗[/bold red]",
            title="Validation Summary",
            border_style="red",
        )

    console.print(summary_panel)
    console.print()

    if not success:
        raise click.Abort()


@config.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path), required=False)
@click.option(
    "--global",
    "use_global",
    is_flag=True,
    help="Reset global config.",
)
@click.option(
    "--file",
    "config_file_path",
    type=click.Path(path_type=Path),
    help="Path to specific config file to reset.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "--backup",
    is_flag=True,
    help="Create .bak backup before resetting.",
)
@click.pass_context
def reset(
    ctx: click.Context,
    input_epub: Path | None,
    use_global: bool,
    config_file_path: Path | None,
    force: bool,
    backup: bool,
) -> None:
    """Reset configuration file to default template.

    Examples:
      tepub config reset --global              # Reset global config
      tepub config reset book.epub             # Reset per-book config
      tepub config reset --file config.yaml    # Reset specific file
    """
    # Determine target config
    if config_file_path:
        config_path = config_file_path
        config_type = "Custom"
        is_per_book = False
    elif use_global or input_epub is None:
        config_path = _get_global_config_path()
        config_type = "Global"
        is_per_book = False
    else:
        # Per-book config
        settings: AppSettings = ctx.obj.get("settings")
        if settings:
            temp_settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
            config_path = temp_settings.work_dir / "config.yaml"
        else:
            # Fallback
            config_path = input_epub.parent / input_epub.stem / "config.yaml"
        config_type = "Per-book"
        is_per_book = True

    # Check if file exists
    if not config_path.exists():
        console.print(Panel(
            f"[yellow]Config file does not exist:[/yellow]\n{config_path}",
            title="⚠ File Not Found",
            border_style="yellow",
        ))
        raise click.Abort()

    # Confirmation
    if not force:
        console.print()
        console.print(Panel(
            f"[yellow]This will reset {config_type.lower()} config to default template:[/yellow]\n"
            f"[bold]{config_path}[/bold]\n\n"
            f"[red]All current settings will be lost![/red]",
            title="⚠ Confirmation Required",
            border_style="yellow",
        ))
        console.print()
        if not click.confirm("Continue?", default=False):
            console.print("[cyan]Reset cancelled.[/cyan]")
            raise click.Abort()

    # Backup
    if backup:
        backup_path = config_path.parent / f"{config_path.name}.bak"
        shutil.copy2(config_path, backup_path)
        console.print(f"[green]✓ Backup created: {backup_path}[/green]")

    # Reset based on type
    if is_per_book and input_epub and input_epub.exists():
        # Per-book config - regenerate from extraction
        from config.templates import create_book_config_template
        from state.store import load_segments

        # Get settings for this book
        settings = ctx.obj.get("settings")
        if settings:
            temp_settings = prepare_settings_for_epub(ctx, settings, input_epub, override=None)
        else:
            # Minimal settings
            from config import AppSettings
            temp_settings = AppSettings(work_dir=config_path.parent)

        # Check if segments exist
        if not temp_settings.segments_file.exists():
            console.print(
                f"[red]Error: segments.json not found. Run [bold]tepub extract {input_epub.name}[/bold] first.[/red]"
            )
            raise click.Abort()

        # Load metadata from segments
        segments_doc = load_segments(temp_settings.segments_file)
        metadata = {
            "title": segments_doc.book_title,
            "author": segments_doc.book_author,
            "publisher": segments_doc.book_publisher,
            "year": segments_doc.book_year,
        }

        # Recreate config
        config_path.unlink()  # Remove old
        create_book_config_template(
            temp_settings.work_dir,
            input_epub.name,
            metadata,
            segments_doc,
            input_epub
        )
    else:
        # Global config - write default template
        _write_global_config_template(config_path)

    console.print()
    console.print(Panel(
        f"[green]Configuration reset successfully![/green]\n"
        f"[dim]{config_path}[/dim]",
        title="✓ Reset Complete",
        border_style="green",
    ))


def _write_global_config_template(config_path: Path) -> None:
    """Write default global config template."""
    template = """# Global TEPUB Configuration
# Location: ~/.tepub/config.yaml
# This file sets default settings for all books.
# Per-book configs (created by 'tepub extract') override these settings.

# ============================================================
# Translation Settings
# ============================================================

# Source and target languages
source_language: auto              # auto-detect or specify (e.g., English, Japanese)
target_language: Simplified Chinese

# Parallel processing
translation_workers: 3             # Number of parallel translation workers

# ============================================================
# Translation Provider
# ============================================================

# Primary translation provider
primary_provider:
  name: openai                     # openai, anthropic, gemini, grok, deepl, ollama
  model: gpt-4o                    # Model name for the provider

# Optional fallback provider (if primary fails)
# fallback_provider:
#   name: anthropic
#   model: claude-3-5-sonnet-20241022

# ============================================================
# Audiobook Settings
# ============================================================

# TTS provider
audiobook_tts_provider: edge       # edge (free) or openai (paid)

# Parallel processing
audiobook_workers: 3               # Number of parallel audiobook workers

# ============================================================
# Content Filtering (Skip Rules)
# ============================================================

# Files matching these keywords will be skipped during extraction
# skip_rules:
#   - keyword: cover
#   - keyword: copyright
#   - keyword: dedication
#   - keyword: acknowledgment
"""
    config_path.write_text(template, encoding="utf-8")


def _get_global_config_path() -> Path:
    """Get path to global config file."""
    return Path.home() / ".tepub" / "config.yaml"


def _format_value(value) -> str:
    """Format a config value for display."""
    if value is None:
        return "[dim]null[/dim]"
    elif isinstance(value, bool):
        return f"[bold]{value}[/bold]"
    elif isinstance(value, (int, float)):
        return f"[cyan]{value}[/cyan]"
    elif isinstance(value, str):
        if len(value) > 50:
            return f"[yellow]\"{value[:47]}...\"[/yellow]"
        return f"[yellow]\"{value}\"[/yellow]"
    elif isinstance(value, dict):
        return f"[magenta]{{...}}[/magenta] [dim]({len(value)} keys)[/dim]"
    elif isinstance(value, list):
        return f"[magenta][...]  [/magenta][dim]({len(value)} items)[/dim]"
    else:
        return f"[dim]{type(value).__name__}[/dim]"
