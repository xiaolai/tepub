from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterator
from copy import deepcopy
from itertools import count
from pathlib import Path

from lxml import html

from extraction.cleaners import normalize_punctuation
from state.models import ExtractMode, Segment, SegmentMetadata

SIMPLE_TAGS = {"p", "blockquote", "div", *{f"h{i}" for i in range(1, 7)}}
ATOMIC_TAGS = {"ul", "ol", "dl", "table", "figure"}

# Tags that require smart extraction (skip structural wrappers)
# For these tags, only extract if element has text at its own level
# or is a leaf node (no same-tag descendants)
SMART_EXTRACT_TAGS = {"blockquote", "div"}

BLOCK_LEVEL_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "figure",
    "figcaption",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}


def _normalize_tag(tag: str) -> str:
    return tag.split("}")[-1].lower()


def _has_atomic_ancestor(element: html.HtmlElement) -> bool:
    parent = element.getparent()
    while parent is not None:
        if _normalize_tag(parent.tag) in ATOMIC_TAGS:
            return True
        parent = parent.getparent()
    return False


def _div_is_text_only(element: html.HtmlElement) -> bool:
    if _normalize_tag(element.tag) != "div":
        return False
    return not any(
        _normalize_tag(child.tag) in BLOCK_LEVEL_TAGS
        for child in element
        if isinstance(child.tag, str)
    )


def _contains_descendant_with_tag(element: html.HtmlElement, tag: str) -> bool:
    """Check if element contains any descendant with the specified tag.

    Used to identify leaf nodes in nested same-tag structures.

    Args:
        element: Element to check
        tag: Tag name to search for (normalized)

    Returns:
        True if any descendant has the specified tag
    """
    for descendant in element.iter():
        if descendant is element:  # Skip self
            continue
        if isinstance(descendant.tag, str) and _normalize_tag(descendant.tag) == tag:
            return True
    return False


def _has_direct_text_content(element: html.HtmlElement, exclude_tag: str) -> bool:
    """Check if element has text content at its own level.

    Returns True if element would still have text after removing all
    same-tag descendants. This identifies elements with meaningful content
    vs. pure structural wrappers.

    Example:
        <blockquote>
          Own text here             ← has direct text: True
          <blockquote>Child</blockquote>
        </blockquote>

        <blockquote>
          <blockquote>Only child</blockquote>
        </blockquote>                ← has direct text: False

    Args:
        element: Element to check
        exclude_tag: Tag to exclude when checking text (normalized)

    Returns:
        True if element has text outside same-tag descendants
    """
    # Clone to avoid modifying original
    clone = deepcopy(element)

    # Remove all same-tag descendants. lxml's remove() also drops the element's
    # tail, which is text belonging to the *parent* — losing it made wrappers with
    # meaningful text after a nested element look like pure structural wrappers.
    for desc in list(clone.iter()):
        if desc is clone:
            continue
        if isinstance(desc.tag, str) and _normalize_tag(desc.tag) == exclude_tag:
            # Remove the descendant
            parent = desc.getparent()
            if parent is not None:
                tail = desc.tail
                if tail:
                    previous = desc.getprevious()
                    if previous is not None:
                        previous.tail = (previous.tail or "") + tail
                    else:
                        parent.text = (parent.text or "") + tail
                parent.remove(desc)

    # Check if remaining element has any text
    text = clone.text_content().strip()
    return bool(text)


def _extract_text(element: html.HtmlElement) -> str:
    text = " ".join(element.text_content().split())
    return normalize_punctuation(text)


def _clean_html_copy(element: html.HtmlElement) -> html.HtmlElement:
    clone = html.fromstring(html.tostring(element, encoding="unicode"))
    # Remove all attributes (id, class, style, etc.) except src for images
    for node in clone.iter():
        if isinstance(node.tag, str):
            tag = _normalize_tag(node.tag)
            if tag in {"img", "image"}:
                # Preserve src attribute for images
                src = (
                    node.get("src")
                    or node.get("href")
                    or node.get("{http://www.w3.org/1999/xlink}href")
                )
                node.attrib.clear()
                if src:
                    node.set("src", src)
            else:
                node.attrib.clear()
    for bad_tag in list(clone.iter()):
        if isinstance(bad_tag.tag, str) and bad_tag.tag.lower() in {"span", "a", "font"}:
            bad_tag.drop_tag()
    return clone


