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
    added = _error_counts(epubcheck.check(out)) - _error_counts(epubcheck.check(book))
    assert not added, {f.code: f.message for f in epubcheck.errors(epubcheck.check(out))}
