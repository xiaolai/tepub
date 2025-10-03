"""Tests for Roman numeral conversion in audiobook titles."""

from pathlib import Path

from audiobook.preprocess import segment_to_text
from state.models import ExtractMode, Segment, SegmentMetadata


def test_standalone_roman_I_in_h1_heading():
    """Test that standalone 'I' in h1 heading converts to 'One'."""
    segment = Segment(
        segment_id="part-1",
        file_path=Path("part01.xhtml"),
        xpath="/html/body/div/h1[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="I",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=12,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "One"


def test_standalone_roman_II_in_h1_heading():
    """Test that standalone 'II' in h1 heading converts to 'Two'."""
    segment = Segment(
        segment_id="part-2",
        file_path=Path("part02.xhtml"),
        xpath="/html/body/div/h1[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="II",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=16,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Two"


def test_roman_numeral_III_to_X():
    """Test Roman numerals III through X."""
    test_cases = [
        ("III", "Three"),
        ("IV", "Four"),
        ("V", "Five"),
        ("VI", "Six"),
        ("VII", "Seven"),
        ("VIII", "Eight"),
        ("IX", "Nine"),
        ("X", "Ten"),
    ]

    for roman, expected in test_cases:
        segment = Segment(
            segment_id=f"chapter-{roman}",
            file_path=Path("chapter.xhtml"),
            xpath="/html/body/div/h1",
            extract_mode=ExtractMode.TEXT,
            source_content=roman,
            metadata=SegmentMetadata(
                element_type="h1",
                spine_index=1,
                order_in_file=1
            )
        )

        result = segment_to_text(segment, reader=None)
        assert result == expected, f"Expected '{roman}' to convert to '{expected}', got '{result}'"


def test_larger_roman_numerals():
    """Test larger Roman numerals XI through XX and beyond."""
    test_cases = [
        ("XI", "Eleven"),
        ("XII", "Twelve"),
        ("XIII", "Thirteen"),
        ("XIV", "Fourteen"),
        ("XV", "Fifteen"),
        ("XVI", "Sixteen"),
        ("XVII", "Seventeen"),
        ("XVIII", "Eighteen"),
        ("XIX", "Nineteen"),
        ("XX", "Twenty"),
        ("XXI", "Twenty-one"),
        ("XXX", "Thirty"),
        ("XL", "Forty"),
        ("L", "Fifty"),
        ("C", "One hundred"),
    ]

    for roman, expected in test_cases:
        segment = Segment(
            segment_id=f"chapter-{roman}",
            file_path=Path("chapter.xhtml"),
            xpath="/html/body/div/h2",
            extract_mode=ExtractMode.TEXT,
            source_content=roman,
            metadata=SegmentMetadata(
                element_type="h2",
                spine_index=1,
                order_in_file=1
            )
        )

        result = segment_to_text(segment, reader=None)
        assert result == expected, f"Expected '{roman}' to convert to '{expected}', got '{result}'"


def test_pronoun_I_in_sentence_unchanged():
    """Test that 'I' as pronoun in sentence is NOT converted."""
    segment = Segment(
        segment_id="p1",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/p",
        extract_mode=ExtractMode.TEXT,
        source_content="I am a sentence with the pronoun I in it.",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=5  # Not first
        )
    )

    result = segment_to_text(segment, reader=None)
    # Should NOT convert - it's a pronoun, not a title
    assert result == "I am a sentence with the pronoun I in it."


def test_roman_numeral_with_period():
    """Test Roman numeral with trailing period."""
    segment = Segment(
        segment_id="ch1",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="I.",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "One."


def test_roman_numeral_with_colon():
    """Test Roman numeral with trailing colon."""
    segment = Segment(
        segment_id="ch1",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="V:",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Five:"


def test_chapter_prefix_with_roman_numeral():
    """Test 'Chapter I' format."""
    segment = Segment(
        segment_id="ch1",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="Chapter I",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Chapter One"


def test_part_prefix_with_roman_numeral():
    """Test 'Part II' format."""
    segment = Segment(
        segment_id="part2",
        file_path=Path("part.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="Part II",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Part Two"


def test_book_prefix_with_roman_numeral():
    """Test 'Book III' format."""
    segment = Segment(
        segment_id="book3",
        file_path=Path("book.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="Book III",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Book Three"


def test_first_segment_in_file_converts():
    """Test that first segment (order_in_file=1) with Roman numeral converts even if not heading."""
    segment = Segment(
        segment_id="first",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/p[1]",
        extract_mode=ExtractMode.TEXT,
        source_content="IV",
        metadata=SegmentMetadata(
            element_type="p",  # Not a heading
            spine_index=1,
            order_in_file=1  # But first in file
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Four"


def test_non_first_segment_paragraph_not_converted():
    """Test that non-first paragraph with 'I' is NOT converted."""
    segment = Segment(
        segment_id="p5",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/p[5]",
        extract_mode=ExtractMode.TEXT,
        source_content="I",
        metadata=SegmentMetadata(
            element_type="p",
            spine_index=1,
            order_in_file=5  # Not first
        )
    )

    result = segment_to_text(segment, reader=None)
    # Should NOT convert - not a heading and not first in file
    assert result == "I"


def test_lowercase_roman_numeral_in_heading():
    """Test lowercase 'i' in heading converts to 'One'."""
    segment = Segment(
        segment_id="ch1",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/h2",
        extract_mode=ExtractMode.TEXT,
        source_content="i",
        metadata=SegmentMetadata(
            element_type="h2",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "One"


def test_mixed_case_roman_numeral():
    """Test mixed case 'Iv' normalizes and converts."""
    segment = Segment(
        segment_id="ch4",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="Iv",
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    assert result == "Four"


def test_invalid_roman_numeral_unchanged():
    """Test invalid Roman numeral pattern is unchanged."""
    segment = Segment(
        segment_id="invalid",
        file_path=Path("chapter.xhtml"),
        xpath="/html/body/div/h1",
        extract_mode=ExtractMode.TEXT,
        source_content="IIII",  # Invalid - should be IV
        metadata=SegmentMetadata(
            element_type="h1",
            spine_index=1,
            order_in_file=1
        )
    )

    result = segment_to_text(segment, reader=None)
    # Invalid Roman numeral should stay as-is
    assert result == "IIII"
