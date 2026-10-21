from __future__ import annotations

import html
import logging
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote

from lxml import html as lxml_html

from epub_io.path_utils import normalize_epub_href
from epub_io.xhtml import local_name
from extraction.segments import locate_units
from state.models import ExtractMode, Segment

logger = logging.getLogger(__name__)

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


def _nltk():
    """Import nltk lazily.

    nltk was imported at module scope, and the CLI's command registration reaches
    this module, so *every* tepub invocation loaded it — ~76 ms of startup for a
    dependency only audiobook synthesis needs.

    It also made the CLI unusable from some directories: nltk's inisec guard
    blocks importing any module whose file lives under the current working
    directory, and uv installs tools under $HOME, so running from ~ (or / or
    ~/.local) aborted before the CLI could start.

    That guard is disabled for this import. It exists to stop a module being
    hijacked from the current working directory (CWE-427), but it tests
    filesystem containment rather than whether the CWD is importable at all, so
    it fires on any environment installed beneath the CWD — upstream nltk#3730.
    tepub runs as an installed console script, where sys.path[0] is the script's
    own directory and the CWD is never on sys.path, so the hijack it guards
    against cannot occur here and every block it produces is a false positive.
    Without this, audiobook synthesis fails outright when run from the home
    directory. Remove once a release carrying nltk#3731 is required.
    """
    import os

    # Scoped to the import: setting it for the whole process also switched the
    # guard off for any program that imports tepub as a library. NLTK's later
    # tokenizer loads were checked from the home directory with the guard back on.
    variable = "NLTK_DISABLE_IMPORT_SECURITY"
    previous = os.environ.get(variable)
    if previous is None:
        os.environ[variable] = "1"
    try:
        import nltk
    finally:
        if previous is None:
            os.environ.pop(variable, None)

    return nltk


def ensure_punkt() -> None:
    """Ensure the Punkt sentence tokenizer data is available.

    NLTK 3.9 rerouted ``tokenizers/punkt/<lang>.pickle`` to the ``punkt_tab``
    dataset, so that is the data the tokenizer actually loads. Requiring the
    legacy ``punkt`` package as well rejected offline installations whose
    tokenizer works with ``punkt_tab`` alone.
    """
    nltk = _nltk()
    resource = "punkt_tab"
    try:
        nltk.data.find(f"tokenizers/{resource}")
    except LookupError:
        # nltk.download returns False instead of raising when it cannot fetch
        # the data (offline, blocked), which used to surface much later as a
        # LookupError in the middle of synthesis.
        if not nltk.download(resource, quiet=True):
            raise RuntimeError(
                f"NLTK's {resource} sentence data is not installed and could not be "
                f"downloaded. Install it with: python -m nltk.downloader {resource}"
            ) from None


def _punkt_tokenizer(prefix: str):
    """The Punkt tokenizer for a language, loaded once per process.

    ``nltk.data.load`` rebuilt the tokenizer and re-read its model files for
    every segment, so a book of thousands of segments parsed them thousands of
    times. The tokenizer is read-only after loading.
    """
    tokenizer = _PUNKT_TOKENIZERS.get(prefix)
    if tokenizer is None:
        nltk = _nltk()
        punkt_name = PUNKT_LANGUAGES.get(prefix, "english")
        try:
            tokenizer = nltk.data.load(f"tokenizers/punkt/{punkt_name}.pickle")
        except LookupError:
            tokenizer = nltk.data.load("tokenizers/punkt/english.pickle")
        _PUNKT_TOKENIZERS[prefix] = tokenizer
    return tokenizer


def _ensure_list_punctuation(root: lxml_html.HtmlElement) -> None:
    for li in root.xpath("//li"):
        if not li.text_content():
            continue
        # Look at the end of the whole item, not just its leading text: a full
        # stop put into li.text landed before the item's inline children, so
        # "Red <em>blue</em> green" was spoken "Red . blue green".
        if BLOCK_PUNCTUATION.search(li.text_content().rstrip()):
            continue
        # Append after the item's last text-bearing node, wherever it is.
        target: tuple[lxml_html.HtmlElement, str] | None = None
        for node in li.iter():
            if not isinstance(node.tag, str):
                continue
            if node.text and node.text.strip():
                target = (node, "text")
            if node is not li and node.tail and node.tail.strip():
                target = (node, "tail")
        if target is not None:
            node, attr = target
            setattr(node, attr, getattr(node, attr).rstrip() + ". ")


