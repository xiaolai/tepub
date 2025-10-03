"""Tests for EPUB structure extraction."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

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
