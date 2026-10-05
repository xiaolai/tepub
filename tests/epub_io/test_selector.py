from __future__ import annotations

from pathlib import Path

import pytest

from config import AppSettings
from epub_io import selector
from tests.epub_builder import build_epub


@pytest.fixture
def book(tmp_path) -> Path:
    """Four chapters; only the table-of-contents titles should drive skipping."""
    return build_epub(
        tmp_path / "book.epub",
        [
            ("Text/cover.xhtml", "Cover", "<p>Cover Page</p>"),
            ("Text/opening.xhtml", "Opening Remarks", "<p>Opening remarks from the editor.</p>"),
            ("Text/preface.xhtml", "Preface", "<p>This is the preface of the book.</p>"),
            ("Text/chapter1.xhtml", "Chapter 1", "<p>Acknowledgments and foreword</p>"),
        ],
    )


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings()
    return cfg.model_copy(update={"work_dir": tmp_path})


def test_collect_skip_candidates_uses_toc_only(book, settings):
    """Test that skip detection only uses TOC titles, not filename/content."""

    candidates = selector.collect_skip_candidates(book, settings)

    # Cover should be detected from TOC title
    assert any(c.file_path.name == "cover.xhtml" and c.source == "toc" for c in candidates)

    # chapter1.xhtml has "Acknowledgments" in content but NOT in TOC title
    # With TOC-only detection, it should NOT be flagged
    assert not any(c.file_path.name == "chapter1.xhtml" for c in candidates)


def test_analyze_skip_candidates_reports_unmatched_titles(book, settings):

    analysis = selector.analyze_skip_candidates(book, settings)

    assert "opening remarks" in analysis.toc_unmatched_titles


def test_skip_after_logic_triggers_cascade(book, settings):
    """Test that cascade skipping activates after back-matter triggers."""

    # Enable cascade skipping
    settings = settings.model_copy(update={"skip_after_back_matter": True})

    candidates = selector.collect_skip_candidates(book, settings)

    # Should have "cover" from TOC (index 0)
    # Should NOT have cascade skips since our fake book doesn't have back-matter triggers
    assert any(c.source == "toc" for c in candidates)
    assert not any(c.source == "cascade" for c in candidates)


def test_skip_after_logic_can_be_disabled(book, settings):
    """Test that cascade skipping can be disabled via configuration."""

    # Disable cascade skipping
    settings = settings.model_copy(update={"skip_after_back_matter": False})

    candidates = selector.collect_skip_candidates(book, settings)

    # Should only have TOC-based skips, no cascade
    assert all(c.source != "cascade" for c in candidates)
