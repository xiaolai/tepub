from pathlib import Path, PurePosixPath

from ebooklib import epub

from epub_io import writer


def test_write_updated_epub_updates_toc(monkeypatch, tmp_path):
    book = epub.EpubBook()
    html_item = epub.EpubHtml(uid="ch1", file_name="Text/ch1.xhtml", content="<h1 id='t'>Original</h1>")
    book.add_item(html_item)
    book.spine = [html_item]
    book.toc = [epub.Link("Text/ch1.xhtml#t", "Original", "ch1")]

    monkeypatch.setattr(writer, "load_book", lambda path: book)
    monkeypatch.setattr(
        writer,
        "get_item_by_href",
        lambda b, href: html_item,
    )

    updated_html = {Path("Text/ch1.xhtml"): "<h1 id='t'>Título</h1>".encode("utf-8")}
    toc_updates = {PurePosixPath("Text/ch1.xhtml"): {"t": "Título", None: "Título"}}

    output_path = tmp_path / "out.epub"
    writer.write_updated_epub(
        tmp_path / "in.epub",
        output_path,
        updated_html,
        toc_updates=toc_updates,
        css_mode="translated_only",
    )

    assert book.toc[0].title == "Título"


def test_write_updated_epub_injects_translated_only_css(monkeypatch, tmp_path):
    book = epub.EpubBook()
    html_item = epub.EpubHtml(uid="ch1", file_name="Text/ch1.xhtml", content="<p data-lang='original'>Hi</p>")
    css_item = epub.EpubItem(
        uid="style",
        file_name="Styles/style.css",
        media_type="text/css",
        content="body { font-family: serif; }".encode("utf-8"),
    )
    book.add_item(html_item)
    book.add_item(css_item)
    book.spine = [html_item]
    book.toc = [epub.Link("Text/ch1.xhtml", "Original", "ch1")]

    monkeypatch.setattr(writer, "load_book", lambda path: book)
    monkeypatch.setattr(
        writer,
        "get_item_by_href",
        lambda b, href: html_item,
    )

    updated_html = {Path("Text/ch1.xhtml"): "<p data-lang='translation'>你好</p>".encode("utf-8")}

    output_path = tmp_path / "out.epub"
    writer.write_updated_epub(
        tmp_path / "in.epub",
        output_path,
        updated_html,
        css_mode="translated_only",
    )

    css_content = css_item.get_content().decode("utf-8")
    assert "[data-lang=\"original\"]" in css_content
