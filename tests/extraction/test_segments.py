import re
from pathlib import Path

from lxml import html

from extraction.segments import iter_segments
from state.models import ExtractMode


def test_iter_segments_classifies_simple_and_atomic():
    markup = """
    <html><body>
    <h1>Chapter One</h1>
    <p>Paragraph text.</p>
    <div>Inline <em>emphasis</em>.</div>
    <ul><li>Item <span>One</span></li><li>Item Two</li></ul>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("chapter1.xhtml"), spine_index=0))
    assert len(segments) == 4

    heading = segments[0]
    assert heading.extract_mode == ExtractMode.TEXT
    assert heading.source_content == "Chapter One"

    list_segment = segments[-1]
    assert list_segment.extract_mode == ExtractMode.HTML
    assert "<span" not in list_segment.source_content
    assert "Item One" in list_segment.source_content


def test_iter_segments_handles_drop_cap():
    """Test that drop cap pattern (decorative first letter) extracts correctly."""
    markup = """
    <html><body>
    <p><span class="drop"><span class="big">T</span></span>he fantasy always runs like this.</p>
    <p>Normal paragraph without drop cap.</p>
    <p><span><span>A</span></span>nother drop cap example.</p>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("test.xhtml"), spine_index=0))
    assert len(segments) == 3

    # First paragraph with drop cap should extract as "The fantasy..." not "T he fantasy..."
    assert segments[0].source_content == "The fantasy always runs like this."

    # Normal paragraph
    assert segments[1].source_content == "Normal paragraph without drop cap."

    # Another drop cap
    assert segments[2].source_content == "Another drop cap example."


def test_iter_segments_normalizes_ellipsis():
    """Test that spaced ellipsis patterns are normalized to standard ellipsis."""
    markup = """
    <html><body>
    <p>Text before . . . and after.</p>
    <p>Multiple . . . . dots here.</p>
    <p>Already correct... format.</p>
    <p>Mix of . . . and ... patterns.</p>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("test.xhtml"), spine_index=0))
    assert len(segments) == 4

    # Spaced ellipsis should be normalized to ...
    assert segments[0].source_content == "Text before ... and after."
    assert segments[1].source_content == "Multiple ... dots here."
    assert segments[2].source_content == "Already correct... format."
    assert segments[3].source_content == "Mix of ... and ... patterns."


def test_iter_segments_nested_blockquote_only_innermost_has_text():
    """Extract only innermost when outer levels are empty wrappers."""
    markup = """
    <html><body>
    <blockquote><blockquote><blockquote>
      Centuries to Millennia Before
    </blockquote></blockquote></blockquote>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("test.xhtml"), spine_index=0))

    # Only innermost blockquote has text, outer levels are wrappers
    assert len(segments) == 1
    assert segments[0].source_content == "Centuries to Millennia Before"
    assert segments[0].metadata.element_type == "blockquote"


def test_iter_segments_nested_blockquote_multiple_levels_have_text():
    """Extract all levels that have their own text content."""
    markup = """
    <html><body>
    <blockquote>
      Outer level text
      <blockquote>Inner level text</blockquote>
    </blockquote>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("test.xhtml"), spine_index=0))

    # Both levels have text
    assert len(segments) == 2
    contents = [s.source_content for s in segments]
    assert "Outer level text Inner level text" in contents  # Outer (includes inner)
    assert "Inner level text" in contents  # Inner


def test_iter_segments_nested_blockquote_mixed():
    """Extract only levels with text, skip empty wrappers."""
    markup = """
    <html><body>
    <blockquote>
      <blockquote>
        Middle level text
        <blockquote>Innermost text</blockquote>
      </blockquote>
    </blockquote>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("test.xhtml"), spine_index=0))

    # Outer has no own text (skip), middle and innermost have text (extract)
    assert len(segments) == 2
    contents = [s.source_content for s in segments]
    # Middle level includes innermost
    assert any("Middle level text" in c and "Innermost text" in c for c in contents)
    # Innermost only
    assert "Innermost text" in contents


