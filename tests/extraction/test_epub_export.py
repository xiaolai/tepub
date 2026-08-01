"""Tests for EPUB structure extraction."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from exceptions import UnsafeArchiveMemberError
from extraction.epub_export import extract_epub_structure, get_epub_metadata_files


def create_test_epub(epub_path: Path) -> None:
    """Create a minimal valid EPUB for testing."""
    with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # mimetype must be first and uncompressed
        epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)

        # META-INF/container.xml
        container_xml = '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''
        epub.writestr('META-INF/container.xml', container_xml)

        # OEBPS/content.opf
        content_opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title>
    <dc:identifier id="bookid">test-123</dc:identifier>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="text1" href="text00000.html" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="text1"/>
  </spine>
</package>'''
        epub.writestr('OEBPS/content.opf', content_opf)

        # OEBPS/toc.ncx
        toc_ncx = '''<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="test-123"/>
  </head>
  <docTitle><text>Test Book</text></docTitle>
  <navMap>
    <navPoint id="navpoint-1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="text00000.html"/>
    </navPoint>
  </navMap>
</ncx>'''
        epub.writestr('OEBPS/toc.ncx', toc_ncx)

        # OEBPS/text00000.html
        html_content = '''<?xml version="1.0"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body><h1>Chapter 1</h1><p>Test content.</p></body>
</html>'''
        epub.writestr('OEBPS/text00000.html', html_content)

        # Add a style file
        css_content = 'body { font-family: serif; }'
        epub.writestr('OEBPS/styles.css', css_content)


class TestExtractEpubStructure:
    """Tests for extract_epub_structure function."""

    def test_extracts_all_files(self, tmp_path):
        """Test that all files from EPUB are extracted."""
        epub_path = tmp_path / "test.epub"
        create_test_epub(epub_path)

        output_dir = tmp_path / "extracted"
        mapping = extract_epub_structure(epub_path, output_dir)

        # Check that all expected files are in mapping
        expected_files = {
            'mimetype',
            'META-INF/container.xml',
            'OEBPS/content.opf',
            'OEBPS/toc.ncx',
            'OEBPS/text00000.html',
            'OEBPS/styles.css',
        }
        assert set(mapping.keys()) == expected_files

    def test_preserves_directory_structure(self, tmp_path):
        """Test that directory structure is preserved."""
        epub_path = tmp_path / "test.epub"
        create_test_epub(epub_path)

        output_dir = tmp_path / "extracted"
        mapping = extract_epub_structure(epub_path, output_dir, preserve_structure=True)

        # Check that files are in correct directories
        assert mapping['mimetype'] == output_dir / 'mimetype'
        assert mapping['META-INF/container.xml'] == output_dir / 'META-INF' / 'container.xml'
        assert mapping['OEBPS/content.opf'] == output_dir / 'OEBPS' / 'content.opf'

        # Verify files actually exist
        assert (output_dir / 'mimetype').exists()
        assert (output_dir / 'META-INF' / 'container.xml').exists()
        assert (output_dir / 'OEBPS' / 'content.opf').exists()

    def test_file_contents_are_correct(self, tmp_path):
        """Test that extracted files have correct contents."""
        epub_path = tmp_path / "test.epub"
        create_test_epub(epub_path)

        output_dir = tmp_path / "extracted"
        mapping = extract_epub_structure(epub_path, output_dir)

        # Check mimetype content
        mimetype_content = mapping['mimetype'].read_text()
        assert mimetype_content == 'application/epub+zip'

        # Check that HTML file contains expected text
        html_content = mapping['OEBPS/text00000.html'].read_text()
        assert 'Chapter 1' in html_content
        assert 'Test content' in html_content

        # Check CSS content
        css_content = mapping['OEBPS/styles.css'].read_text()
        assert 'font-family: serif' in css_content

    def test_creates_output_directory_if_missing(self, tmp_path):
        """Test that output directory is created if it doesn't exist."""
        epub_path = tmp_path / "test.epub"
        create_test_epub(epub_path)

        output_dir = tmp_path / "deep" / "nested" / "extracted"
        assert not output_dir.exists()

        extract_epub_structure(epub_path, output_dir)

        assert output_dir.exists()
        assert (output_dir / 'mimetype').exists()

    def test_raises_error_for_missing_epub(self, tmp_path):
        """Test that FileNotFoundError is raised for missing EPUB."""
        epub_path = tmp_path / "nonexistent.epub"
        output_dir = tmp_path / "extracted"

        with pytest.raises(FileNotFoundError):
            extract_epub_structure(epub_path, output_dir)

    def test_raises_error_for_invalid_epub(self, tmp_path):
        """Test that BadZipFile is raised for invalid EPUB."""
        epub_path = tmp_path / "invalid.epub"
        epub_path.write_text("This is not a valid EPUB file")

        output_dir = tmp_path / "extracted"

        with pytest.raises(zipfile.BadZipFile):
            extract_epub_structure(epub_path, output_dir)

    def test_flattened_extraction(self, tmp_path):
        """Test extraction without preserving directory structure."""
        epub_path = tmp_path / "test.epub"
        create_test_epub(epub_path)

        output_dir = tmp_path / "extracted"
        mapping = extract_epub_structure(epub_path, output_dir, preserve_structure=False)

        # All files should be in the root output directory
        assert mapping['mimetype'] == output_dir / 'mimetype'
        assert mapping['META-INF/container.xml'] == output_dir / 'container.xml'
        assert mapping['OEBPS/content.opf'] == output_dir / 'content.opf'
        assert mapping['OEBPS/text00000.html'] == output_dir / 'text00000.html'

        # Verify no subdirectories were created
        subdirs = [d for d in output_dir.iterdir() if d.is_dir()]
        assert len(subdirs) == 0


