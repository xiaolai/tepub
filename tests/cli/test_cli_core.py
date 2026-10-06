"""Shared CLI helpers: workspace discovery, book identity, provider overrides."""

from __future__ import annotations

import os
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from cli.core import resolve_bookless_workspace, settings_for_book, with_provider
from cli.main import app
from config import AppSettings
from exceptions import ArtifactMismatchError, CorruptedStateError
from extraction.pipeline import run_extraction
from tests.epub_builder import build_epub


def test_a_provider_chosen_for_the_run_gets_its_server_from_the_environment(
    monkeypatch,
) -> None:
    """OLLAMA_BASE_URL was applied to the configured provider only."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://other-machine:11434")
    from config import ProviderConfig

    settings = AppSettings(primary_provider=ProviderConfig(name="openai", model="gpt-x"))
    switched = with_provider(settings, "ollama", "gemma")
    assert switched.primary_provider.base_url == "http://other-machine:11434"


def test_renamed_artifacts_mark_a_workspace(tmp_path: Path) -> None:
    book = tmp_path / "root" / "book-a"
    book.mkdir(parents=True)
    (book / "units.json").write_text("{}", encoding="utf-8")
    settings = resolve_bookless_workspace(
        AppSettings(work_dir=tmp_path / "root", segments_file=Path("units.json"))
    )
    assert settings.work_dir == book


def test_a_found_workspace_applies_its_own_config(tmp_path: Path) -> None:
    book = tmp_path / "root" / "book-a"
    book.mkdir(parents=True)
    (book / "segments.json").write_text("{}", encoding="utf-8")
    (book / "config.yaml").write_text("translation_files: [c.xhtml]\n", encoding="utf-8")
    settings = resolve_bookless_workspace(AppSettings(work_dir=tmp_path / "root"))
    assert settings.translation_files == ["c.xhtml"]


def _ctx(settings: AppSettings) -> click.Context:
    ctx = click.Context(click.Command("x"))
    ctx.obj = {"settings": settings, "work_dir_overridden": True, "work_dir_override_path": None}
    return ctx


def test_a_book_given_must_own_the_workspace(tmp_path: Path) -> None:
    """--work-dir naming another book's workspace let format and
    purge-refusals change that book's state."""
    first = build_epub(tmp_path / "first.epub", [("c.xhtml", "C", "<p>One.</p>")])
    other = build_epub(tmp_path / "other.epub", [("c.xhtml", "C", "<p>Else.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, first)

    ctx = _ctx(settings)
    ctx.obj["work_dir_override_path"] = tmp_path / "w"
    assert settings_for_book(ctx, first).work_dir == tmp_path / "w"
    with pytest.raises(ArtifactMismatchError):
        settings_for_book(ctx, other)


def test_purge_refusals_refuses_another_books_workspace(tmp_path: Path) -> None:
    from state.models import SegmentStatus
    from state.store import load_state, save_state

    first = build_epub(tmp_path / "first.epub", [("c.xhtml", "C", "<p>One.</p>")])
    other = build_epub(tmp_path / "other.epub", [("c.xhtml", "C", "<p>Else.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, first)
    state = load_state(settings.state_file)
    for sid, record in state.segments.items():
        state.segments[sid] = record.model_copy(
            update={
                "translation": "I'm sorry, I can't help with that.",
                "status": SegmentStatus.COMPLETED,
            }
        )
    save_state(state, settings.state_file)

    result = CliRunner().invoke(
        app, ["--work-dir", str(tmp_path / "w"), "debug", "purge-refusals", str(other)]
    )
    assert result.exit_code != 0
    assert {r.status for r in load_state(settings.state_file).segments.values()} == {
        SegmentStatus.COMPLETED
    }


def test_unreadable_artifacts_are_not_taken_for_missing_ones(tmp_path: Path) -> None:
    from cli.core import describe_pipeline_artifacts

    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    settings.state_file.write_text("[]", encoding="utf-8")
    with pytest.raises(CorruptedStateError):
        describe_pipeline_artifacts(settings, book)


def test_older_segmentation_is_not_reusable(tmp_path: Path) -> None:
    import json

    from cli.core import describe_pipeline_artifacts

    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    assert describe_pipeline_artifacts(settings, book) == (True, "reusable")
    data = json.loads(settings.segments_file.read_text(encoding="utf-8"))
    data["format_version"] = 3
    settings.segments_file.write_text(json.dumps(data), encoding="utf-8")
    reusable, reason = describe_pipeline_artifacts(settings, book)
    assert not reusable and "older format" in reason


def _two_books_one_layout(tmp_path: Path) -> tuple[Path, AppSettings, AppSettings]:
    """Two books whose units share ids: ids come from place, not text."""
    first = build_epub(tmp_path / "first.epub", [("c.xhtml", "C", "<p>One.</p>")])
    other = build_epub(tmp_path / "other.epub", [("c.xhtml", "C", "<p>Else.</p>")])
    mine, theirs = AppSettings(work_dir=tmp_path / "w"), AppSettings(work_dir=tmp_path / "x")
    run_extraction(mine, first)
    run_extraction(theirs, other)
    return first, mine, theirs


def test_another_books_state_with_the_same_ids_is_not_reused(tmp_path: Path) -> None:
    """The epub digest vouched for segments.json only, so a foreign state.json
    whose ids collided was resumed, and its translations exported."""
    from cli.core import describe_pipeline_artifacts

    first, mine, theirs = _two_books_one_layout(tmp_path)
    assert describe_pipeline_artifacts(mine, first) == (True, "reusable")
    mine.state_file.write_bytes(theirs.state_file.read_bytes())
    reusable, reason = describe_pipeline_artifacts(mine, first)
    assert not reusable and "other source text" in reason


def test_a_state_without_source_digests_is_extracted_again(tmp_path: Path) -> None:
    from cli.core import describe_pipeline_artifacts
    from state.store import load_state, save_state

    first, mine, _ = _two_books_one_layout(tmp_path)
    state = load_state(mine.state_file)
    for record in state.segments.values():
        record.source_sha256 = None
    save_state(state, mine.state_file)
    reusable, reason = describe_pipeline_artifacts(mine, first)
    assert not reusable and "no source digest" in reason
    run_extraction(mine, first)
    assert describe_pipeline_artifacts(mine, first) == (True, "reusable")


def test_a_state_without_segments_is_not_taken_for_the_books(tmp_path: Path) -> None:
    """Identity lives in segments.json; without it, purge-refusals and format
    changed whatever state sat in --work-dir."""
    from exceptions import TepubError
    from state.models import SegmentStatus
    from state.store import load_state, save_state

    first, mine, _ = _two_books_one_layout(tmp_path)
    state = load_state(mine.state_file)
    for sid, record in state.segments.items():
        state.segments[sid] = record.model_copy(
            update={"translation": "I'm sorry, I can't help with that.",
                    "status": SegmentStatus.COMPLETED}
        )
    save_state(state, mine.state_file)
    mine.segments_file.unlink()
    before = mine.state_file.read_bytes()

    ctx = _ctx(mine)
    ctx.obj["work_dir_override_path"] = tmp_path / "w"
    with pytest.raises(TepubError, match="cannot be confirmed"):
        settings_for_book(ctx, tmp_path / "other.epub")
    for command in (["debug", "purge-refusals"], ["format"]):
        result = CliRunner().invoke(
            app, ["--work-dir", str(tmp_path / "w"), *command, str(tmp_path / "other.epub")]
        )
        assert result.exit_code != 0
    assert mine.state_file.read_bytes() == before


def test_workspace_names_in_the_ambiguity_message_are_not_markup(tmp_path: Path) -> None:
    """A path reading "[/blue]" ended the message in a MarkupError."""
    root = tmp_path / "x [" / "blue] y"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / "segments.json").write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(app, ["--work-dir", str(root), "status"])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    flat = "".join(result.output.split())
    assert "holdsworkspacesforseveralbooks" in flat and "x[/blue]y" in flat


def test_an_unreadable_workspace_is_reported(tmp_path: Path) -> None:
    """Resolving a workspace without permission raised a traceback."""
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    root = tmp_path / "w"
    root.mkdir()
    root.chmod(0o444)  # listed, but nothing inside can be looked up
    try:
        results = [
            CliRunner().invoke(app, ["--work-dir", str(root), "format", *args])
            for args in ([str(book)], [])
        ]
    finally:
        root.chmod(0o755)
    for result in results:
        assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
        assert "could not be read" in result.output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files without permission")
@pytest.mark.parametrize("unreadable", ["segments", "book"])
def test_unreadable_files_in_the_identity_check_are_reported(
    tmp_path: Path, unreadable: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reading segments.json or hashing the book without permission raised a
    traceback; other permission errors end with a message and exit 1."""
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    ctx = _ctx(settings)
    ctx.obj["work_dir_override_path"] = tmp_path / "w"
    target = settings.segments_file if unreadable == "segments" else book
    target.chmod(0)
    try:
        with pytest.raises(SystemExit) as raised:
            settings_for_book(ctx, book)
    finally:
        target.chmod(0o644)
    assert raised.value.code == 1
    assert "couldnotberead" in "".join(capsys.readouterr().out.split())
