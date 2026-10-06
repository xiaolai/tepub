"""`tepub audiobook generate` refuses bad input before any synthesis starts."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from PIL import Image

from audiobook.cover import SpineCoverCandidate
from cli.main import app
from config import AppSettings
from exceptions import ArtifactMismatchError
from extraction.pipeline import run_extraction
from tests.epub_builder import build_epub

module = sys.modules["cli.commands.audiobook"]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runs: list[dict] = []
    monkeypatch.setattr(module, "run_audiobook", lambda **kwargs: runs.append(kwargs))
    monkeypatch.setattr(
        module, "list_voices_for_provider",
        lambda *a, **k: [{"ShortName": "en-US-GuyNeural", "Locale": "en-US"}],
    )
    monkeypatch.delenv("TEPUB_AUDIOBOOK_COVER_PATH", raising=False)
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One sentence here.</p>")])
    work = tmp_path / "w"
    extracted = CliRunner().invoke(
        app, ["--work-dir", str(work), "extract", str(book)], prog_name="tepub"
    )
    assert extracted.exit_code == 0, extracted.output
    cover = tmp_path / "cover.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(cover)

    def generate(*args: str, env: dict | None = None):
        return CliRunner().invoke(
            app,
            ["--work-dir", str(work), "audiobook", "generate", str(book), *args],
            prog_name="tepub",
            env=env,
        )

    (book_dir,) = {path.parent for path in work.rglob("segments.json")}
    return {
        "generate": generate, "runs": runs, "work": book_dir, "cover": cover,
        "tmp": tmp_path, "book": book,
    }


@pytest.mark.parametrize(
    "args",
    [
        ["--volume", "+2dB"],
        ["--rate", "fast"],
        ["--tts-speed", "9"],
        ["--tts-speed", "nan"],
    ],
)
def test_invalid_prosody_or_speed_is_refused(workspace, args) -> None:
    result = workspace["generate"]("--voice", "en-US-GuyNeural", *args)

    assert result.exit_code == 2, result.output
    assert workspace["runs"] == []


def test_signed_percentages_are_accepted(workspace) -> None:
    result = workspace["generate"]("--voice", "en-US-GuyNeural", "--volume", "+2%")

    assert result.exit_code == 0, result.output
    assert workspace["runs"][0]["volume"] == "+2%"


def test_cover_that_is_not_an_image_is_refused(workspace) -> None:
    not_image = workspace["tmp"] / "notes.png"
    not_image.write_text("not an image")

    voice = ("--voice", "en-US-GuyNeural")
    as_dir = workspace["generate"](*voice, "--cover-path", str(workspace["tmp"]))
    as_text = workspace["generate"](*voice, "--cover-path", str(not_image))

    assert as_dir.exit_code == 2 and as_text.exit_code == 2
    assert "not a readable image" in as_text.output
    assert workspace["runs"] == []


def test_stale_env_cover_does_not_block_an_explicit_cover(workspace) -> None:
    result = workspace["generate"](
        "--voice", "en-US-GuyNeural", "--cover-path", str(workspace["cover"]),
        env={"TEPUB_AUDIOBOOK_COVER_PATH": str(workspace["tmp"] / "gone.png")},
    )

    assert result.exit_code == 0, result.output
    assert workspace["runs"][0]["cover_path"] == workspace["cover"]


def test_config_cover_in_home_is_expanded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    Image.new("RGB", (8, 8)).save(tmp_path / "cover.png")
    settings = AppSettings(work_dir=tmp_path / "w", cover_image_path=Path("~/cover.png"))

    chosen = module._choose_cover(None, settings, None, tmp_path / "book.epub")

    assert chosen == tmp_path / "cover.png"


def test_unreadable_stored_cover_is_refused(tmp_path) -> None:
    stored = tmp_path / "old-cover.jpg"
    stored.write_text("not an image")
    settings = AppSettings(work_dir=tmp_path / "w")

    with pytest.raises(click.UsageError, match="not a readable image"):
        module._choose_cover(None, settings, stored, tmp_path / "book.epub")


def test_unreadable_detected_cover_is_passed_over(tmp_path, monkeypatch) -> None:
    candidate = tmp_path / "spine_cover.svg"
    candidate.write_text("<svg/>")
    monkeypatch.setattr(
        module,
        "_write_cover_candidate",
        lambda settings, epub: (
            candidate, SpineCoverCandidate(Path("cover.svg"), Path("c.xhtml"))
        ),
    )
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    settings = AppSettings(work_dir=tmp_path / "w")

    with module.console.capture() as captured:
        chosen = module._choose_cover(None, settings, None, tmp_path / "book.epub")

    assert chosen is None
    assert "not using it" in captured.get()


def test_cover_only_refuses_audio_settings(workspace) -> None:
    result = workspace["generate"]("--cover-only", "--voice", "en-US-JennyNeural")

    assert result.exit_code == 2, result.output
    assert "--voice" in result.output
    assert workspace["runs"] == []


def test_voice_for_the_other_provider_is_refused(workspace) -> None:
    result = workspace["generate"]("--voice", "nova")

    assert result.exit_code == 2, result.output
    assert "not a edge voice" in " ".join(result.output.split())


def test_no_voice_for_the_language_stops_instead_of_guessing(workspace, monkeypatch) -> None:
    monkeypatch.setattr(module, "list_voices_for_provider", lambda *a, **k: [])

    result = workspace["generate"]()

    assert result.exit_code == 2, result.output
    assert "Pass --voice" in " ".join(result.output.split())
    assert workspace["runs"] == []


def test_unreadable_state_of_the_selected_provider_stops(workspace) -> None:
    state = workspace["work"] / "audiobook@edgetts" / "audio_state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json")

    result = workspace["generate"]("--voice", "en-US-GuyNeural")

    assert result.exit_code == 1, result.output
    assert workspace["runs"] == []


def test_another_books_workspace_is_refused(workspace) -> None:
    # A different book saved over the same file reaches the same workspace.
    build_epub(workspace["book"], [("x.xhtml", "X", "<p>Different book.</p>")])

    result = workspace["generate"]("--voice", "en-US-GuyNeural")

    assert isinstance(result.exception, ArtifactMismatchError), result.output
    assert workspace["runs"] == []


def test_language_is_detected_from_the_selected_segments(tmp_path) -> None:
    epub = build_epub(
        tmp_path / "book.epub",
        [("front.xhtml", "Front", "<p>Bonjour tout le monde.</p>"),
         ("main.xhtml", "Main", "<p>Hello world.</p>")],
    )
    settings = AppSettings(work_dir=tmp_path / "w", audiobook_files=["main.xhtml"])
    run_extraction(settings, epub)
    from state.store import load_segments

    texts = module._sample_texts(settings, load_segments(settings.segments_file))

    assert texts == ["Hello world."]


def test_malformed_chapters_file_is_reported(tmp_path) -> None:
    book = tmp_path / "book.m4a"
    book.write_bytes(b"")
    chapters = tmp_path / "chapters.yaml"
    chapters.write_text("chapters:\n  - title: One\n    start: 'a:b'\n")

    result = CliRunner().invoke(
        app, ["audiobook", "update-chapters", str(book), str(chapters)], prog_name="tepub"
    )

    assert result.exit_code == 1, result.output
    assert "Invalid chapters file" in result.output


def test_oversized_chapter_timestamp_is_reported(tmp_path) -> None:
    book = tmp_path / "book.m4a"
    book.write_bytes(b"")
    chapters = tmp_path / "chapters.yaml"
    chapters.write_text("chapters:\n  - title: One\n    start: 1" + "0" * 400 + "\n")

    result = CliRunner().invoke(
        app, ["audiobook", "update-chapters", str(book), str(chapters)], prog_name="tepub"
    )

    assert result.exit_code == 1, result.output
    assert "Invalid chapters file" in result.output and "too large" in result.output


def test_truncated_cover_is_refused(tmp_path) -> None:
    whole = tmp_path / "whole.jpg"
    Image.new("RGB", (256, 256), (200, 100, 50)).save(whole, quality=95)
    truncated = tmp_path / "truncated.jpg"
    truncated.write_bytes(whole.read_bytes()[: whole.stat().st_size // 2])
    with Image.open(truncated) as image:
        image.verify()  # the check this replaces let the file through

    assert module._image_problem(whole) is None
    assert "not a readable image" in (module._image_problem(truncated) or "")


def test_stored_speed_is_kept_when_none_is_given(workspace) -> None:
    from audiobook.state import ensure_state

    state_dir = workspace["work"] / "audiobook@edgetts"
    state_dir.mkdir()
    ensure_state(
        state_dir / "audio_state.json", state_dir, "en-US-GuyNeural",
        language="en", tts_speed=1.5,
    )

    result = workspace["generate"]()

    assert result.exit_code == 0, result.output
    assert workspace["runs"][-1]["tts_speed"] == 1.5
