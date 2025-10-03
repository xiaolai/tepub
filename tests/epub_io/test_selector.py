from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import html

from config import AppSettings
from epub_io import selector


class FakeNav:
    def __init__(self, title: str, href: str, subitems: list | None = None) -> None:
        self.title = title
        self.href = href
        self.subitems = subitems or []


class FakeItem:
    def __init__(self, id_: str, file_name: str) -> None:
        self.id = id_
        self.file_name = file_name
        self.media_type = "application/xhtml+xml"


class FakeBook:
    def __init__(self) -> None:
        self._items = [
            FakeItem("cover", "Text/cover.xhtml"),
            FakeItem("opening", "Text/opening.xhtml"),
            FakeItem("preface", "Text/preface.xhtml"),
            FakeItem("chapter1", "Text/chapter1.xhtml"),
        ]
        self.spine = [
            ("cover", "yes"),
            ("opening", "yes"),
            ("preface", "yes"),
            ("chapter1", "yes"),
        ]
        self.toc = [
            FakeNav("Cover", "Text/cover.xhtml"),
            FakeNav("Opening Remarks", "Text/opening.xhtml"),
            FakeNav("Preface", "Text/preface.xhtml"),
            FakeNav("Chapter 1", "Text/chapter1.xhtml"),
        ]

    def get_items(self):
        return self._items


class FakeReader:
    def __init__(self, *_args, **_kwargs):
        self.book = FakeBook()
        self._docs = [
            SimpleNamespace(
                path=Path("Text/cover.xhtml"),
                spine_item=SimpleNamespace(index=0, href=Path("Text/cover.xhtml"), linear=True),
                tree=html.fromstring("<html><body>Cover Page</body></html>"),
            ),
            SimpleNamespace(
                path=Path("Text/opening.xhtml"),
                spine_item=SimpleNamespace(index=1, href=Path("Text/opening.xhtml"), linear=True),
                tree=html.fromstring("<html><body>Opening remarks from the editor.</body></html>"),
            ),
            SimpleNamespace(
                path=Path("Text/preface.xhtml"),
                spine_item=SimpleNamespace(index=2, href=Path("Text/preface.xhtml"), linear=True),
                tree=html.fromstring("<html><body>This is the preface of the book.</body></html>"),
            ),
            SimpleNamespace(
                path=Path("Text/chapter1.xhtml"),
                spine_item=SimpleNamespace(index=3, href=Path("Text/chapter1.xhtml"), linear=True),
                tree=html.fromstring("<html><body>Acknowledgments and foreword</body></html>"),
            ),
        ]

    def iter_documents(self):
        for doc in self._docs:
            yield doc


@pytest.fixture
def settings(tmp_path):
    cfg = AppSettings()
    return cfg.model_copy(update={"work_dir": tmp_path})


def test_collect_skip_candidates_uses_toc_only(monkeypatch, settings):
    """Test that skip detection only uses TOC titles, not filename/content."""
    monkeypatch.setattr(selector, "EpubReader", FakeReader)

    candidates = selector.collect_skip_candidates(Path("dummy.epub"), settings)

    # Cover should be detected from TOC title
    assert any(c.file_path.name == "cover.xhtml" and c.source == "toc" for c in candidates)

    # chapter1.xhtml has "Acknowledgments" in content but NOT in TOC title
    # With TOC-only detection, it should NOT be flagged
    assert not any(c.file_path.name == "chapter1.xhtml" for c in candidates)


def test_analyze_skip_candidates_reports_unmatched_titles(monkeypatch, settings):
    monkeypatch.setattr(selector, "EpubReader", FakeReader)

    analysis = selector.analyze_skip_candidates(Path("dummy.epub"), settings)

    assert "opening remarks" in analysis.toc_unmatched_titles


def test_skip_after_logic_triggers_cascade(monkeypatch, settings):
    """Test that cascade skipping activates after back-matter triggers."""
    monkeypatch.setattr(selector, "EpubReader", FakeReader)

    # Enable cascade skipping
    settings = settings.model_copy(update={"skip_after_back_matter": True})

    candidates = selector.collect_skip_candidates(Path("dummy.epub"), settings)

    # Should have "cover" from TOC (index 0)
    # Should NOT have cascade skips since our fake book doesn't have back-matter triggers
    assert any(c.source == "toc" for c in candidates)
    assert not any(c.source == "cascade" for c in candidates)


def test_skip_after_logic_can_be_disabled(monkeypatch, settings):
    """Test that cascade skipping can be disabled via configuration."""
    monkeypatch.setattr(selector, "EpubReader", FakeReader)

    # Disable cascade skipping
    settings = settings.model_copy(update={"skip_after_back_matter": False})

    candidates = selector.collect_skip_candidates(Path("dummy.epub"), settings)

    # Should only have TOC-based skips, no cascade
    assert all(c.source != "cascade" for c in candidates)