class TestArchiveMemberSafety:
    """Extraction must never write outside the requested output directory."""

    @pytest.mark.parametrize(
        "malicious_member",
        [
            "../escaped.txt",
            "../../escaped.txt",
            "OEBPS/../../escaped.txt",
            "/tmp/absolute-escape.txt",
        ],
    )
    def test_rejects_traversal_members(self, tmp_path, malicious_member):
        """A member escaping output_dir is refused before anything is written."""
        epub_path = tmp_path / "evil.epub"
        with zipfile.ZipFile(epub_path, "w") as epub:
            epub.writestr("mimetype", "application/epub+zip")
            epub.writestr(malicious_member, "pwned")

        output_dir = tmp_path / "extracted"

        with pytest.raises(UnsafeArchiveMemberError):
            extract_epub_structure(epub_path, output_dir)

    def test_traversal_does_not_write_outside_output_dir(self, tmp_path):
        """The escaping payload must not exist on disk after a rejected extraction."""
        epub_path = tmp_path / "evil.epub"
        with zipfile.ZipFile(epub_path, "w") as epub:
            epub.writestr("../escaped.txt", "pwned")

        output_dir = tmp_path / "extracted"
        escaped = tmp_path / "escaped.txt"

        with pytest.raises(UnsafeArchiveMemberError):
            extract_epub_structure(epub_path, output_dir)

        assert not escaped.exists()

    def test_flatten_does_not_silently_overwrite_collisions(self, tmp_path):
        """Same-named members from different directories must all survive flattening."""
        epub_path = tmp_path / "collide.epub"
        with zipfile.ZipFile(epub_path, "w") as epub:
            epub.writestr("OEBPS/a/page.html", "first")
            epub.writestr("OEBPS/b/page.html", "second")

        output_dir = tmp_path / "extracted"
        mapping = extract_epub_structure(epub_path, output_dir, preserve_structure=False)

        contents = sorted(p.read_text() for p in mapping.values())
        assert contents == ["first", "second"]
        assert len({p.name for p in mapping.values()}) == 2


class TestMetadataDiscoveryIsDeterministic:
    """Metadata discovery must not depend on mapping iteration order."""

    def test_container_match_requires_basename(self, tmp_path):
        """A file merely containing 'container.xml' in its name is not the container."""
        mapping = {
            "OEBPS/not-a-container.xml.html": tmp_path / "decoy.html",
            "META-INF/container.xml": tmp_path / "container.xml",
        }

        metadata = get_epub_metadata_files(mapping)

        assert metadata["container"] == tmp_path / "container.xml"

    def test_shallowest_opf_wins_regardless_of_order(self, tmp_path):
        """With several .opf files the shallowest is chosen, not the last iterated."""
        forward = {
            "OEBPS/deep/nested/other.opf": tmp_path / "other.opf",
            "content.opf": tmp_path / "content.opf",
        }
        reverse = dict(reversed(list(forward.items())))

        assert get_epub_metadata_files(forward)["opf"] == tmp_path / "content.opf"
        assert get_epub_metadata_files(reverse)["opf"] == tmp_path / "content.opf"


class TestGetEpubMetadataFiles:
    """Tests for get_epub_metadata_files function."""

    def test_identifies_key_metadata_files(self, tmp_path):
        """Test that key metadata files are correctly identified."""
        epub_path = tmp_path / "test.epub"
        create_test_epub(epub_path)

        output_dir = tmp_path / "extracted"
        mapping = extract_epub_structure(epub_path, output_dir)
        metadata = get_epub_metadata_files(mapping)

        # Check that all key files are identified
        assert 'mimetype' in metadata
        assert 'container' in metadata
        assert 'opf' in metadata
        assert 'ncx' in metadata

        # Verify paths are correct
        assert metadata['mimetype'] == output_dir / 'mimetype'
        assert metadata['container'] == output_dir / 'META-INF' / 'container.xml'
        assert metadata['opf'] == output_dir / 'OEBPS' / 'content.opf'
        assert metadata['ncx'] == output_dir / 'OEBPS' / 'toc.ncx'

    def test_handles_missing_files_gracefully(self, tmp_path):
        """Test that missing metadata files don't cause errors."""
        # Create mapping with only some files
        mapping = {
            'mimetype': tmp_path / 'mimetype',
            'OEBPS/text.html': tmp_path / 'OEBPS' / 'text.html',
        }

        metadata = get_epub_metadata_files(mapping)

        # Should have mimetype but not others
        assert 'mimetype' in metadata
        assert 'container' not in metadata
        assert 'opf' not in metadata
        assert 'ncx' not in metadata

    def test_case_insensitive_matching(self, tmp_path):
        """Test that file matching is case-insensitive for container.xml."""
        mapping = {
            'META-INF/Container.XML': tmp_path / 'META-INF' / 'Container.XML',
            'OEBPS/Content.OPF': tmp_path / 'OEBPS' / 'Content.OPF',
        }

        metadata = get_epub_metadata_files(mapping)

        assert 'container' in metadata
        assert 'opf' in metadata


def test_nothing_is_written_when_a_later_member_is_unsafe(tmp_path):
    """Every member is validated before any write.

    Validation used to happen inside the write loop, so a malicious member
    partway through an archive was rejected only after everything preceding it
    had already been written to disk.
    """
    epub_path = tmp_path / "evil.epub"
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr("good1.txt", "a")
        epub.writestr("good2.txt", "b")
        epub.writestr("../escape.txt", "pwned")

    output_dir = tmp_path / "extracted"

    with pytest.raises(UnsafeArchiveMemberError):
        extract_epub_structure(epub_path, output_dir)

    written = list(output_dir.rglob("*")) if output_dir.exists() else []
    assert written == []
    assert not (tmp_path / "escape.txt").exists()
