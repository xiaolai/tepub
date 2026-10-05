from pathlib import Path
from types import SimpleNamespace

from extraction.image_export import (
    _is_potential_cover,
    extract_images,
    get_image_mapping,
)
from tests.epub_builder import build_epub


def test_is_potential_cover():
    assert _is_potential_cover(Path("images/cover.jpg"), False) is True
    assert _is_potential_cover(Path("images/title.png"), False) is True
    assert _is_potential_cover(Path("images/first.jpg"), True) is True
    assert _is_potential_cover(Path("images/diagram.jpg"), False) is False


def test_extract_images(tmp_path):
    """Images are extracted from a real EPUB; documents are not."""
    epub_path = build_epub(
        tmp_path / "test.epub",
        [("chapter.xhtml", "One", "<p>Text.</p>")],
        resources={"images/cover.jpg": b"fake-jpg-data", "images/fig1.png": b"fake-png-data"},
    )
    output_dir = tmp_path / "images"

    extracted = extract_images(SimpleNamespace(), epub_path, output_dir)

    assert sorted(img.extracted_path.name for img in extracted) == ["cover.jpg", "fig1.png"]
    assert (output_dir / "cover.jpg").read_bytes() == b"fake-jpg-data"
    cover_candidates = [img for img in extracted if img.is_cover_candidate]
    assert any("cover" in img.epub_path.name for img in cover_candidates)


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
