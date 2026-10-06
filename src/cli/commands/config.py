"""Config command implementation."""

from __future__ import annotations

import shutil
from pathlib import Path

import click
from pydantic import ValidationError
from rich.markup import escape
from rich.panel import Panel
from rich.tree import Tree

from config import AppSettings
from config.loader import ConfigFileError, _parse_yaml_file
from config.provenance import shown_settings
from config.workspace import _with_book_config, build_workspace_name, derive_book_workspace
from console_singleton import get_console

console = get_console()


@click.group()
def config() -> None:
    """Configuration management commands."""
    pass


def _book_settings(ctx: click.Context, input_epub: Path) -> AppSettings:
    """The settings pointed at the book's workspace, before its config.yaml applies.

    prepare_settings_for_epub creates the workspace and applies the book's own
    config, so inspecting a config wrote to disk, failed when the config being
    inspected was itself invalid, and lost the config's path when that config
    moved work_dir. This mirrors its workspace choice without either effect.
    """
    settings: AppSettings = ctx.obj["settings"]
    override = ctx.obj.get("work_dir_override_path")
    if override is None:
        work_dir = derive_book_workspace(settings, input_epub)
        return settings.model_copy(update={"work_root": work_dir.parent, "work_dir": work_dir})
    base = Path(override).expanduser()
    if not base.is_absolute():
        base = Path.cwd() / base
    if (base / "segments.json").exists() or (base / "state.json").exists():
        return settings.model_copy(update={"work_root": base.parent, "work_dir": base})
    work_dir = base / build_workspace_name(input_epub)
    return settings.model_copy(update={"work_root": base, "work_dir": work_dir})


def _config_target(
    ctx: click.Context, input_epub: Path | None, use_global: bool, config_file_path: Path | None
) -> tuple[Path, str]:
    """The config file a validate or reset acts on, and its kind."""
    if config_file_path:
        return config_file_path, "Custom"
    if use_global or input_epub is None:
        return _get_global_config_path(), "Global"
    return _book_settings(ctx, input_epub).work_dir / "config.yaml", "Per-book"


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
def validate(
    ctx: click.Context, input_epub: Path | None, use_global: bool, config_file_path: Path | None
) -> None:
    """Validate configuration file syntax and values.

    Examples:
      tepub config validate                    # Validate global config
      tepub config validate --global           # Validate global config (explicit)
      tepub config validate book.epub          # Validate per-book config
      tepub config validate --file path.yaml   # Validate specific file
    """
    config_path, config_type = _config_target(ctx, input_epub, use_global, config_file_path)
    # A per-book config is checked as it applies: over the settings in effect.
    # Any other file is checked on its own; deciding this from the book alone
    # made `--file X book.epub` reach for settings it had never loaded.
    base: AppSettings | None = ctx.obj["settings"] if config_type == "Per-book" else None

    if not config_path.exists():
        console.print(Panel(
            f"[yellow]Config file not found:[/yellow]\n{escape(str(config_path))}",
            title="⚠ Configuration Not Found",
            border_style="yellow",
        ))
        if input_epub is not None and base is not None:
            console.print(
                f"\n[dim]Hint: Run [bold]tepub extract {escape(input_epub.name)}[/bold] "
                "to create the config file.[/dim]"
            )
        # click.Abort is reported as an interrupt ("Progress is saved", exit 130).
        raise SystemExit(1)

    console.print()
    console.print(Panel(
        f"[bold]{config_type} Configuration Validation[/bold]\n"
        f"[dim]File: {escape(str(config_path))}[/dim]",
        border_style="cyan",
    ))
    console.print()

    yaml_data = _read_config_file(config_path)
    _warn_unknown_keys(yaml_data)
    errors_by_field = _settings_errors(yaml_data, base)
    for message in _voice_errors(yaml_data):
        errors_by_field.setdefault("audiobook_voice", []).append(message)
    valid_count, invalid_count = _print_field_tree(yaml_data, errors_by_field)
    success = not errors_by_field
    _print_summary(valid_count, invalid_count, success)

    if not success:
        raise SystemExit(1)


