from collections import defaultdict
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from lxml import html

from injection.engine import _apply_translations_to_document, _group_translated_segments
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentStatus, TranslationRecord


def test_apply_translations_inserts_translation_node():
    markup = "<html><body><p>Original text</p></body></html>"
    tree = html.fromstring(markup)
    document = SimpleNamespace(tree=tree, spine_item=SimpleNamespace(index=0))

    segment = Segment(
        segment_id="chapter-1",
        file_path=Path("Text/ch1.xhtml"),
        xpath="/html/body/p",
        extract_mode=ExtractMode.TEXT,
        source_content="Original text",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
    )

    title_map = defaultdict(dict)
    updated, failures = _apply_translations_to_document(
        document, [(segment, "Translated")], "bilingual", title_map
    )
    assert updated is True
    assert failures == []
    paragraphs = tree.xpath("/html/body/p")
    assert len(paragraphs) == 2
    assert paragraphs[0].get("data-lang") == "original"
    assert paragraphs[1].get("data-lang") == "translation"
    assert paragraphs[1].text == "Translated"
    assert title_map == {}


def test_apply_translations_replaces_in_translated_only_mode():
    markup = "<html><body><h1 id='t'>Original heading</h1></body></html>"
    tree = html.fromstring(markup)
    document = SimpleNamespace(tree=tree, spine_item=SimpleNamespace(index=0))

    segment = Segment(
        segment_id="chapter-title",
        file_path=Path("Text/ch1.xhtml"),
        xpath="/html/body/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="Original heading",
        metadata=SegmentMetadata(element_type="h1", spine_index=0, order_in_file=1),
    )

    title_map = defaultdict(dict)
    updated, failures = _apply_translations_to_document(
        document, [(segment, "Título traducido")], "translated_only", title_map
    )

    assert updated is True
    assert failures == []
    headings = tree.xpath("/html/body/h1")
    assert len(headings) == 1
    assert headings[0].text == "Título traducido"
    mapped = title_map[PurePosixPath("Text/ch1.xhtml")]
    assert mapped["t"] == "Título traducido"
    assert mapped[None] == "Título traducido"


def test_group_translated_segments_excludes_auto_copied(tmp_path, monkeypatch):
    """Test that segments with provider_name=None (auto-copied) are excluded from injection."""
    from state.store import save_segments, save_state
    from state.models import SegmentsDocument, StateDocument

    # Create test segments
    segments = [
        Segment(
            segment_id="seg-1",
            file_path=Path("Text/ch1.xhtml"),
            xpath="/html/body/p[1]",
            extract_mode=ExtractMode.TEXT,
            source_content="Hello world",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=1),
        ),
        Segment(
            segment_id="seg-2",
            file_path=Path("Text/ch1.xhtml"),
            xpath="/html/body/p[2]",
            extract_mode=ExtractMode.TEXT,
            source_content="…",
            metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=2),
        ),
    ]

    # Create state with one AI-translated and one auto-copied segment
    state = StateDocument(
        segments={
            "seg-1": TranslationRecord(
                segment_id="seg-1",
                status=SegmentStatus.COMPLETED,
                translation="Translated text",
                provider_name="anthropic",
                model_name="claude-3-5-sonnet",
            ),
            "seg-2": TranslationRecord(
                segment_id="seg-2",
                status=SegmentStatus.COMPLETED,
                translation="…",
                provider_name=None,  # Auto-copied segment
                model_name=None,
            ),
        },
        current_provider="anthropic",
        current_model="claude-3-5-sonnet",
    )

    # Save test data
    work_root = tmp_path / ".tepub"
    work_root.mkdir()
    segments_file = work_root / "segments.json"
    state_file = work_root / "state.json"

    segments_doc = SegmentsDocument(
        epub_path=tmp_path / "test.epub",
        generated_at="2024-01-01T00:00:00",
        segments=segments,
    )
    save_segments(segments_doc, segments_file)
    save_state(state, state_file)

    # Create settings mock
    settings = SimpleNamespace(
        segments_file=segments_file,
        state_file=state_file,
    )

    # Test the grouping function
    grouped = _group_translated_segments(settings)

    # Should only include the AI-translated segment, not the auto-copied one
    assert Path("Text/ch1.xhtml") in grouped
    segments_list = grouped[Path("Text/ch1.xhtml")]
    assert len(segments_list) == 1
    assert segments_list[0][0].segment_id == "seg-1"
    assert segments_list[0][1] == "Translated text"


def test_apply_translations_nested_blockquote_smart():
    """Test injection with smart-extracted nested blockquotes."""
    # Outer wrapper (no text), innermost has text
    markup = """<html><body>
    <blockquote><blockquote><blockquote>
      Centuries to Millennia Before
    </blockquote></blockquote></blockquote>
    </body></html>"""

    tree = html.fromstring(markup)
    document = SimpleNamespace(tree=tree, spine_item=SimpleNamespace(index=0))

    # Only innermost extracted (from smart extraction)
    segment = Segment(
        segment_id="bq-inner",
        file_path=Path("Text/ch1.xhtml"),
        xpath="/html/body/blockquote/blockquote/blockquote",
        extract_mode=ExtractMode.TEXT,
        source_content="Centuries to Millennia Before",
        metadata=SegmentMetadata(element_type="blockquote", spine_index=0, order_in_file=1),
    )

    title_map = defaultdict(dict)
    updated, failures = _apply_translations_to_document(
        document, [(segment, "几个世纪到几千年之前")], "bilingual", title_map
    )

    assert updated is True
    assert failures == []

    # Innermost level should have 2 blockquotes (original + translation)
    innermost_bqs = tree.xpath("/html/body/blockquote/blockquote/blockquote")
    assert len(innermost_bqs) == 2
    assert innermost_bqs[0].get("data-lang") == "original"
    assert innermost_bqs[1].get("data-lang") == "translation"

    # Outer blockquotes should have NO data-lang (not extracted)
    outer_bqs = tree.xpath("/html/body/blockquote")
    for bq in outer_bqs[:1]:  # First outer blockquote
        assert bq.get("data-lang") is None


def test_apply_translations_ul_no_wrapper():
    """Verify UL translation has no wrapper artifact."""
    markup = """<html><body><ul><li>Item 1</li></ul></body></html>"""
    tree = html.fromstring(markup)
    document = SimpleNamespace(tree=tree, spine_item=SimpleNamespace(index=0))

    segment = Segment(
        segment_id="list-1",
        file_path=Path("Text/ch1.xhtml"),
        xpath="/html/body/ul",
        extract_mode=ExtractMode.HTML,
        source_content="<li>Item 1</li>",
        metadata=SegmentMetadata(element_type="ul", spine_index=0, order_in_file=1),
    )

    title_map = defaultdict(dict)
    updated, failures = _apply_translations_to_document(
        document, [(segment, "<li>项目 1</li>")], "bilingual", title_map
    )

    uls = tree.xpath("/html/body/ul")
    translation_ul = uls[1]
    result = html.tostring(translation_ul, encoding="unicode")

    assert "<wrapper>" not in result
    assert "<li>项目 1</li>" in result