def _html_to_text(raw_html: str, element_type: str) -> str:
    root = lxml_html.fromstring(raw_html)
    if element_type in LIST_TAGS:
        _ensure_list_punctuation(root)
    _strip_non_narratable(root)
    return html.unescape(_spoken_text(root))


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


def _explicitly_a_noteref(link) -> bool:
    """True when the link itself declares that it is a note reference.

    EPUB marks note references explicitly via ``epub:type="noteref"`` or ARIA
    ``role="doc-noteref"``; publishers otherwise signal it with a class or id.
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
    return any(hint in haystack for hint in NOTEREF_HINTS)


def _fragment_target_is_note(href: str, reader, source_path: Path | None) -> bool:
    """True when ``href`` points at an element that looks like a note definition.

    A fragment alone is not evidence: a linked exponent may point at a formula
    in the same file, or at documentation elsewhere. The target is looked up
    and must carry note semantics (epub:type, role, id or class), so a real
    formula or an external citation is spoken.
    """
    if reader is None or source_path is None:
        return False
    document_part, _, fragment = href.partition("#")
    fragment = unquote(fragment)
    if not fragment:
        return False
    if document_part:
        normalized = normalize_epub_href(source_path, document_part)
        if not normalized:
            return False  # an external URL, a data URI, or a path outside the book
        target_path = Path(normalized)
    else:
        target_path = source_path
    try:
        document = reader.read_document_by_path(target_path)
    except KeyError:
        return False
    if document.tree is None:
        return False
    matches = document.tree.xpath("//*[@id=$target]", target=fragment)
    return any(_looks_like_note_definition(node) for node in matches)


def _is_noteref(link, reader=None, source_path: Path | None = None) -> bool:
    """True when an <a> carrying sup/sub is really a note reference.

    Explicit note semantics win. An unannotated superscript link is accepted
    only when its fragment target is a verified note definition; everything
    else (linked formulas, ordinals, external citations) is content and must
    survive.
    """
    if _explicitly_a_noteref(link):
        return True
    if not any(local_name(child) in ("sup", "sub") for child in link):
        return False

    href = link.get("href")
    if href is None:
        # Bare <a><sup>1</sup></a> with no target — a stripped note marker.
        return True
    # Points at a fragment, in this file ("#fn1") or a notes document
    # ("notes.xhtml#n1"); it is a note only if that target is one.
    return _fragment_target_is_note(href, reader, source_path)

def _locate(segment: Segment, reader):
    """The segment's element in the book, or None when it cannot be trusted.

    Units are found by the same enumeration that produced them, and only if the
    source text still matches; stored xpaths are not used. Located units are
    cached per document on the reader, because narration looks one up for every
    segment it holds.
    """
    document = reader.read_document_by_path(segment.file_path)
    if document.tree is None:
        return None
    cache = reader.unit_cache
    units = cache.get(segment.file_path)
    if units is None:
        units = cache[segment.file_path] = locate_units(document.tree, segment.file_path)
    entry = units.get(segment.metadata.order_in_file)
    if entry is None or entry[2] != segment.source_content:
        logger.warning(
            "Segment %s no longer matches %s; narrating its stored text",
            segment.segment_id,
            segment.file_path,
        )
        return None
    return entry[0]


def _remove_keeping_tail(node) -> None:
    """Remove ``node``, keeping the text that followed it."""
    parent = node.getparent()
    if parent is None:
        return
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


#: Subtrees that are never narrated. Their contents are code, styling or
#: scripts; the text after them still belongs to the sentence.
_NON_NARRATABLE = frozenset({"script", "style"})

#: Block-level tags, plus line breaks, that separate words when text is read.
#: itertext() joins them with nothing, so "First</li><li>Second" was spoken
#: as "FirstSecond".
_SEPARATING_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "center", "dd",
        "details", "div", "dl", "dt", "figcaption", "figure", "footer", "form", "h1",
        "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav",
        "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th",
        "thead", "tr", "ul",
    }
)


def _spoken_text(element) -> str:
    """Text with a separator at every line break and block boundary."""
    pieces: list[str] = []

    def visit(node) -> None:
        if not isinstance(node.tag, str):
            return
        boundary = local_name(node) in _SEPARATING_TAGS
        if boundary:
            pieces.append(" ")
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if boundary:
            pieces.append(" ")

    visit(element)
    return " ".join("".join(pieces).split())


def _strip_non_narratable(element) -> None:
    """Drop script and style subtrees, keeping the text after them."""
    for child in list(element):
        if not isinstance(child.tag, str):
            continue
        if local_name(child) in _NON_NARRATABLE:
            _remove_keeping_tail(child)
            continue
        _strip_non_narratable(child)


def _strip_note_definitions(element) -> None:
    """Drop marked note definitions inside a unit, keeping their tails.

    A list kept whole can hold a note definition in one of its items; checking
    only the unit and its ancestors missed it, and the note was narrated.
    """
    for child in list(element):
        if not isinstance(child.tag, str):
            continue
        if _looks_like_note_definition(child):
            _remove_keeping_tail(child)
            continue
        _strip_note_definitions(child)


def _reextract_filtered(segment: Segment, reader) -> str | None:
    """The segment's text with note references removed, or None if not located."""
    element = _locate(segment, reader)
    if element is None:
        return None
    clone = deepcopy(element)

    _strip_non_narratable(clone)
    _strip_note_definitions(clone)

    # Remove note references (a links wrapping sup or sub), and links that say
    # they are note references whatever their children. A superscript link is
    # not automatically a note: linked formulas, ordinals and citations use
    # the same markup, so an actual note-reference signal (or a verified note
    # target) is required. The text after a removed link stays.
    links = [
        node
        for node in clone.iter()
        if local_name(node) == "a"
        and (
            _explicitly_a_noteref(node)
            or any(local_name(c) in ("sup", "sub") for c in node)
        )
    ]
    for link in links:
        if not _is_noteref(link, reader, segment.file_path):
            continue
        _remove_keeping_tail(link)

    return _spoken_text(clone)


