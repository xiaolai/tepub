"""Named fixture books, each a small valid EPUB built for one hard case.

They are generated, so they carry no licence question, and each one passes
epubcheck as built: any error in an output is the writer's doing.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path

from tests.epub_builder import build_epub

# A 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
CSS = "body { font-family: serif; }\nblockquote { margin: 1em 2em; }\n"


def nested_blockquotes(path: Path) -> Path:
    body = """<section epub:type="chapter"><h1>Quotes</h1>
<blockquote><p>A quoted paragraph.</p></blockquote>
<blockquote>Outer words <blockquote>Inner words</blockquote> and more.</blockquote>
<p>An ordinary paragraph with <em>emphasis</em> and <a href="#q">a link</a>.</p>
<p id="q">Target.</p></section>"""
    return build_epub(path, [("ch1.xhtml", "Quotes", body)], css=CSS)


def switch_and_mathml(path: Path) -> Path:
    # epub:switch is deprecated (epubcheck warns) but common in older books, and
    # its prefixed name crashed the export when stored in an xpath.
    body = """<h1>Maths</h1>
<p>Inline <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>+</mo><mn>1</mn></math> here.</p>
<epub:switch id="sw1"><epub:case required-namespace="http://www.w3.org/1998/Math/MathML">
<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>y</mi></math></epub:case>
<epub:default><p>y</p></epub:default></epub:switch>
<p>After the formula.</p>"""
    return build_epub(
        path, [("ch1.xhtml", "Maths", body)], properties={"ch1.xhtml": "mathml switch"}
    )


def svg_cover(path: Path) -> Path:
    cover = """<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
 version="1.1" viewBox="0 0 600 800" preserveAspectRatio="xMidYMid meet" width="100%" height="100%">
<image width="600" height="800" xlink:href="cover.png"/></svg></div>"""
    return build_epub(
        path,
        [("cover.xhtml", "Cover", cover), ("ch1.xhtml", "One", "<h1>One</h1><p>Text.</p>")],
        resources={"cover.png": PNG},
        properties={"cover.xhtml": "svg"},
    )


def footnotes_with_backlinks(path: Path) -> Path:
    chapter = """<h1>Chapter</h1>
<p>A claim<a epub:type="noteref" id="r1" href="notes.xhtml#n1"><sup>1</sup></a> and another<a epub:type="noteref" id="r2" href="notes.xhtml#n2"><sup>2</sup></a>.</p>"""
    notes = """<h1>Notes</h1><section epub:type="endnotes" role="doc-endnotes"><ol>
<li id="n1"><p>First note. <a href="ch1.xhtml#r1" role="doc-backlink">Back</a></p></li>
<li id="n2"><p>Second note. <a href="ch1.xhtml#r2" role="doc-backlink">Back</a></p></li>
</ol></section>"""
    return build_epub(path, [("ch1.xhtml", "Chapter", chapter), ("notes.xhtml", "Notes", notes)])


def epub2_with_ncx(path: Path) -> Path:
    return build_epub(
        path,
        [
            ("text/ch1.xhtml", "One", "<h1>One</h1><p>First chapter text.</p>"),
            ("text/ch2.xhtml", "Two", "<h1>Two</h1><p>Second chapter text.</p>"),
        ],
        version=2,
        css=CSS,
    )


def same_basename(path: Path) -> Path:
    return build_epub(
        path,
        [
            ("part1/chapter.xhtml", "Part One", "<h1>Part One</h1><p>Alpha.</p>"),
            ("part2/chapter.xhtml", "Part Two", "<h1>Part Two</h1><p>Beta.</p>"),
        ],
    )


def vertical_cjk(path: Path) -> Path:
    css = "html { writing-mode: vertical-rl; -epub-writing-mode: vertical-rl; }\n"
    body = "<h1>第一章</h1><p>天地玄黄，宇宙洪荒。</p><p>日月盈昃，辰宿列张。</p>"
    return build_epub(
        path, [("ch1.xhtml", "第一章", body)], title="竖排", lang="zh", css=css, page_direction="rtl"
    )


FIXTURES: dict[str, Callable[[Path], Path]] = {
    "nested_blockquotes": nested_blockquotes,
    "switch_and_mathml": switch_and_mathml,
    "svg_cover": svg_cover,
    "footnotes_with_backlinks": footnotes_with_backlinks,
    "epub2_with_ncx": epub2_with_ncx,
    "same_basename": same_basename,
    "vertical_cjk": vertical_cjk,
}
