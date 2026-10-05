"""Content documents are parsed as XML and written back faithfully.

lxml's HTML parser lowercased SVG names (viewBox became viewbox, breaking SVG
covers), turned prefixed names such as epub:switch into literal tag names that
no XPath could address, and dropped the XHTML 1.1 DOCTYPE.
"""

from __future__ import annotations

import pytest
from lxml import etree

from epub_io.xhtml import NotWellFormed, parse_xhtml, serialize_xhtml

SVG_DOC = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="fr" lang="fr">
<head><title>T</title></head>
<body>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" preserveAspectRatio="xMidYMid"><linearGradient id="g"/></svg>
<epub:switch id="s"><epub:default><p>y</p></epub:default></epub:switch>
<p>Text<br/>more</p>
</body>
</html>
"""


def test_svg_and_prefixed_names_survive_a_round_trip() -> None:
    doc = parse_xhtml(SVG_DOC)
    out = serialize_xhtml(doc)
    assert b"viewBox" in out and b"preserveAspectRatio" in out and b"linearGradient" in out
    assert b"<epub:switch" in out
    assert b'xml:lang="fr"' in out
    etree.fromstring(out)


def test_an_unchanged_document_serialises_to_equivalent_xml() -> None:
    doc = parse_xhtml(SVG_DOC)
    again = parse_xhtml(serialize_xhtml(doc))
    assert etree.tostring(again.root, method="c14n") == etree.tostring(doc.root, method="c14n")


def test_the_xhtml_1_1_doctype_is_kept() -> None:
    data = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
        b'"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head>'
        b"<body><p>x</p></body></html>"
    )
    out = serialize_xhtml(parse_xhtml(data))
    assert b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"' in out
    assert out.startswith(b"<?xml")


@pytest.mark.parametrize(
    ("entity", "char"), [(b"&nbsp;", " "), (b"&eacute;", "é"), (b"&mdash;", "—")]
)
def test_html_named_entities_are_understood(entity: bytes, char: str) -> None:
    data = SVG_DOC.replace(b"<p>Text<br/>more</p>", b"<p>a" + entity + b"b</p>")
    doc = parse_xhtml(data)
    assert f"a{char}b" in "".join(doc.root.itertext())


def test_escaped_markup_stays_escaped() -> None:
    data = SVG_DOC.replace(b"<p>Text<br/>more</p>", b"<p>&lt;b&gt; &amp;nbsp; R&amp;D</p>")
    text = "".join(parse_xhtml(data).root.itertext())
    assert "<b> &nbsp; R&D" in text


def test_entities_inside_cdata_and_comments_are_left_alone() -> None:
    data = SVG_DOC.replace(
        b"<p>Text<br/>more</p>", b"<!-- &nbsp; --><p><![CDATA[&nbsp;]]></p>"
    )
    doc = parse_xhtml(data)
    assert "&nbsp;" in "".join(doc.root.itertext())
    assert b"<!-- &nbsp; -->" in serialize_xhtml(doc)


def test_external_entities_are_never_resolved(tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    data = (
        b'<?xml version="1.0"?>\n<!DOCTYPE html [<!ENTITY xxe SYSTEM "file://'
        + str(secret).encode()
        + b'">]>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head>'
        b"<body><p>&xxe;</p></body></html>"
    )
    try:
        doc = parse_xhtml(data)
    except NotWellFormed:
        return  # refusing the document is also safe
    assert b"TOP SECRET" not in serialize_xhtml(doc)
    assert "TOP SECRET" not in "".join(doc.root.itertext())


@pytest.mark.parametrize(
    "broken",
    [
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Line<br>break</p></body></html>',
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>R&D</p></body></html>',
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>unclosed</body></html>',
    ],
)
def test_a_malformed_document_is_reported_not_repaired(broken: bytes) -> None:
    with pytest.raises(NotWellFormed):
        parse_xhtml(broken)
