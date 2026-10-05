"""`tepub glossary`: propose a book's glossary, and check translations against it."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from cli.core import prepare_settings_for_epub
from cli.errors import handle_run_errors, handle_state_errors
from config import AppSettings
from console_singleton import get_console
from epub_io.reader import EpubReader
from glossary import GLOSSARY_FILE, GlossaryError, glossary_for, load_glossary, plain_text
from glossary.build import PROPOSED_FILE, propose, render_proposals
from glossary.candidates import candidates
from glossary.report import find_misses, mark_for_retranslation
from state.models import ExtractMode, Segment, SegmentMetadata
from state.store import load_segments, load_state
from state.writer import update_state_atomic

console = get_console()


def _workspace(ctx: click.Context, input_epub: Path) -> AppSettings:
    settings = prepare_settings_for_epub(ctx, ctx.obj["settings"], input_epub, override=None)
    settings.validate_for_translation(input_epub)
    return settings


@click.group()
def glossary() -> None:
    """Keep a book's terms consistent: propose a glossary, check translations."""


@glossary.command("build")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option("--to", "target_language", default=None, help="Target language (code or name).")
@click.option(
    "--propose/--no-propose",
    "ask_model",
    default=True,
    help="Ask the translation model for a rendering of each term (default), or leave them blank.",
)
@click.option("--limit", default=150, show_default=True, help="Most terms to propose.")
@click.option("--min-count", default=3, show_default=True, help="Fewest occurrences for a term.")
@click.option("--force", is_flag=True, help=f"Overwrite an existing {PROPOSED_FILE}.")
@click.pass_context
@handle_state_errors
@handle_run_errors
def build(
    ctx: click.Context,
    input_epub: Path,
    target_language: str | None,
    ask_model: bool,
    limit: int,
    min_count: int,
    force: bool,
) -> None:
    """Propose terms from the book's index and names, for review."""
    settings = _workspace(ctx, input_epub)
    output = settings.work_dir / PROPOSED_FILE
    if output.exists() and not force:
        raise click.ClickException(f"{output} exists; review it, or pass --force to replace it.")
    target = target_language or settings.target_language
    segments_doc = load_segments(settings.segments_file)
    text = "\n".join(plain_text(segment.source_content) for segment in segments_doc.segments)

    book_glossary = settings.work_dir / GLOSSARY_FILE
    known = {t.source for t in load_glossary(book_glossary).terms} if book_glossary.exists() else set()
    reader = EpubReader(input_epub, settings)
    indexes = [
        document.tree
        for skipped in segments_doc.skipped_documents
        if skipped.reason == "index"
        and (document := reader.read_document_by_path(skipped.file_path)).tree is not None
    ]
    found = candidates(indexes, text, known=known, min_count=min_count, limit=limit)
    console.print(
        f"Found {len(found)} terms ({len(indexes)} index document(s); "
        f"{len(known)} already in {GLOSSARY_FILE})."
    )

    translate = None
    if ask_model and found:
        from translation.providers import create_provider

        provider = create_provider(settings.primary_provider)
        provider.preflight()

        def translate(term: str, context: str) -> str:
            segment = Segment(
                segment_id=f"glossary:{term}",
                file_path=Path(GLOSSARY_FILE),
                xpath="/",
                extract_mode=ExtractMode.TEXT,
                source_content=term,
                metadata=SegmentMetadata(
                    element_type="term",
                    spine_index=0,
                    order_in_file=0,
                    notes=(
                        f'This is a term from a book, used in this sentence: "{context}" '
                        "Translate only the term itself, and reply with its translation alone."
                    ),
                ),
            )
            return provider.translate(segment, source_language="auto", target_language=target)

    with console.status("Proposing renderings...") as status:
        proposals = propose(
            found,
            text,
            translate,
            progress=lambda done: status.update(f"Proposing renderings... {done}/{len(found)}"),
        )
    output.write_text(render_proposals(proposals, target), encoding="utf-8")
    console.print(f"[green]Wrote {output}[/green]")
    console.print(
        f"Review it, then save it as {settings.work_dir / GLOSSARY_FILE}; "
        "`tepub translate` uses it from then on."
    )


@glossary.command("check")
@click.argument("input_epub", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--retranslate",
    is_flag=True,
    help="Mark the units that miss a rendering as pending, for the next `tepub translate`.",
)
@click.pass_context
@handle_state_errors
@handle_run_errors
def check(ctx: click.Context, input_epub: Path, retranslate: bool) -> None:
    """List finished translations that do not follow the glossary."""
    settings = _workspace(ctx, input_epub)
    if not settings.state_file.exists():
        raise click.ClickException("Nothing translated yet; run `tepub translate` first.")
    state = load_state(settings.state_file)
    book_glossary = glossary_for(settings.work_root, settings.work_dir, state.target_language)
    if book_glossary is None:
        raise GlossaryError(
            f"No {GLOSSARY_FILE} in {settings.work_dir} or {settings.work_root}; "
            "create one with `tepub glossary build`."
        )
    misses = find_misses(load_segments(settings.segments_file).segments, state, book_glossary)
    if not misses:
        console.print(f"[green]Every finished unit follows the glossary "
                      f"({len(book_glossary.decided())} terms).[/green]")
        return

    by_term: dict[str, list] = {}
    for miss in misses:
        by_term.setdefault(miss.term, []).append(miss)
    table = Table(title="Translations not following the glossary")
    table.add_column("Term")
    table.add_column("Units", justify="right")
    table.add_column("Example")
    for term, term_misses in sorted(by_term.items(), key=lambda item: -len(item[1])):
        table.add_row(term, str(len(term_misses)), term_misses[0].problem)
    console.print(table)
    units = {miss.segment_id for miss in misses}
    console.print(f"{len(units)} unit(s) affected.")
    if retranslate:
        update_state_atomic(settings.state_file, lambda doc: mark_for_retranslation(doc, units))
        console.print(f"[yellow]Marked {len(units)} unit(s) for translation; "
                      "run `tepub translate` to redo them with the glossary.[/yellow]")
    else:
        console.print("Pass --retranslate to redo them with the glossary.")