def _fail(title: str, message: str) -> None:
    console.print(Panel(message, title=title, border_style="red"))


def _read_config_file(config_path: Path) -> dict:
    """The file's settings; exits when it is not YAML or not a mapping."""
    try:
        return _parse_yaml_file(config_path)
    except ConfigFileError as e:
        # Every check reads the file as a mapping of setting names; a list or a
        # bare value crashed them, and `false` or `0` passed as no settings.
        _fail("✗ Validation Failed", f"[red]{escape(str(e))}[/red]")
        raise SystemExit(1) from e
    except Exception as e:
        _fail("✗ Validation Failed", f"[red]YAML Syntax Error:[/red]\n{escape(str(e))}")
        raise SystemExit(1) from e


def _warn_unknown_keys(yaml_data: dict) -> None:
    """Report keys Pydantic will silently ignore.

    AppSettings does not set extra="forbid" — and making it do so would
    hard-fail existing configs on upgrade — so a misspelled key
    ("target_lanugage") would otherwise validate cleanly while having no effect.
    """
    unknown_keys = sorted(set(yaml_data) - set(AppSettings.model_fields))
    if unknown_keys:
        console.print(Panel(
            "[yellow]These keys are not recognised and will be ignored:[/yellow]\n"
            + "\n".join(f"  • {escape(str(key))}" for key in unknown_keys)
            + "\n\n[dim]Check for typos — an ignored key has no effect.[/dim]",
            title="⚠ Unknown settings",
            border_style="yellow",
        ))


def _settings_errors(yaml_data: dict, base: AppSettings | None) -> dict[str, list[str]]:
    """Each top-level setting's validation errors; the file is checked over
    `base` when given, on its own otherwise.

    Errors are kept under their top-level setting: a nested one
    (primary_provider.max_tokens) used to match no displayed field, so the field
    showed a green check and its error was never printed.
    """
    errors_by_field: dict[str, list[str]] = {}
    try:
        if base is None:
            # Checked as standalone settings; a work_dir satisfies required fields.
            AppSettings(**{"work_dir": "~/.tepub", **yaml_data})
        else:
            # model_copy(update=...) used to assign without running validators,
            # so this command reported success for invalid values. Re-validate
            # the merged mapping instead.
            AppSettings.model_validate({**base.model_dump(), **yaml_data})
    except ValidationError as e:
        for error in e.errors():
            loc = error["loc"]
            field = str(loc[0]) if loc else "(file)"
            where = ".".join(str(part) for part in loc[1:])
            errors_by_field.setdefault(field, []).append(
                f"{where}: {error['msg']}" if where else error["msg"]
            )
    except Exception as e:
        _fail("✗ Validation Failed", f"[red]Validation Error:[/red]\n{escape(str(e))}")
        raise SystemExit(1) from e
    return errors_by_field


def _voice_errors(yaml_data: dict) -> list[str]:
    """Whether audiobook_voice names a voice of its provider.

    When the voices cannot be listed, that is a warning, not a failure.
    """
    voice_value = yaml_data.get("audiobook_voice")
    if not voice_value:
        return []
    provider = yaml_data.get("audiobook_tts_provider", "edge")
    try:
        from audiobook.voices import list_voices_for_provider

        valid_names = [v["ShortName"] for v in list_voices_for_provider(provider)]
    except Exception as voice_err:
        console.print(
            f"[yellow]Warning: Could not validate voice: {escape(str(voice_err))}[/yellow]"
        )
        return []
    if voice_value in valid_names:
        return []
    return [
        f"Invalid voice '{voice_value}' for provider '{provider}'. "
        f"Valid voices: {', '.join(valid_names)}"
    ]


# The fields validate lists, by category.
_CATEGORIES = {
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
    "Provider Settings": ["primary_provider", "providers"],
    "Skip Rules": ["skip_rules", "skip_after_back_matter"],
    "Directories": ["work_root", "work_dir"],
}


