from __future__ import annotations

import html
import random
import re

import nltk
from lxml import etree
from lxml import html as lxml_html

from state.models import ExtractMode, Segment

BLOCK_PUNCTUATION = re.compile(r"[.!?…]$")
NON_WORD_RE = re.compile(r"^[^\w]+$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。？！])\s+")
LIST_TAGS = {"ul", "ol"}

# Roman numeral pattern and conversion
ROMAN_NUMERAL_PATTERN = re.compile(
    r'^(?:(Chapter|Part|Book|Section)\s+)?([IVXLCDM]+)([.:\-—]?)$',
    re.IGNORECASE
)

ROMAN_TO_INT = {
    'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
    'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
    'XI': 11, 'XII': 12, 'XIII': 13, 'XIV': 14, 'XV': 15,
    'XVI': 16, 'XVII': 17, 'XVIII': 18, 'XIX': 19, 'XX': 20,
    'XXI': 21, 'XXII': 22, 'XXIII': 23, 'XXIV': 24, 'XXV': 25,
    'XXVI': 26, 'XXVII': 27, 'XXVIII': 28, 'XXIX': 29, 'XXX': 30,
    'XXXI': 31, 'XXXII': 32, 'XXXIII': 33, 'XXXIV': 34, 'XXXV': 35,
    'XXXVI': 36, 'XXXVII': 37, 'XXXVIII': 38, 'XXXIX': 39, 'XL': 40,
    'XLI': 41, 'XLII': 42, 'XLIII': 43, 'XLIV': 44, 'XLV': 45,
    'XLVI': 46, 'XLVII': 47, 'XLVIII': 48, 'XLIX': 49, 'L': 50,
    'LX': 60, 'LXX': 70, 'LXXX': 80, 'XC': 90, 'C': 100,
}

INT_TO_WORDS = {
    1: 'One', 2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five',
    6: 'Six', 7: 'Seven', 8: 'Eight', 9: 'Nine', 10: 'Ten',
    11: 'Eleven', 12: 'Twelve', 13: 'Thirteen', 14: 'Fourteen', 15: 'Fifteen',
    16: 'Sixteen', 17: 'Seventeen', 18: 'Eighteen', 19: 'Nineteen', 20: 'Twenty',
    21: 'Twenty-one', 22: 'Twenty-two', 23: 'Twenty-three', 24: 'Twenty-four', 25: 'Twenty-five',
    26: 'Twenty-six', 27: 'Twenty-seven', 28: 'Twenty-eight', 29: 'Twenty-nine', 30: 'Thirty',
    31: 'Thirty-one', 32: 'Thirty-two', 33: 'Thirty-three', 34: 'Thirty-four', 35: 'Thirty-five',
    36: 'Thirty-six', 37: 'Thirty-seven', 38: 'Thirty-eight', 39: 'Thirty-nine', 40: 'Forty',
    41: 'Forty-one', 42: 'Forty-two', 43: 'Forty-three', 44: 'Forty-four', 45: 'Forty-five',
    46: 'Forty-six', 47: 'Forty-seven', 48: 'Forty-eight', 49: 'Forty-nine', 50: 'Fifty',
    60: 'Sixty', 70: 'Seventy', 80: 'Eighty', 90: 'Ninety', 100: 'One hundred',
}


ELLIPSIS_PATTERN = re.compile(r"(\.\s+){2,}\.")


