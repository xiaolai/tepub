from __future__ import annotations

import html
import re

import nltk
from lxml import html as lxml_html

from state.models import ExtractMode, Segment

BLOCK_PUNCTUATION = re.compile(r"[.!?…]$")
NON_WORD_RE = re.compile(r"^[^\w]+$")
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
    """Ensure the Punkt sentence tokenizer data is available.

    NLTK 3.9 rerouted ``tokenizers/punkt/<lang>.pickle`` to the ``punkt_tab``
    dataset, so downloading only ``punkt`` left the loader raising LookupError at
    synthesis time on a fresh install. Both are requested; each is a no-op when
    already present.
    """
    for resource in ("punkt", "punkt_tab"):
        try:
            nltk.data.find(f"tokenizers/{resource}")
        except LookupError:
            nltk.download(resource, quiet=True)


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
                # Append the punctuation to the last text-bearing node. Copying
                # text_content() into li.text left the original children in place,
                # so every such list item was spoken twice.
                target: tuple[lxml_html.HtmlElement, str] | None = None
                for node in li.iter():
                    if node is li:
                        continue
                    if node.text and node.text.strip():
                        target = (node, "text")
                    if node.tail and node.tail.strip():
                        target = (node, "tail")
                if target is not None:
                    node, attr = target
                    setattr(node, attr, getattr(node, attr).rstrip() + ". ")


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



NOTEREF_HINTS = ("footnote", "noteref", "endnote", "fn", "note")


def _is_noteref(link) -> bool:
    """True when an <a> carrying sup/sub is really a note reference.

    EPUB marks note references explicitly via ``epub:type="noteref"`` or ARIA
    ``role="doc-noteref"``; publishers otherwise signal it with a class name.
    A bare in-page fragment link wrapping a superscript is the common
    unannotated case. Everything else (linked formulas, ordinals, external
    citations) is real content and must survive.
    """
    epub_type = (
        link.get("{http://www.idpf.org/2007/ops}type") or link.get("epub:type") or ""
    ).lower()
    if "noteref" in epub_type:
        return True
    if "doc-noteref" in (link.get("role") or "").lower():
        return True

    haystack = " ".join(
        filter(None, [link.get("class") or "", link.get("id") or ""])
    ).lower()
    if any(hint in haystack for hint in NOTEREF_HINTS):
        return True

    href = link.get("href")
    if href is None:
        # Bare <a><sup>1</sup></a> with no target — a stripped note marker.
        return True
    if "#" in href:
        # Points at a fragment, in this file ("#fn1") or a notes document
        # ("notes.xhtml#n1"). Both are conventional note references.
        return True

    # An <a> with a real, fragment-less destination wrapping a superscript is a
    # linked formula, ordinal or citation — content, not a note marker.
    return False

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

    # Remove footnote references (a tags with sup/sub children).
    # A superscript link is not automatically a footnote — linked formulas,
    # ordinals and citations use the same markup — so require an actual
    # note-reference signal before deleting content.
    # Preserve tail text before removing the element
    for link in clone.xpath('.//a[sup or sub]'):
        if not _is_noteref(link):
            continue
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


FOOTNOTE_DEF_HINTS = ("footnote", "endnote", "rearnote", "ftn", "fn", "note")
_TOKEN_SPLIT_RE = re.compile(r"[\s_\-]+")


def _looks_like_note_identifier(value: str) -> bool:
    """True when an id/class token names a note, e.g. "ftn3", "footnote-2", "fn1".

    Token-prefix matching rather than substring: a plain ``in`` test would match
    ids like "fnord" or any class containing "note".
    """
    for token in _TOKEN_SPLIT_RE.split(value.lower()):
        stripped = token.rstrip("0123456789")
        if stripped and stripped in FOOTNOTE_DEF_HINTS:
            return True
    return False


def _element_is_footnote_definition(segment: Segment, reader) -> bool:
    """Check the element and its ancestors for note-definition semantics.

    EPUB 3 marks definitions with ``epub:type="footnote"`` / ``"endnote"`` or ARIA
    ``role="doc-footnote"``; older books rely on id/class naming.
    """
    try:
        doc = reader.read_document_by_path(segment.file_path)
        elements = doc.tree.xpath(segment.xpath)
    except Exception:
        return False
    if not elements:
        return False

    node = elements[0]
    while node is not None:
        get = getattr(node, "get", None)
        if get is None:
            break
        epub_type = (
            get("{http://www.idpf.org/2007/ops}type") or get("epub:type") or ""
        ).lower()
        if any(hint in epub_type for hint in ("footnote", "endnote", "rearnote", "note")):
            return True
        if (get("role") or "").lower() in {"doc-footnote", "doc-endnote"}:
            return True
        for attr in ("id", "class"):
            value = get(attr) or ""
            if value and _looks_like_note_identifier(value):
                return True
        node = node.getparent()
    return False


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

    # Check xpath for footnote container divs.
    # Note: this only fires for hand-written xpaths carrying @id/@class predicates.
    # Real extraction stores absolute positional paths ("/html/body/div[3]/p[2]"),
    # so the element inspection below is what actually catches definitions in the
    # wild — this check is kept for segments whose xpath does carry predicates.
    footnote_xpath_patterns = ["footnote", "endnote", "notes"]
    if any(f"div[@id='{pattern}" in xpath_lower or f"div[@class='{pattern}" in xpath_lower
           for pattern in footnote_xpath_patterns):
        return None

    # Inspect the actual element and its ancestors. Segment ids are
    # "{file_stem}-{digest}" and xpaths are positional, so neither carries the
    # semantics the checks above look for; without this, in-file footnote
    # definitions were read aloud in full.
    if reader is not None and _element_is_footnote_definition(segment, reader):
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


# Punkt ships models for these languages; the key is the ISO-639-1 prefix.
PUNKT_LANGUAGES = {
    "cs": "czech", "da": "danish", "de": "german", "el": "greek", "en": "english",
    "es": "spanish", "et": "estonian", "fi": "finnish", "fr": "french",
    "it": "italian", "nl": "dutch", "no": "norwegian", "pl": "polish",
    "pt": "portuguese", "ru": "russian", "sl": "slovene", "sv": "swedish",
    "tr": "turkish",
}

# Punkt has no model for CJK, which does not separate sentences with whitespace.
CJK_SENTENCE_RE = re.compile(r"(?<=[。？！…；.!?])\s*")


def _split_cjk(text: str) -> list[str]:
    """Split on CJK sentence terminators, which Punkt cannot handle."""
    return [part for part in CJK_SENTENCE_RE.split(text) if part.strip()]


def split_sentences(text: str, language: str | None = None) -> list[str]:
    """Split text into sentences using a tokenizer appropriate to ``language``.

    Previously the English Punkt model was loaded unconditionally, so CJK text —
    which uses different terminators and no inter-sentence spaces — came back as
    one giant sentence, producing a single unbroken audio segment.
    """
    normalized = _normalize_ellipsis(text)

    prefix = (language or "en").split("-")[0].lower()
    if prefix in {"zh", "ja", "ko"}:
        sentences = _split_cjk(normalized)
    else:
        ensure_punkt()
        punkt_name = PUNKT_LANGUAGES.get(prefix, "english")
        try:
            tokenizer = nltk.data.load(f"tokenizers/punkt/{punkt_name}.pickle")
        except LookupError:
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

