"""Translated books must be valid EPUBs: the gate that actually translates.

Extraction, a deterministic fake translation, and injection run over every
fixture in both output modes, and epubcheck must report no more occurrences of
any error than the source book had. An unchanged round trip proves nothing
about segmentation or injection.
"""

from __future__ import annotations

import collections
import json
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
    ("epub2_with_ncx", "bilingual"): "HTML parser drops the XHTML 1.1 DOCTYPE and head title (WI-4.2)",
    ("epub2_with_ncx", "translated_only"): "HTML parser drops the XHTML 1.1 DOCTYPE and head title (WI-4.2)",
    ("nested_blockquotes", "bilingual"): "translated copies repeat ids (WI-4.6, D5)",
    ("switch_and_mathml", "bilingual"): "epub: prefix in a stored xpath cannot be evaluated (WI-4.2, WI-4.4)",
    ("switch_and_mathml", "translated_only"): "epub: prefix in a stored xpath cannot be evaluated (WI-4.2, WI-4.4)",
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
    added = _error_counts(epubcheck.check(out)) - _error_counts(epubcheck.check(book))
    assert not added, {f.code: f.message for f in epubcheck.errors(epubcheck.check(out))}
