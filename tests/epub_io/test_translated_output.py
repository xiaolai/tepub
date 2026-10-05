"""Translated books must be valid EPUBs: the gate that actually translates.

Extraction, a deterministic fake translation, and injection run over every
fixture in both output modes, and epubcheck must report no more occurrences of
any error than the source book had. An unchanged round trip proves nothing
about segmentation or injection.
"""

from __future__ import annotations

import collections
import json
import zipfile
from pathlib import Path

import pytest

from config import AppSettings
from extraction.pipeline import run_extraction
from injection.engine import run_injection
from tests import epubcheck
from tests.epub_fixtures import FIXTURES

pytestmark = pytest.mark.skipif(not epubcheck.AVAILABLE, reason="epubcheck not installed")

# Fixtures that the parts of the new core not yet built still break, by reason.
# Strict: when a fix lands, its entries must be removed here.
PENDING: dict[tuple[str, str], str] = {
}


def _translate_everything(settings: AppSettings) -> None:
    segments = json.loads(settings.segments_file.read_text(encoding="utf-8"))["segments"]
    state = json.loads(settings.state_file.read_text(encoding="utf-8"))
    for segment in segments:
        text = segment["source_content"]
        translation = text if segment["extract_mode"] == "html" else f"【译】{text}"
        state["segments"][segment["segment_id"]] = {
            "segment_id": segment["segment_id"],
            "translation": translation,
            "provider_name": "fake",
            "model_name": "fake",
            "status": "completed",
            "error_message": None,
        }
    settings.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _head_of(data: bytes) -> list[str]:
    """The <title> and every <link> of a document, as a comparable list."""
    from lxml import etree

    root = etree.fromstring(data)
    head = root.find("{http://www.w3.org/1999/xhtml}head")
    if head is None:
        return []
    return [
        etree.tostring(child, method="c14n").decode()
        for child in head
        if isinstance(child.tag, str) and etree.QName(child).localname in ("title", "link")
    ]


def _assert_heads_kept(source: Path, output: Path) -> None:
    """epubcheck does not flag a lost stylesheet link; this does."""
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(output) as out:
        for name in src.namelist():
            if name.endswith(".xhtml") and not name.endswith("nav.xhtml"):
                assert _head_of(out.read(name)) == _head_of(src.read(name)), name


def _links_resolve(output: Path) -> list[str]:
    """In-book links whose target file or anchor does not exist in the output."""
    import posixpath
    from urllib.parse import unquote

    from lxml import etree

    with zipfile.ZipFile(output) as archive:
        documents = {
            name: etree.fromstring(archive.read(name))
            for name in archive.namelist()
            if name.endswith(".xhtml")
        }
    ids = {
        name: {e.get("id") for e in root.iter() if isinstance(e.tag, str) and e.get("id")}
        for name, root in documents.items()
    }
    broken = []
    for name, root in documents.items():
        for link in root.iter("{http://www.w3.org/1999/xhtml}a"):
            href = link.get("href") or ""
            if not href or "://" in href or href.startswith("mailto:"):
                continue
            path, _, fragment = href.partition("#")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(path))) if path else name
            if target not in ids or (fragment and fragment not in ids[target]):
                broken.append(f"{name}: {href}")
    return broken


def _noterefs(output: Path) -> int:
    with zipfile.ZipFile(output) as archive:
        text = "".join(
            archive.read(n).decode("utf-8") for n in archive.namelist() if n.endswith(".xhtml")
        )
    return text.count('epub:type="noteref"')


def _error_counts(findings) -> collections.Counter:
    return collections.Counter(f.code for f in epubcheck.errors(findings))


CASES = [(name, mode) for name in sorted(FIXTURES) for mode in ("bilingual", "translated_only")]


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        pytest.param(n, m, marks=pytest.mark.xfail(strict=True, reason=PENDING[(n, m)]))
        if (n, m) in PENDING
        else (n, m)
        for n, m in CASES
    ],
)
def test_translated_output_adds_no_epubcheck_errors(name: str, mode: str, tmp_path: Path) -> None:
    book = FIXTURES[name](tmp_path / f"{name}.epub")
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    _translate_everything(settings)
    out = tmp_path / f"out-{mode}.epub"

    updated, _ = run_injection(settings, book, out, mode=mode)

    assert updated, "nothing was injected"
    _assert_heads_kept(book, out)
    assert _links_resolve(out) == []
    # Footnote references survive translation in both modes; translated-only
    # output used to replace each paragraph with bare text, dropping them.
    assert _noterefs(out) >= _noterefs(book)
    added = _error_counts(epubcheck.check(out)) - _error_counts(epubcheck.check(book))
    assert not added, {f.code: f.message for f in epubcheck.errors(epubcheck.check(out))}


CORPUS = __import__("os").environ.get("TEPUB_CORPUS_DIR")


def _corpus_books() -> list[Path]:
    if not CORPUS:
        return []
    return sorted(Path(CORPUS).expanduser().glob("*.epub"))


@pytest.mark.corpus
@pytest.mark.skipif(not CORPUS, reason="set TEPUB_CORPUS_DIR to a folder of real EPUBs")
@pytest.mark.parametrize("mode", ["bilingual", "translated_only"])
@pytest.mark.parametrize("book", _corpus_books(), ids=lambda p: p.stem[:40])
def test_real_books_translate_into_valid_epubs(book: Path, mode: str, tmp_path: Path) -> None:
    settings = AppSettings(work_dir=tmp_path / "work")
    run_extraction(settings, book)
    _translate_everything(settings)
    out = tmp_path / f"out-{mode}.epub"

    run_injection(settings, book, out, mode=mode)

    _assert_heads_kept(book, out)
    added = _error_counts(epubcheck.check(out)) - _error_counts(epubcheck.check(book))
    assert not added, dict(added)
