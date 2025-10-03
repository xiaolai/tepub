"""Extract complete EPUB internal structure to workspace."""

from __future__ import annotations

import zipfile
from pathlib import Path

from console_singleton import get_console

console = get_console()


def extract_epub_structure(
    input_epub: Path,
    output_dir: Path,
    preserve_structure: bool = True,
) -> dict[str, Path]:
    """
    Extract all files from EPUB maintaining directory structure.

    This function extracts the complete internal structure of an EPUB file,
    preserving the original directory hierarchy (META-INF/, OEBPS/, etc.).
    This is useful for:
    - Inspecting original HTML/CSS/metadata
    - Debugging translation issues
    - Advanced custom processing
    - Re-packaging EPUBs

    Args:
        input_epub: Path to the source EPUB file
        output_dir: Directory where EPUB contents will be extracted
        preserve_structure: If True, maintains original directory structure

    Returns:
        Dictionary mapping internal EPUB paths to extracted file paths
        Example: {"OEBPS/text00000.html": Path("/workspace/epub_raw/OEBPS/text00000.html")}

    Raises:
        FileNotFoundError: If input_epub doesn't exist
        zipfile.BadZipFile: If input_epub is not a valid ZIP/EPUB file

    Example:
        >>> mapping = extract_epub_structure(
        ...     Path("book.epub"),
        ...     Path("workspace/epub_raw")
        ... )
        >>> print(mapping["OEBPS/content.opf"])
        PosixPath('workspace/epub_raw/OEBPS/content.opf')
    """
    if not input_epub.exists():
        raise FileNotFoundError(f"EPUB file not found: {input_epub}")

    output_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, Path] = {}

    try:
        with zipfile.ZipFile(input_epub, "r") as epub_zip:
            # Get list of all files in the EPUB
            file_list = epub_zip.namelist()

            for internal_path in file_list:
                # Skip directories (they end with /)
                if internal_path.endswith("/"):
                    continue

                # Determine output path
                if preserve_structure:
                    # Preserve full directory structure
                    output_path = output_dir / internal_path
                else:
                    # Flatten to single directory
                    filename = Path(internal_path).name
                    output_path = output_dir / filename

                # Create parent directories if needed
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Extract file
                with epub_zip.open(internal_path) as source:
                    output_path.write_bytes(source.read())

                # Store mapping
                mapping[internal_path] = output_path

    except zipfile.BadZipFile as e:
        raise zipfile.BadZipFile(f"Invalid EPUB/ZIP file: {input_epub}") from e

    return mapping


def get_epub_metadata_files(mapping: dict[str, Path]) -> dict[str, Path]:
    """
    Extract key metadata files from the structure mapping.

    Args:
        mapping: Dictionary from extract_epub_structure()

    Returns:
        Dictionary with standardized keys:
        - 'mimetype': Path to mimetype file
        - 'container': Path to META-INF/container.xml
        - 'opf': Path to content.opf (package document)
        - 'ncx': Path to toc.ncx (navigation)

    Example:
        >>> mapping = extract_epub_structure(epub_path, output_dir)
        >>> metadata = get_epub_metadata_files(mapping)
        >>> print(metadata['opf'])
        PosixPath('workspace/epub_raw/OEBPS/content.opf')
    """
    result: dict[str, Path] = {}

    for internal_path, extracted_path in mapping.items():
        internal_lower = internal_path.lower()

        if internal_path == "mimetype":
            result["mimetype"] = extracted_path
        elif "container.xml" in internal_lower:
            result["container"] = extracted_path
        elif internal_lower.endswith(".opf"):
            result["opf"] = extracted_path
        elif internal_lower.endswith(".ncx"):
            result["ncx"] = extracted_path

    return result
