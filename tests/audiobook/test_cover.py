from pathlib import Path

import pytest
from PIL import Image

from audiobook.cover import CoverImageError, _prepare_cover
from epub_io.path_utils import normalize_epub_href


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


def test_unreadable_explicit_cover_fails_instead_of_vanishing(tmp_path):
    src = tmp_path / "cover.jpg"
    src.write_text("not an image")

    with pytest.raises(CoverImageError):
        _prepare_cover(tmp_path / "output", reader=None, explicit_cover=src)


def test_a_truncated_detected_cover_falls_back_to_none(tmp_path):
    """Image.open reads only headers; truncating the pixel data raised later,
    outside the guard, and aborted assembly."""
    from io import BytesIO
    from types import SimpleNamespace

    saved = BytesIO()
    Image.new("RGB", (64, 64), color="navy").save(saved, format="PNG")
    data = saved.getvalue()
    truncated = data[: len(data) // 2]

    item = SimpleNamespace(is_image=True, href=Path("images/cover.png"))
    reader = SimpleNamespace(
        package=SimpleNamespace(cover_item=lambda: None),
        item_for=lambda _item: None,
        iter_documents=lambda: iter(()),
        items=lambda: [item],
        read_bytes=lambda _item: truncated,
    )

    assert _prepare_cover(tmp_path / "output", reader) is None


def test_a_transparent_palette_cover_keeps_its_transparency(tmp_path):
    """Converted to RGB and saved as JPEG, transparent pixels showed their
    palette colour."""
    src = tmp_path / "palette.png"
    image = Image.new("P", (8, 8))
    image.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    image.save(src, format="PNG", transparency=0)

    result = _prepare_cover(tmp_path / "output", reader=None, explicit_cover=src)

    assert result is not None and result.suffix == ".png"
    assert Image.open(result).mode == "RGBA"