def _print_field_tree(yaml_data: dict, errors_by_field: dict[str, list[str]]) -> tuple[int, int]:
    """Print each field the file sets or that has errors; (valid, invalid) counts."""
    tree = Tree("📝 Configuration Fields", guide_style="dim")
    # Errors for settings no category lists, and errors about the file as a
    # whole, would otherwise not be shown at all.
    listed = {name for names in _CATEGORIES.values() for name in names}
    categories = {
        **_CATEGORIES,
        "Other": [name for name in errors_by_field if name not in listed],
    }

    valid_count = 0
    invalid_count = 0
    for category, field_names in categories.items():
        category_fields = [
            name for name in field_names if name in yaml_data or name in errors_by_field
        ]
        if not category_fields:
            continue

        category_branch = tree.add(f"[bold cyan]{category}[/bold cyan]")
        for field_name in category_fields:
            shown_value = (
                _format_value(yaml_data[field_name]) if field_name in yaml_data else "[dim]—[/dim]"
            )
            label = escape(field_name)
            if field_name in errors_by_field:
                invalid_count += 1
                field_branch = category_branch.add(f"[red]✗ {label}[/red]: {shown_value}")
                for error_msg in errors_by_field[field_name]:
                    field_branch.add(f"[red]└─ Error: {escape(error_msg)}[/red]")
            else:
                valid_count += 1
                category_branch.add(f"[green]✓ {label}[/green]: {shown_value}")

    console.print(tree)
    console.print()
    return valid_count, invalid_count


def _print_summary(valid_count: int, invalid_count: int, success: bool) -> None:
    color, verdict = ("green", "PASSED ✓") if success else ("red", "FAILED ✗")
    console.print(Panel(
        f"[bold]Total fields:[/bold] {valid_count + invalid_count}\n"
        f"[green]Valid:[/green] {valid_count} ✓\n"
        f"[red]Invalid:[/red] {invalid_count} ✗\n\n"
        f"[bold {color}]Status: {verdict}[/bold {color}]",
        title="Validation Summary",
        border_style=color,
    ))
    console.print()


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
    config_path, config_type = _config_target(ctx, input_epub, use_global, config_file_path)

    if not config_path.exists():
        console.print(Panel(
            f"[yellow]Config file does not exist:[/yellow]\n{escape(str(config_path))}",
            title="⚠ File Not Found",
            border_style="yellow",
        ))
        raise SystemExit(1)

    if not force and not _confirm_reset(config_path, config_type):
        console.print("[cyan]Reset cancelled.[/cyan]")
        raise SystemExit(1)

    if backup:
        backup_path = config_path.parent / f"{config_path.name}.bak"
        shutil.copy2(config_path, backup_path)
        console.print(f"[green]✓ Backup created: {escape(str(backup_path))}[/green]")

    if config_type == "Per-book" and input_epub is not None:
        # The book's own config is not applied: it is what is being replaced,
        # and may be the broken thing that prompted the reset.
        _regenerate_book_config(_book_settings(ctx, input_epub), input_epub, config_path)
    else:
        _write_global_config_template(config_path)

    console.print()
    console.print(Panel(
        f"[green]Configuration reset successfully![/green]\n"
        f"[dim]{escape(str(config_path))}[/dim]",
        title="✓ Reset Complete",
        border_style="green",
    ))


def _confirm_reset(config_path: Path, config_type: str) -> bool:
    console.print()
    console.print(Panel(
        f"[yellow]This will reset {config_type.lower()} config to default template:[/yellow]\n"
        f"[bold]{escape(str(config_path))}[/bold]\n\n"
        f"[red]All current settings will be lost![/red]",
        title="⚠ Confirmation Required",
        border_style="yellow",
    ))
    console.print()
    return click.confirm("Continue?", default=False)