FOOTNOTE_DEF_HINTS = ("footnote", "endnote", "rearnote", "ftn", "fn", "note")
_TOKEN_SPLIT_RE = re.compile(r"[\s_\-]+")


def _looks_like_note_identifier(value: str, *, allow_bare_note: bool) -> bool:
    """True when an id/class token names a note, e.g. "ftn3", "footnote-2", "fn1".

    Token-prefix matching rather than substring: a plain ``in`` test would match
    ids like "fnord" or any class containing "note".

    A bare "note" token is accepted only where ``allow_bare_note`` says so. As an
    id ("note-3") it names a note; as a class it is the standard name for an
    admonition box ("Note: ...") in technical books, which is content.
    """
    for token in _TOKEN_SPLIT_RE.split(value.lower()):
        stripped = token.rstrip("0123456789")
        if not stripped or stripped not in FOOTNOTE_DEF_HINTS:
            continue
        if stripped == "note" and not allow_bare_note:
            continue
        return True
    return False


def _looks_like_note_definition(node) -> bool:
    """True when the element or one of its ancestors is a note definition.

    EPUB 3 marks definitions with ``epub:type="footnote"`` / ``"endnote"`` or ARIA
    ``role="doc-footnote"``; older books rely on id/class naming.
    """
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
        if _looks_like_note_identifier(get("id") or "", allow_bare_note=True):
            return True
        if _looks_like_note_identifier(get("class") or "", allow_bare_note=False):
            return True
        node = node.getparent()
    return False


def _element_is_footnote_definition(segment: Segment, reader) -> bool:
    """Check the element and its ancestors for note-definition semantics."""
    node = _locate(segment, reader)
    if node is None:
        return False
    return _looks_like_note_definition(node)


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

    # Inspect the actual element and its ancestors. Segment ids and positional
    # xpaths carry no note semantics: matching "note-" against the id dropped
    # every segment of files such as authors_note.xhtml or keynote.xhtml.
    if reader is not None and _element_is_footnote_definition(segment, reader):
        return None

    # If reader provided, re-extract with footnote filtering
    if reader is not None:
        content = _reextract_filtered(segment, reader)
        if content is None:
            # Not located again (see _locate); narrate the stored text.
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

#: Loaded Punkt tokenizers, by language prefix; see _punkt_tokenizer.
_PUNKT_TOKENIZERS: dict[str, object] = {}

# Punkt has no model for CJK, which does not separate sentences with whitespace.
# Full-width terminators, and ASCII ! and ?, always end a sentence. An ASCII full
# stop ends one only before whitespace or the end of the text: splitting on every
# "." broke 3.5 and U.S.A. into fragments, each spoken after a pause.
CJK_SENTENCE_RE = re.compile(r"(?<=[。？！…；!?])\s*|(?<=\.)(?:\s+|$)")


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
        sentences = _punkt_tokenizer(prefix).tokenize(normalized)

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