def _extract_inner_html(element: html.HtmlElement) -> str:
    clone = _clean_html_copy(element)
    parts: list[str] = [clone.text or ""]
    for child in clone:
        if isinstance(child.tag, str):
            parts.append(html.tostring(child, encoding="unicode"))
        else:
            # Comments and processing instructions have a callable tag. Serializing
            # them keeps their tail text; yielding the node itself used to reach
            # "".join() as a non-string and raise TypeError, aborting extraction.
            parts.append(html.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


def _build_segment_id(file_path: Path, xpath: str) -> str:
    """Legacy segment id: file stem plus a hash of the xpath alone.

    Deliberately unchanged. It can collide — two files with the same basename in
    different directories produce the same id for the same xpath — but rewriting
    the scheme outright would re-key every existing workspace and strand all
    completed translations and synthesised audio. Collisions are instead resolved
    after extraction by resolve_segment_id_collisions(), so the overwhelming
    majority of segments keep the id they already have.
    """
    digest = hashlib.sha1(xpath.encode()).hexdigest()[:12]
    return f"{file_path.stem}-{digest}"


def _disambiguated_segment_id(file_path: Path, xpath: str) -> str:
    """Collision-safe id, derived from the full EPUB-relative path and the xpath."""
    payload = f"{file_path.as_posix()}\0{xpath}"
    digest = hashlib.sha1(payload.encode()).hexdigest()[:12]
    return f"{file_path.stem}-v2-{digest}"


def resolve_segment_id_collisions(segments: list[Segment]) -> tuple[list[Segment], list[str]]:
    """Give every member of a colliding id group a unique, path-derived id.

    Segments whose legacy id is already unique are returned untouched, so an
    existing workspace still matches after re-extraction. Only the segments that
    were genuinely ambiguous — and whose shared state record was therefore
    already corrupt — are re-keyed.

    Returns the segments and the list of legacy ids that had to be replaced.
    """
    by_id: dict[str, list[Segment]] = defaultdict(list)
    for segment in segments:
        by_id[segment.segment_id].append(segment)

    colliding = {sid: group for sid, group in by_id.items() if len(group) > 1}
    if not colliding:
        return segments, []

    for group in colliding.values():
        for segment in group:
            segment.segment_id = _disambiguated_segment_id(segment.file_path, segment.xpath)

    # Two segments sharing both file path and xpath cannot be told apart at all;
    # that is an extraction defect, not a naming collision.
    final_ids = [segment.segment_id for segment in segments]
    if len(set(final_ids)) != len(final_ids):
        duplicates = {sid for sid in final_ids if final_ids.count(sid) > 1}
        raise ValueError(
            f"Segment ids still collide after disambiguation: {sorted(duplicates)}. "
            f"Two segments share both file path and xpath."
        )

    return segments, sorted(colliding)


def iter_segments(
    tree: html.HtmlElement,
    file_path: Path,
    spine_index: int,
) -> Iterator[Segment]:
    order_counter = count(1)
    root_tree = tree.getroottree()
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        tag = _normalize_tag(element.tag)
        if tag in ATOMIC_TAGS:
            if _has_atomic_ancestor(element):
                continue
            extract_mode = ExtractMode.HTML
        elif tag in SIMPLE_TAGS:
            # A simple element inside an atomic one is already carried by that
            # atomic element's HTML segment; extracting it again duplicates the
            # content and produces overlapping injections.
            if _has_atomic_ancestor(element):
                continue
            if tag == "div" and not _div_is_text_only(element):
                continue

            # Smart extraction for tags that commonly have nested structures
            # Skip if element is just a structural wrapper (no own text, only descendants)
            if tag in SMART_EXTRACT_TAGS:
                has_same_tag_descendants = _contains_descendant_with_tag(element, tag)
                if has_same_tag_descendants:
                    # Has nested same-tag elements: only extract if this level has own text
                    if not _has_direct_text_content(element, tag):
                        continue  # Skip - pure structural wrapper

            extract_mode = ExtractMode.TEXT
        else:
            continue

        xpath = root_tree.getpath(element)
        segment_id = _build_segment_id(file_path, xpath)

        if extract_mode == ExtractMode.TEXT:
            content = _extract_text(element)
            if not content:
                continue
        else:
            content = _extract_inner_html(element)
            if not content:
                continue

        # Take the order number only once the element is known to yield a segment;
        # advancing it earlier left gaps and could leave no segment at order 1.
        order_idx = next(order_counter)

        metadata = SegmentMetadata(
            element_type=tag,
            spine_index=spine_index,
            order_in_file=order_idx,
        )

        yield Segment(
            segment_id=segment_id,
            file_path=file_path,
            xpath=xpath,
            extract_mode=extract_mode,
            source_content=content,
            metadata=metadata,
        )
