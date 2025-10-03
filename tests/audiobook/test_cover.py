from pathlib import Path

from PIL import Image

from epub_io.path_utils import normalize_epub_href
from audiobook.assembly import _prepare_cover


def test_normalize_relative_image_path():
    doc = Path("Text/chapter1.xhtml")
    assert normalize_epub_href(doc, "images/cover.jpg") == "Text/images/cover.jpg"


def test_normalize_parent_directory():
    doc = Path("Text/chapter1.xhtml")
    assert normalize_epub_href(doc, "../Images/cover.jpg") == "Images/cover.jpg"


def test_normalize_rooted_path():
    doc = Path("Text/chapter1.xhtml")
    assert normalize_epub_href(doc, "/Images/cover.jpg") == "Images/cover.jpg"


def test_normalize_rejects_data_uri():
    doc = Path("Text/chapter1.xhtml")
    assert normalize_epub_href(doc, "data:image/png;base64,abc") is None


def test_normalize_rejects_remote_url():
    doc = Path("Text/chapter1.xhtml")
    assert normalize_epub_href(doc, "https://example.com/cover.jpg") is None


def test_prepare_cover_keeps_dimensions(tmp_path):
    src = tmp_path / "source.png"
    Image.new("RGB", (320, 180), color="navy").save(src, format="PNG")
    output_root = tmp_path / "output"
    result = _prepare_cover(output_root, reader=None, explicit_cover=src)
    assert result is not None
    saved = Image.open(result)
    assert saved.size == (320, 180)
