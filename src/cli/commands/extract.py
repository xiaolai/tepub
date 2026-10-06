"""Extract command implementation."""

import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import click
from rich.markup import escape

from cli.core import prepare_settings_for_epub
from config import AppSettings, create_book_config_template
from console_singleton import get_console
from debug_tools.extraction_summary import print_extraction_summary
from exceptions import UnsafeArchiveMemberError
from extraction.epub_export import extract_epub_structure, get_epub_metadata_files
from extraction.image_export import extract_images, get_image_mapping
from extraction.pipeline import run_extraction
from state.store import load_segments

console = get_console()

T = TypeVar("T")


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Optional work directory override for this extraction run.",
)
@click.option(
    "--include-back-matter",
    is_flag=True,
    help="Include back-matter continuation pages (index, notes, etc.). "
    "By default, files after back-matter triggers are skipped.",
)
@click.option(
    "--raw", is_flag=True, help="Also unzip the whole book into the workspace (epub_raw/)."
)
@click.option(
    "--markdown",
    is_flag=True,
    help="Also export the book as Markdown, with its images (markdown/).",
)
@click.pass_context
def extract(
    ctx: click.Context,
    input_epub: Path,
    output: Path | None,
    include_back_matter: bool,
    raw: bool,
    markdown: bool,
) -> None:
    """Find the text to translate, and write the book's workspace.

    The workspace holds the units to translate (segments.json), the
    translation state, and the book's config.yaml. --raw and --markdown also
    write an unzipped copy of the book and a Markdown export.
    """
    # The unzip and the Markdown export used to be written on every run,
    # hundreds of files no translation needs; they are opt-in now.
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, output)

    # Apply --include-back-matter flag
    if include_back_matter:
        settings = settings.model_copy(update={"skip_after_back_matter": False})

    run_extraction(settings=settings, input_epub=input_epub)
    console.print(f"[green]Segments written to {escape(str(settings.segments_file))}[/green]")

    # Load extracted metadata and create config.yaml with filled values
    segments_doc = load_segments(settings.segments_file)
    metadata = {
        "title": segments_doc.book_title,
        "author": segments_doc.book_author,
        "publisher": segments_doc.book_publisher,
        "year": segments_doc.book_year,
    }
    create_book_config_template(
        settings.work_dir, input_epub.name, metadata, segments_doc, input_epub
    )

    print_extraction_summary(settings, epub_path=input_epub)
    if raw:
        _write_raw(settings, input_epub)
    if markdown:
        _write_markdown(settings, input_epub)


def _replace_dir(target: Path, build: Callable[[Path], T]) -> T:
    """Build into a fresh sibling of `target`, then swap it in.

    Writing in place kept files the book no longer has, and clearing first
    lost the last good copy when the new one failed.
    """
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        result = build(staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    previous = staging.with_name(f"{staging.name}.old")
    try:
        if target.exists():
            target.rename(previous)
        try:
            staging.rename(target)
        except BaseException:
            # Put the last good copy back rather than leave none at all.
            if previous.exists():
                previous.rename(target)
            raise
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    return result


def _write_raw(settings: AppSettings, input_epub: Path) -> None:
    epub_raw_dir = settings.work_dir / "epub_raw"

    def build(staging: Path) -> tuple[int, dict[str, Path]]:
        structure_mapping = extract_epub_structure(input_epub, staging, preserve_structure=True)
        metadata_files = get_epub_metadata_files(structure_mapping)
        return len(structure_mapping), {
            key: path.relative_to(staging) for key, path in metadata_files.items()
        }

    try:
        count, metadata_files = _replace_dir(epub_raw_dir, build)
    except UnsafeArchiveMemberError:
        # A malicious or malformed archive is not a "warning" case — surface it.
        raise
    except (OSError, zipfile.BadZipFile) as e:
        # --raw asked for the tree; a failure to write it is the command failing.
        raise click.ClickException(f"Could not unzip the book into {epub_raw_dir}: {e}") from e

    cwd = Path.cwd()
    shown = epub_raw_dir.relative_to(cwd) if epub_raw_dir.is_relative_to(cwd) else epub_raw_dir
    console.print(
        f"[green]Extracted complete EPUB structure ({count} files) to "
        f"{escape(str(shown))}[/green]"
    )
    if metadata_files:
        console.print("[cyan]Key EPUB files extracted:[/cyan]")
        for key, rel_path in sorted(metadata_files.items()):
            console.print(f"  {escape(str(key))}: {escape(str(rel_path))}")


def _write_markdown(settings: AppSettings, input_epub: Path) -> None:
    # Imported lazily so that commands other than `extract` do not pay the
    # html2text import cost at CLI startup. html2text is a declared dependency,
    # so a failure here means a broken install rather than a missing extra.
    try:
        from extraction.markdown_export import export_markdown
    except ImportError as exc:
        console.print(f"[red]Markdown export unavailable: {escape(str(exc))}[/red]")
        console.print(
            "[yellow]Reinstall tepub to repair the environment: pip install -e .[/yellow]"
        )
        raise SystemExit(1) from exc

    markdown_dir = settings.work_dir / "markdown"

    def build(staging: Path):
        # Images go to markdown/images; the Markdown refers to them by file name.
        extracted_images = extract_images(settings, input_epub, staging / "images")
        image_mapping = get_image_mapping(extracted_images)
        created_files, combined_file = export_markdown(
            settings, input_epub, staging, image_mapping
        )
        return extracted_images, created_files, combined_file

    try:
        extracted_images, created_files, combined_file = _replace_dir(markdown_dir, build)
    except OSError as e:
        # As for --raw: asked for, so a failure to write it fails the command.
        raise click.ClickException(
            f"Could not write the Markdown export to {markdown_dir}: {e}"
        ) from e

    if extracted_images:
        images_dir = markdown_dir / "images"
        console.print(
            f"[green]Extracted {len(extracted_images)} images to "
            f"{escape(str(images_dir))}[/green]"
        )

        # Report cover candidates
        cover_candidates = [img for img in extracted_images if img.is_cover_candidate]
        if cover_candidates:
            console.print("[cyan]Potential cover candidates:[/cyan]")
            for img in cover_candidates[:3]:  # Show top 3
                console.print(f"  - {escape(img.extracted_path.name)}")

    console.print(
        f"[green]Exported {len(created_files)} markdown files to "
        f"{escape(str(markdown_dir))}[/green]"
    )
    console.print(f"[green]Created combined markdown: {escape(combined_file.name)}[/green]")
