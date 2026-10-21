"""The web viewer's original/translation toggles work for every EPUB version.

EPUB 2 output is marked only with tepub-original and tepub-translation classes
(XHTML 1.1 has no data-* attributes), and the web export stripped every class,
so its toggles matched nothing for EPUB 2 books.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import html as lxml_html

from config import AppSettings
from extraction.pipeline import run_extraction
from state.store import load_segments
from tests.epub_builder import build_epub
from webbuilder import export_web


@pytest.mark.parametrize("version", [2, 3])
def test_web_export_keeps_markers_and_language(tmp_path: Path, version: int) -> None:
    book = build_epub(
        tmp_path / "b.epub",
        [("ch1.xhtml", "One", "<p>Hello there.</p>")],
        version=version,
        lang="en",
    )
    settings = AppSettings(work_dir=tmp_path / "w")
    run_extraction(settings, book)
    state = json.loads(settings.state_file.read_text(encoding="utf-8"))
    for segment in load_segments(settings.segments_file).segments:
        state["segments"][segment.segment_id].update(
            translation="你好。", provider_name="fake", status="completed"
        )
    settings.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    web = export_web(settings, book, output_dir=tmp_path / "web").site

    page = next((web / "content").rglob("ch1.xhtml")).read_text(encoding="utf-8")
    tree = lxml_html.fromstring(page)
    original = tree.xpath(".//p[contains(@class, 'tepub-original')]")
    translation = tree.xpath(".//p[contains(@class, 'tepub-translation')]")
    assert len(original) == 1 and len(translation) == 1
    # The root element's own xml:lang="en" made the old substring assertion
    # pass even when both marked paragraphs carried no language at all.
    assert (original[0].get("lang"), original[0].get("xml:lang")) == ("en", "en")
    assert (translation[0].get("lang"), translation[0].get("xml:lang")) == ("zh-CN", "zh-CN")