def test_iter_segments_nested_div_smart_extraction():
    """Test smart extraction works for nested divs too."""
    markup = """
    <html><body>
    <div>
      <div>Actual content</div>
    </div>
    </body></html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("test.xhtml"), spine_index=0))

    # Only inner div has text
    assert len(segments) == 1
    assert segments[0].source_content == "Actual content"
    assert segments[0].metadata.element_type == "div"


def _normalize_html(value: str) -> str:
    return re.sub(r"\s+", "", value)


def test_iter_segments_handles_complex_markup():
    markup = """
    <html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>
      <body>
        <section epub:type='frontmatter' id='fm'>
          <h2 class='heading'>Preface &amp; Overview</h2>
          <p id='p1' class='lead'>This <span>preface</span> has <a href='#'>links</a> &amp; inline elements.</p>
          <div id='blurb' style='color:red;'>Solo text block.</div>
          <div class='skip'><p>Nested paragraph should not produce div segment.</p></div>
          <table id='stats' class='table table-striped' style='width:100%'>
            <thead>
              <tr><th scope='col'>Year</th><th>Sales</th></tr>
            </thead>
            <tbody>
              <tr class='row'>
                <td data-label='Year'><span>2024</span></td>
                <td><span>10k</span></td>
              </tr>
              <tr>
                <td>2025</td>
                <td><span>12k</span></td>
              </tr>
            </tbody>
          </table>
          <ul class='bullet-list' id='points'>
            <li><span>First</span> point</li>
            <li>Second <strong>point</strong>
              <ul>
                <li>Nested should be ignored</li>
              </ul>
            </li>
          </ul>
          <epub:switch>
            <epub:case><p>Namespaced case paragraph.</p></epub:case>
          </epub:switch>
        </section>
      </body>
    </html>
    """
    tree = html.fromstring(markup)
    segments = list(iter_segments(tree, Path("complex.xhtml"), spine_index=2))

    assert len(segments) == 7
    assert [segment.metadata.element_type for segment in segments] == [
        "h2",
        "p",
        "div",
        "p",
        "table",
        "ul",
        "p",
    ]

    for index, segment in enumerate(segments, start=1):
        assert segment.metadata.order_in_file == index
        assert segment.metadata.spine_index == 2

    heading, first_para, blurb_div, nested_para, table_seg, list_seg, namespaced_para = segments

    assert heading.extract_mode == ExtractMode.TEXT
    assert heading.source_content == "Preface & Overview"

    assert first_para.extract_mode == ExtractMode.TEXT
    assert first_para.source_content == "This preface has links & inline elements."

    assert blurb_div.extract_mode == ExtractMode.TEXT
    assert blurb_div.source_content == "Solo text block."

    assert nested_para.extract_mode == ExtractMode.TEXT
    assert nested_para.source_content == "Nested paragraph should not produce div segment."

    assert table_seg.extract_mode == ExtractMode.HTML
    expected_table = (
        "<thead><tr><th>Year</th><th>Sales</th></tr></thead>"
        "<tbody><tr><td>2024</td><td>10k</td></tr><tr><td>2025</td><td>12k</td></tr></tbody>"
    )
    assert _normalize_html(table_seg.source_content) == _normalize_html(expected_table)
    assert "span" not in table_seg.source_content
    assert "class=" not in table_seg.source_content

    assert list_seg.extract_mode == ExtractMode.HTML
    expected_list = (
        "<li>First point</li>"
        "<li>Second <strong>point</strong><ul><li>Nested should be ignored</li></ul></li>"
    )
    assert _normalize_html(list_seg.source_content) == _normalize_html(expected_list)
    assert "span" not in list_seg.source_content
    assert "class=" not in list_seg.source_content

    assert namespaced_para.extract_mode == ExtractMode.TEXT
    assert namespaced_para.source_content == "Namespaced case paragraph."
