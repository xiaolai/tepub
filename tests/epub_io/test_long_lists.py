"""Long lists, tables and definition lists are split into items, and each
item's translation is placed inside it."""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from config import AppSettings
from extraction.pipeline import run_extraction
from extraction.segments import SPLIT_ABOVE_CHARS
from injection.engine import run_injection
from state.store import load_segments
from tests.epub_fixtures import long_endnotes, long_lists_epub2
from tests.epub_io.test_translated_output import _translate_everything

XHTML = "{http://www.w3.org/1999/xhtml}"


def _extract(book: Path, tmp_path: Path) -> AppSettings:
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    return settings


def test_a_long_list_becomes_one_unit_per_item(tmp_path: Path) -> None:
    settings = _extract(long_endnotes(tmp_path / "book.epub"), tmp_path)
    notes = [s for s in load_segments(settings.segments_file).segments if s.file_path.name == "notes.xhtml"]
    assert [s.metadata.element_type for s in notes].count("li") == 40
    assert max(len(s.source_content) for s in notes) < SPLIT_ABOVE_CHARS


def test_a_short_list_stays_whole(tmp_path: Path) -> None:
    from tests.epub_builder import build_epub

    book = build_epub(tmp_path / "b.epub", [("c.xhtml", "C", "<ol><li>One.</li><li>Two.</li></ol>")])
    settings = _extract(book, tmp_path)
    assert [s.metadata.element_type for s in load_segments(settings.segments_file).segments] == ["ol"]


def _tree(book: Path, name: str) -> etree._Element:
    with zipfile.ZipFile(book) as archive:
        member = next(n for n in archive.namelist() if n.endswith(name))
        return etree.fromstring(archive.read(member))


def test_bilingual_items_keep_the_list_and_hold_both_texts(tmp_path: Path) -> None:
    book = long_endnotes(tmp_path / "book.epub")
    settings = _extract(book, tmp_path)
    _translate_everything(settings)
    out = tmp_path / "out.epub"
    run_injection(settings, book, out, mode="bilingual")

    items = _tree(out, "notes.xhtml").findall(f".//{XHTML}ol/{XHTML}li")
    assert len(items) == 40  # no translated copies beside the items: numbering kept
    first = items[0]
    assert first.get("id") == "n1" and "tepub-original" not in (first.get("class") or "")
    original, translated = list(first)
    assert "tepub-original" in original.get("class") and "tepub-translation" in translated.get("class")
    assert original.find(f"{XHTML}a").get("href") == "ch1.xhtml#r1"  # backlink kept
    assert "【译】" in "".join(translated.itertext()) or translated.find(f"{XHTML}a") is not None


def test_a_dt_translation_is_inline_in_epub2(tmp_path: Path) -> None:
    book = long_lists_epub2(tmp_path / "book.epub")
    settings = _extract(book, tmp_path)
    _translate_everything(settings)
    out = tmp_path / "out.epub"
    run_injection(settings, book, out, mode="bilingual")

    term = _tree(out, "ch1.xhtml").find(f".//{XHTML}dt")
    assert [child.tag.split("}")[1] for child in term] == ["span", "br", "span"]
    rows = _tree(out, "ch1.xhtml").findall(f".//{XHTML}tr")
    assert len(rows) == 29 and all(len(row) == 2 for row in rows)  # no added cells


def test_a_list_of_links_is_split_by_the_size_of_its_markup(tmp_path: Path) -> None:
    """Little text, much markup: 60 linked entries of a few words each."""
    from tests.epub_builder import build_epub

    items = "".join(
        f'<li><a href="c.xhtml#section-number-{n}" class="toc-entry-level-two">Part {n}</a></li>'
        for n in range(60)
    )
    book = build_epub(tmp_path / "b.epub", [("c.xhtml", "C", f"<ol>{items}</ol>")])
    settings = _extract(book, tmp_path)
    units = load_segments(settings.segments_file).segments
    assert [u.metadata.element_type for u in units].count("li") == 60


def test_a_translated_copy_does_not_repeat_page_markers(tmp_path: Path) -> None:
    """A page-break marker copied into the translation marked the same printed
    page twice, as role="doc-pagebreak" with its id removed."""
    from tests.epub_builder import build_epub

    body = (
        '<p>Before the break<span id="page_v" role="doc-pagebreak" title="v"/> and '
        '<a href="#x">after</a>.</p>'
    )
    book = build_epub(tmp_path / "b.epub", [("c.xhtml", "C", body)])
    settings = _extract(book, tmp_path)
    _translate_everything(settings)
    out = tmp_path / "out.epub"
    run_injection(settings, book, out, mode="bilingual")

    tree = _tree(out, "c.xhtml")
    breaks = [n for n in tree.iter() if isinstance(n.tag, str) and n.get("role") == "doc-pagebreak"]
    assert len(breaks) == 1 and breaks[0].get("id") == "page_v"
    copy = next(p for p in tree.iter(f"{XHTML}p") if "tepub-translation" in (p.get("class") or ""))
    assert "after" in "".join(copy.itertext())


def test_a_blockquote_filled_with_text_holds_it_in_a_paragraph(tmp_path: Path) -> None:
    """<blockquote><font>…</font></blockquote>: translated, the <font> went and
    the text stood loose in the blockquote, invalid in EPUB 2."""
    from tests.epub_builder import build_epub

    book = build_epub(
        tmp_path / "b.epub",
        [("c.xhtml", "C", "<blockquote><span>ME: Um, not really, no.</span></blockquote>")],
        version=2,
    )
    settings = _extract(book, tmp_path)
    _translate_everything(settings)
    out = tmp_path / "out.epub"
    run_injection(settings, book, out, mode="bilingual")

    quotes = list(_tree(out, "c.xhtml").iter(f"{XHTML}blockquote"))
    copy = next(q for q in quotes if "tepub-translation" in (q.get("class") or ""))
    assert not (copy.text or "").strip()
    assert [child.tag for child in copy] == [f"{XHTML}p"]
    assert "Um, not really" in "".join(copy.itertext())