def _normalize_ellipsis(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group()
        return "..." if raw else raw

    new_text = text
    while True:
        updated = ELLIPSIS_PATTERN.sub(replace, new_text)
        if updated == new_text:
            break
        new_text = updated
    return new_text


def ensure_punkt() -> None:
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        nltk.download("punkt")


def _ensure_list_punctuation(root: lxml_html.HtmlElement) -> None:
    for li in root.xpath("//li"):
        if not li.text_content():
            continue
        text = li.text or ""
        if text and BLOCK_PUNCTUATION.search(text.strip()):
            continue
        if li.text:
            li.text = li.text.rstrip() + ". "
        else:
            child_text = li.text_content().rstrip()
            if child_text and not BLOCK_PUNCTUATION.search(child_text):
                li.insert(0, etree.Element("span"))
                li.text = child_text + ". "


def _html_to_text(raw_html: str, element_type: str) -> str:
    root = lxml_html.fromstring(raw_html)
    if element_type in LIST_TAGS:
        _ensure_list_punctuation(root)
    text = root.text_content()
    return html.unescape(" ".join(text.split()))


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _convert_roman_numeral_to_spoken(text: str, segment: Segment) -> str:
    """Convert Roman numerals in titles to spoken form.

    Only converts if:
    - Segment is a heading (h1-h6) OR first in file (order_in_file == 1)
    - Text matches Roman numeral pattern (standalone or with prefix)

    Args:
        text: The text content to check
        segment: The segment metadata

    Returns:
        Text with Roman numerals converted to spoken form, or unchanged
    """
    # Only apply to headings or first segment in file
    is_heading = segment.metadata.element_type in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    is_first_in_file = segment.metadata.order_in_file == 1

    if not (is_heading or is_first_in_file):
        return text

    # Check if text matches Roman numeral pattern
    match = ROMAN_NUMERAL_PATTERN.match(text.strip())
    if not match:
        return text

    prefix = match.group(1) or ""  # Chapter/Part/Book
    roman = match.group(2).upper()  # The Roman numeral
    suffix = match.group(3) or ""  # Punctuation

    # Look up the Roman numeral
    if roman not in ROMAN_TO_INT:
        return text  # Invalid Roman numeral, leave unchanged

    # Convert to integer then to words
    number = ROMAN_TO_INT[roman]
    if number not in INT_TO_WORDS:
        return text  # Number not in our mapping, leave unchanged

    words = INT_TO_WORDS[number]

    # Reconstruct with spoken form
    if prefix:
        return f"{prefix} {words}{suffix}"
    else:
        return f"{words}{suffix}"


def _reextract_filtered(segment: Segment, reader) -> str:
    """Re-extract element from EPUB with footnote filtering.

    Args:
        segment: Segment to re-extract
        reader: EpubReader instance

    Returns:
        Filtered text content without footnote references
    """
    # Load the document
    doc = reader.read_document_by_path(segment.file_path)

    # Find element by xpath
    elements = doc.tree.xpath(segment.xpath)
    if not elements:
        raise ValueError(f"Element not found at xpath: {segment.xpath}")

    element = elements[0]

    # Clone to avoid modifying original
    clone = lxml_html.fromstring(lxml_html.tostring(element, encoding="unicode"))

    # Remove footnote references (a tags with sup/sub children)
    # Preserve tail text before removing the element
    for link in clone.xpath('.//a[sup or sub]'):
        parent = link.getparent()
        if parent is not None:
            # Preserve the tail text (text after the link element)
            if link.tail:
                # Find the previous sibling or use parent.text
                prev = link.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + link.tail
                else:
                    parent.text = (parent.text or "") + link.tail
            parent.remove(link)

    # Extract text
    text = " ".join(clone.text_content().split())
    return text


def segment_to_text(segment: Segment, reader=None) -> str | None:
    """Convert segment to text, optionally re-extracting from EPUB with footnote filtering.

    Args:
        segment: Segment to convert
        reader: Optional EpubReader for re-extraction with filtering

    Returns:
        Text content, or None if segment should be skipped
    """
    if segment.metadata.element_type in {"table", "figure"}:
        return None

    # Skip footnote/endnote definition sections based on segment ID or xpath
    # Common patterns: ftn*, fn*, note*, endnote*, footnote*
    seg_id_lower = segment.segment_id.lower()
    xpath_lower = segment.xpath.lower()

    footnote_id_patterns = ["ftn", "fn-", "note-", "endnote", "footnote"]
    if any(pattern in seg_id_lower for pattern in footnote_id_patterns):
        return None

    # Check xpath for footnote container divs
    footnote_xpath_patterns = ["footnote", "endnote", "notes"]
    if any(f"div[@id='{pattern}" in xpath_lower or f"div[@class='{pattern}" in xpath_lower
           for pattern in footnote_xpath_patterns):
        return None

    # If reader provided, re-extract with footnote filtering
    if reader is not None:
        try:
            content = _reextract_filtered(segment, reader)
        except Exception:
            # Fallback to stored content if re-extraction fails
            if segment.extract_mode == ExtractMode.HTML:
                content = _html_to_text(segment.source_content, segment.metadata.element_type)
            else:
                content = _normalize_text(segment.source_content)
    else:
        # Use stored content
        if segment.extract_mode == ExtractMode.HTML:
            content = _html_to_text(segment.source_content, segment.metadata.element_type)
        else:
            content = _normalize_text(segment.source_content)

    content = _normalize_ellipsis(content)
    if not content:
        return None
    if NON_WORD_RE.match(content):
        return None

    # Convert Roman numerals in titles to spoken form
    content = _convert_roman_numeral_to_spoken(content, segment)

    return content


def split_sentences(text: str, seed: int | None = None) -> list[str]:
    normalized = _normalize_ellipsis(text)
    ensure_punkt()
    tokenizer = nltk.data.load("tokenizers/punkt/english.pickle")
    sentences = tokenizer.tokenize(normalized)

    cleaned: list[str] = []
    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        # Remove leading punctuation artifacts
        stripped = stripped.lstrip(". ")
        if not stripped:
            continue
        if NON_WORD_RE.match(stripped):
            continue
        cleaned.append(stripped)

    if not cleaned:
        normalized = normalized.strip()
        return [normalized] if normalized else []

    return cleaned


def random_pause(range_seconds: tuple[float, float], seed: int | None = None) -> float:
    low, high = range_seconds
    if seed is not None:
        rng = random.Random(seed)
        return rng.uniform(low, high)
    return random.uniform(low, high)
