"""Extract command implementation."""

from pathlib import Path

import click

from cli.core import prepare_settings_for_epub
from config import AppSettings, create_book_config_template
from console_singleton import get_console
from debug_tools.extraction_summary import print_extraction_summary
from extraction.epub_export import extract_epub_structure, get_epub_metadata_files
from extraction.image_export import extract_images, get_image_mapping
from extraction.markdown_export import export_combined_markdown, export_to_markdown
from extraction.pipeline import run_extraction
from state.store import load_segments

console = get_console()


@click.command()
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Optional work directory override for this extraction run.",
)
@click.option(
    "--include-back-matter",
    is_flag=True,
    help="Include back-matter continuation pages (index, notes, etc.). By default, files after back-matter triggers are skipped.",
)
@click.pass_context
def extract(
    ctx: click.Context, input_epub: Path, output: Path | None, include_back_matter: bool
) -> None:
    """Extract segments from the EPUB file."""
    settings: AppSettings = ctx.obj["settings"]
    settings = prepare_settings_for_epub(ctx, settings, input_epub, output)

    # Apply --include-back-matter flag
    if include_back_matter:
        settings = settings.model_copy(update={"skip_after_back_matter": False})

    run_extraction(settings=settings, input_epub=input_epub)
    console.print(f"[green]Segments written to {settings.segments_file}[/green]")

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

    # Extract complete EPUB structure
    epub_raw_dir = settings.work_dir / "epub_raw"
    try:
        structure_mapping = extract_epub_structure(
            input_epub, epub_raw_dir, preserve_structure=True
        )
        console.print(
            f"[green]Extracted complete EPUB structure ({len(structure_mapping)} files) to {epub_raw_dir.relative_to(Path.cwd()) if epub_raw_dir.is_relative_to(Path.cwd()) else epub_raw_dir}[/green]"
        )

        # Show key metadata files
        metadata_files = get_epub_metadata_files(structure_mapping)
        if metadata_files:
            console.print("[cyan]Key EPUB files extracted:[/cyan]")
            for key, path in sorted(metadata_files.items()):
                rel_path = path.relative_to(epub_raw_dir)
                console.print(f"  {key}: {rel_path}")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not extract EPUB structure: {e}[/yellow]")

    # Extract images to markdown/images directory
    markdown_dir = settings.work_dir / "markdown"
    images_dir = markdown_dir / "images"

    extracted_images = extract_images(settings, input_epub, images_dir)
    image_mapping = get_image_mapping(extracted_images)

    if extracted_images:
        console.print(f"[green]Extracted {len(extracted_images)} images to {images_dir}[/green]")

        # Report cover candidates
        cover_candidates = [img for img in extracted_images if img.is_cover_candidate]
        if cover_candidates:
            console.print("[cyan]Potential cover candidates:[/cyan]")
            for img in cover_candidates[:3]:  # Show top 3
                console.print(f"  - {img.extracted_path.name}")

    # Export markdown files with image references
    created_files = export_to_markdown(settings, input_epub, markdown_dir, image_mapping)
    console.print(f"[green]Exported {len(created_files)} markdown files to {markdown_dir}[/green]")

    # Export combined markdown file
    combined_file = export_combined_markdown(settings, input_epub, markdown_dir, image_mapping)
    console.print(f"[green]Created combined markdown: {combined_file.name}[/green]")
