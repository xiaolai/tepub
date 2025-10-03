from __future__ import annotations

from pathlib import Path

from rich.console import Console

from config import AppSettings
from debug_tools import analysis
from epub_io.selector import SkipAnalysis, SkipCandidate


def test_analyze_library_summarises_results(monkeypatch, tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "book1.epub").touch()
    (library / "book2.epub").touch()

    responses = [
        SkipAnalysis(
            candidates=[
                SkipCandidate(
                    file_path=Path("Text/cover.xhtml"),
                    spine_index=0,
                    reason="cover",
                    source="toc",
                )
            ],
            toc_unmatched_titles=["foreword"],
        ),
        SkipAnalysis(candidates=[], toc_unmatched_titles=["preface"]),
    ]

    iterator = iter(responses)

    def _fake_analysis(_path, _settings):
        try:
            return next(iterator)
        except StopIteration:
            return responses[-1]

    monkeypatch.setattr(analysis, "analyze_skip_candidates", _fake_analysis)

    class DummyProgress:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # pragma: no cover - structural
            return False

        def add_task(self, *_args, **_kwargs):
            return 0

        def advance(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(analysis, "Progress", DummyProgress)

    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)
    test_console = Console(record=True)
    monkeypatch.setattr(analysis, "console", test_console)

    analysis.analyze_library(settings, library, limit=None, top_n=5, report_path=None)

    output = test_console.export_text()
    assert "Skip Reasons" in output
    assert "cover" in output
    assert "Potential New" in output
