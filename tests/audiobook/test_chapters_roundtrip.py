"""Round-trip tests for chapter YAML export/import."""

from __future__ import annotations

from audiobook.chapters import ChapterInfo, read_chapters_yaml, write_chapters_yaml


def _roundtrip(tmp_path, chapters, metadata=None):
    path = tmp_path / "chapters.yaml"
    write_chapters_yaml(chapters, metadata or {"source": "t.epub"}, path)
    return read_chapters_yaml(path)


def test_segments_survive_roundtrip(tmp_path):
    """Segments were previously emitted as a truncated comment and lost on read."""
    original = [ChapterInfo("Ch 1", 10.0, ["a.xhtml", "b.xhtml", "c.xhtml", "d.xhtml"])]

    chapters, _ = _roundtrip(tmp_path, original)

    assert chapters[0].segments == ["a.xhtml", "b.xhtml", "c.xhtml", "d.xhtml"]


def test_titles_with_yaml_metacharacters_survive(tmp_path):
    """Unescaped interpolation used to emit invalid YAML for ordinary book titles."""
    tricky = 'He said "hello" \\ and left: really?'
    original = [ChapterInfo(tricky, 1.0, [])]

    chapters, _ = _roundtrip(tmp_path, original)

    assert chapters[0].title == tricky


def test_fractional_timestamps_are_not_floored(tmp_path):
    """Flooring made export->import shift every marker by up to a second."""
    original = [ChapterInfo("Ch 1", 45.5, []), ChapterInfo("Ch 2", 5025.0, [])]

    chapters, _ = _roundtrip(tmp_path, original)

    assert chapters[0].start == 45.5
    assert chapters[1].start == 5025.0


def test_metadata_survives_roundtrip(tmp_path):
    """Metadata values must be readable back, not just written as comments."""
    meta = {"source": "book.epub", "mode": "preview", "note": "a: b", "empty": None}

    _, loaded = _roundtrip(tmp_path, [ChapterInfo("Ch", 0.0, [])], meta)

    assert loaded["source"] == "book.epub"
    assert loaded["note"] == "a: b"
    assert "empty" not in loaded