def _regenerate_book_config(settings: AppSettings, input_epub: Path, config_path: Path) -> None:
    """Write the book's config afresh from its extracted segments.

    The old file used to be unlinked first, so any failure during generation
    left the user with no config at all — including local edits they had made.
    It is kept aside until the new file is in place. The rollback copy is not
    the .bak: that name deleted the backup --backup had just made.
    """
    from config.templates import create_book_config_template
    from state.store import load_segments

    if not settings.segments_file.exists():
        console.print(
            "[red]Error: segments.json not found. Run "
            f"[bold]tepub extract {escape(input_epub.name)}[/bold] first.[/red]"
        )
        raise SystemExit(1)

    segments_doc = load_segments(settings.segments_file)
    metadata = {
        "title": segments_doc.book_title,
        "author": segments_doc.book_author,
        "publisher": segments_doc.book_publisher,
        "year": segments_doc.book_year,
    }

    rollback_path = config_path.with_name(config_path.name + ".reset-rollback")
    config_path.replace(rollback_path)
    try:
        create_book_config_template(
            settings.work_dir, input_epub.name, metadata, segments_doc, input_epub
        )
    except Exception:
        # Restore the previous config rather than leaving the book unconfigured.
        rollback_path.replace(config_path)
        raise
    rollback_path.unlink()


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
  name: ollama                     # ollama (local, free), openai, anthropic, gemini, grok, deepl
  model: translategemma:12b        # Install with: ollama pull translategemma:12b
  # base_url: http://localhost:11434   # Ollama on another machine: its address

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
            return f"[yellow]\"{escape(value[:47])}...\"[/yellow]"
        return f"[yellow]\"{escape(value)}\"[/yellow]"
    elif isinstance(value, dict):
        return f"[magenta]{{...}}[/magenta] [dim]({len(value)} keys)[/dim]"
    elif isinstance(value, list):
        return f"[magenta][...]  [/magenta][dim]({len(value)} items)[/dim]"
    else:
        return f"[dim]{type(value).__name__}[/dim]"


@config.command("show")
@click.argument(
    "input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=False
)
@click.pass_context
def show(ctx: click.Context, input_epub: Path | None) -> None:
    """Show the main settings in effect, and the file each one came from.

    Files are read in this order, each overriding the ones before:
    ~/.tepub/config.yaml, .env, ./config.yaml, --config FILE; then
    OLLAMA_BASE_URL, and the book's own config.yaml when a book is given.
    API keys are never printed.
    """
    import os

    settings: AppSettings = ctx.obj["settings"]
    if input_epub is not None:
        settings = _settings_with_book(ctx, input_epub)

    console.print("[bold]Files[/bold] (later ones override earlier ones)")
    for source in settings.config_layers:
        found = "[green]found[/green]" if source.found else "[dim]absent[/dim]"
        console.print(f"  {source.label:14} {escape(str(source.path))}  {found}")

    console.print("\n[bold]Settings[/bold] (the main ones; tepub config validate lists a file's)")
    for setting in shown_settings(settings, os.getenv("OLLAMA_BASE_URL")):
        shown = setting.value
        if setting.name == "prompt_preamble" and shown is not None:
            shown = (str(shown).splitlines() or [""])[0][:60] + " …"
        # Values are the user's text: printed as markup, "[/x]" in a prompt
        # stopped the command with a MarkupError.
        console.print(
            f"  {setting.name:28} {escape(f'{shown!s:40}')} [dim]{setting.origin}[/dim]"
        )

    console.print("\n[bold]API keys[/bold]")
    for variable in _SECRET_ENV:
        console.print(f"  {variable:28} {'set' if os.getenv(variable) else '[dim]not set[/dim]'}")
    configured_key = "set" if settings.primary_provider.api_key else "[dim]not set[/dim]"
    console.print(f"  {'primary_provider.api_key':28} {configured_key}")


# Never printed: only whether they are set.
_SECRET_ENV = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROK_API_KEY", "DEEPL_API_KEY"
)


def _settings_with_book(ctx: click.Context, input_epub: Path) -> AppSettings:
    """The settings with the book's config applied, which records that config."""
    book_base = _book_settings(ctx, input_epub)
    book_config = book_base.work_dir / "config.yaml"
    try:
        return _with_book_config(book_base)
    except ValidationError as exc:
        console.print(f"[red]The book's config {escape(str(book_config))} is invalid:[/red]")
        console.print(escape(str(exc)), markup=False)
        console.print("[dim]tepub config validate BOOK shows each problem.[/dim]")
        raise SystemExit(1) from exc
