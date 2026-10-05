from collections import defaultdict
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from lxml import etree

from epub_io.xhtml import XHTML_NS, local_name, parse_xhtml, text_of
from extraction.segments import iter_segments
from injection.engine import _apply_translations_to_document, _group_translated_segments
from state.models import ExtractMode, Segment, SegmentMetadata, SegmentStatus, TranslationRecord

PATH = Path("Text/ch1.xhtml")


def _document(body: str):
    """A document as the reader builds it, plus its segments from real extraction."""
    markup = (
        f'<html xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>t</title></head><body>{body}</body></html>"
    )
    xhtml = parse_xhtml(markup.encode("utf-8"))
    document = SimpleNamespace(tree=xhtml.root, xhtml=xhtml, path=PATH)
    return document, list(iter_segments(xhtml.root, PATH, spine_index=0))


def _elements(document, name: str):
    return [e for e in document.tree.iter() if local_name(e) == name]


def test_apply_translations_inserts_translation_node():
    document, (segment,) = _document("<p>Original text</p>")

    title_map = defaultdict(dict)
    updated, failures = _apply_translations_to_document(
        document, [(segment, "Translated")], "bilingual", title_map
    )

    assert (updated, failures) == (True, [])
    paragraphs = _elements(document, "p")
    assert [p.get("data-lang") for p in paragraphs] == ["original", "translation"]
    assert paragraphs[1].text == "Translated"
    assert title_map == {}


def test_apply_translations_replaces_in_translated_only_mode():
    document, (segment,) = _document("<h1 id='t'>Original heading</h1>")

    title_map = defaultdict(dict)
    updated, failures = _apply_translations_to_document(
        document, [(segment, "Título traducido")], "translated_only", title_map
    )

    assert (updated, failures) == (True, [])
    (heading,) = _elements(document, "h1")
    assert heading.text == "Título traducido"
    mapped = title_map[PurePosixPath("Text/ch1.xhtml")]
    assert mapped["t"] == "Título traducido"
    assert mapped[None] == "Título traducido"


def test_a_changed_document_is_not_injected():
    """Segments are matched by place and checked by source text before use."""
    document, (segment,) = _document("<p>Original text</p>")
    stale = segment.model_copy(update={"source_content": "Text from an older edition"})

    updated, failures = _apply_translations_to_document(
        document, [(stale, "Translated")], "bilingual", defaultdict(dict)
    )

    assert (updated, failures) == (False, [segment.segment_id])
    assert len(_elements(document, "p")) == 1


def test_translated_copies_carry_no_ids():
    """Bilingual copies beside their originals must not repeat ids (D5)."""
    document, (segment,) = _document("<p id='p1'>Text <a id='r1' href='#n1'>1</a></p>")
    _apply_translations_to_document(document, [(segment, "Texte")], "bilingual", defaultdict(dict))
    ids = [e.get("id") for e in document.tree.iter() if isinstance(e.tag, str) and e.get("id")]
    assert ids == ["p1", "r1"]


def test_group_translated_segments_excludes_auto_copied(tmp_path, monkeypatch):
    """Test that segments with provider_name=None (auto-copied) are excluded from injection."""
    from state.models import SegmentsDocument, StateDocument
    from state.store import save_segments, save_state

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
    """Empty wrapper blockquotes are walked through; the text-bearing level is the unit."""
    document, (segment,) = _document(
        "<blockquote><blockquote><blockquote>Centuries to Millennia Before"
        "</blockquote></blockquote></blockquote>"
    )

    updated, failures = _apply_translations_to_document(
        document, [(segment, "几个世纪到几千年之前")], "bilingual", defaultdict(dict)
    )

    assert (updated, failures) == (True, [])
    quotes = _elements(document, "blockquote")
    assert [q.get("data-lang") for q in quotes] == [None, None, "original", "translation"]


def test_apply_translations_ul_no_wrapper():
    """A list's translation lands as list items in the XHTML namespace, no wrapper."""
    document, (segment,) = _document("<ul><li>Item 1</li></ul>")
    assert segment.extract_mode == ExtractMode.HTML

    _apply_translations_to_document(
        document, [(segment, "<li>项目 1</li>")], "bilingual", defaultdict(dict)
    )

    translated = [u for u in _elements(document, "ul") if u.get("data-lang") == "translation"][0]
    result = etree.tostring(translated, encoding="unicode")
    assert "wrapper" not in result
    assert [text_of(li) for li in translated] == ["项目 1"]
    assert all(etree.QName(li).namespace == XHTML_NS for li in translated)
