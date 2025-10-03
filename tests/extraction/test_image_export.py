from pathlib import Path
from types import SimpleNamespace

from extraction.image_export import (
    _is_image_item,
    _is_potential_cover,
    extract_images,
    get_image_mapping,
)


def test_is_image_item():
    # Mock item with media_type
    item_image = SimpleNamespace(media_type="image/jpeg", file_name="test.jpg")
    assert _is_image_item(item_image) is True

    item_text = SimpleNamespace(media_type="text/html", file_name="test.html")
    assert _is_image_item(item_text) is False

    # Mock item with only filename
    item_png = SimpleNamespace(file_name="image.png")
    assert _is_image_item(item_png) is True

    item_txt = SimpleNamespace(file_name="readme.txt")
    assert _is_image_item(item_txt) is False


def test_is_potential_cover():
    assert _is_potential_cover(Path("images/cover.jpg"), False) is True
    assert _is_potential_cover(Path("images/title.png"), False) is True
    assert _is_potential_cover(Path("images/first.jpg"), True) is True
    assert _is_potential_cover(Path("images/diagram.jpg"), False) is False


def test_extract_images(tmp_path):
    """Test image extraction with mock EPUB."""
    # Create mock items
    mock_items = [
        SimpleNamespace(
            media_type="image/jpeg",
            file_name="images/cover.jpg",
            get_content=lambda: b"fake-jpg-data",
        ),
        SimpleNamespace(
            media_type="image/png",
            file_name="images/fig1.png",
            get_content=lambda: b"fake-png-data",
        ),
        SimpleNamespace(
            media_type="text/html",
            file_name="chapter.html",
            get_content=lambda: b"<html></html>",
        ),
    ]

    # Mock book
    mock_book = SimpleNamespace(get_items=lambda: mock_items)

    # Mock reader
    mock_reader = SimpleNamespace(book=mock_book)

    # Mock settings
    settings = SimpleNamespace()

    # Patch EpubReader to return mock
    from extraction import image_export

    original_reader = image_export.EpubReader

    def mock_epub_reader(epub_path, settings):
        return mock_reader

    image_export.EpubReader = mock_epub_reader

    try:
        # Extract images
        output_dir = tmp_path / "images"
        epub_path = tmp_path / "test.epub"
        extracted = extract_images(settings, epub_path, output_dir)

        # Verify results
        assert len(extracted) == 2
        assert (output_dir / "cover.jpg").exists()
        assert (output_dir / "fig1.png").exists()

        # Verify cover candidate detection
        cover_candidates = [img for img in extracted if img.is_cover_candidate]
        assert len(cover_candidates) >= 1
        assert any("cover" in img.epub_path.name for img in cover_candidates)

    finally:
        image_export.EpubReader = original_reader


def test_get_image_mapping():
    from extraction.image_export import ImageInfo

    images = [
        ImageInfo(
            epub_path=Path("images/cover.jpg"),
            extracted_path=Path("/tmp/cover.jpg"),
            is_cover_candidate=True,
        ),
        ImageInfo(
            epub_path=Path("images/fig1.png"),
            extracted_path=Path("/tmp/fig1.png"),
            is_cover_candidate=False,
        ),
    ]

    mapping = get_image_mapping(images)

    assert mapping["images/cover.jpg"] == "cover.jpg"
    assert mapping["images/fig1.png"] == "fig1.png"
