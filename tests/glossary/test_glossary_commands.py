"""`tepub glossary build` and `check` on a real EPUB workspace."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cli.main import app
from state.models import SegmentStatus
from state.store import load_segments, load_state, save_state
from tests.epub_builder import build_epub

BODY = (
    "<p>The scam compound near Sihanoukville was raided.</p>"
    "<p>Another scam compound opened in Sihanoukville.</p>"
    "<p>A third scam compound, near Bavet.</p>"
)
INDEX = "<ul><li>scam compounds, <a href='ch1.xhtml'>2</a></li><li>Bavet, <a href='ch1.xhtml'>3</a></li></ul>"


def _workspace(tmp_path: Path) -> tuple[CliRunner, Path, Path]:
    book = build_epub(
        tmp_path / "book.epub", [("ch1.xhtml", "One", BODY), ("index.xhtml", "Index", INDEX)]
    )
    root = tmp_path / "work"
    runner = CliRunner()
    result = runner.invoke(app, ["--work-dir", str(root), "extract", str(book)])
    assert result.exit_code == 0, result.output
    (work,) = [path.parent for path in root.rglob("segments.json")]
    return runner, book, work


def _run(runner: CliRunner, root: Path, *args: str):
    return runner.invoke(app, ["--work-dir", str(root), "glossary", *args])


def test_build_proposes_index_terms_and_names_for_review(tmp_path: Path) -> None:
    runner, book, work = _workspace(tmp_path)
    result = _run(runner, tmp_path / "work", "build", str(book), "--no-propose", "--min-count", "2")
    assert result.exit_code == 0, result.output
    proposed = (work / "glossary.proposed.yaml").read_text(encoding="utf-8")
    assert '- source: "scam compound"' in proposed
    assert '- source: "Sihanoukville"' in proposed
    assert not (work / "glossary.yaml").exists()  # nothing is used until reviewed

    again = _run(runner, tmp_path / "work", "build", str(book), "--no-propose")
    assert again.exit_code != 0 and "--force" in again.output


def test_check_lists_misses_and_marks_them_for_retranslation(tmp_path: Path) -> None:
    runner, book, work = _workspace(tmp_path)
    (work / "glossary.yaml").write_text(
        "target_language: Simplified Chinese\nterms:\n"
        '  - source: "scam compound"\n    target: "诈骗园区"\n    avoid: ["集中营"]\n',
        encoding="utf-8",
    )
    renderings = {
        "The scam": "西港附近的诈骗园区被突袭。",
        "Another": "又一个集中营在西港开张。",
        "A third": "第三个窝点，在巴韦。",
    }
    segments = [
        segment
        for opening in renderings
        for segment in load_segments(work / "segments.json").segments
        if segment.source_content.startswith(opening)
    ]
    state = load_state(work / "state.json")
    for segment, translation in zip(segments, renderings.values()):
        state.segments[segment.segment_id] = state.segments[segment.segment_id].model_copy(
            update={"translation": translation, "status": SegmentStatus.COMPLETED, "provider_name": "ollama"}
        )
    state.target_language = "zh-CN"
    save_state(state, work / "state.json")

    result = _run(runner, tmp_path / "work", "check", str(book))
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "scam compound" in output and "2 unit(s) affected" in output

    result = _run(runner, tmp_path / "work", "check", str(book), "--retranslate")
    assert result.exit_code == 0, result.output
    statuses = [load_state(work / "state.json").segments[s.segment_id].status for s in segments]
    assert statuses == [SegmentStatus.COMPLETED, SegmentStatus.PENDING, SegmentStatus.PENDING]

    # The marked units are outstanding work, not a clean bill.
    result = _run(runner, tmp_path / "work", "check", str(book))
    output = " ".join(result.output.split())
    assert "2 unit(s) with glossary terms are not translated yet" in output


def test_check_without_a_glossary_says_how_to_make_one(tmp_path: Path) -> None:
    runner, book, _work = _workspace(tmp_path)
    result = _run(runner, tmp_path / "work", "check", str(book))
    assert result.exit_code == 1
    assert "tepub glossary build" in " ".join(result.output.split())
