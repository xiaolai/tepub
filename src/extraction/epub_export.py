"""Extract complete EPUB internal structure to workspace."""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from epub_io.path_utils import safe_relative_member


def _safe_relative_member(internal_path: str, epub_path: Path) -> PurePosixPath:
    """Validate an archive member name (see epub_io.path_utils.safe_relative_member)."""
    return safe_relative_member(internal_path, epub_path)


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

            # Flattening collapses distinct members onto one name; track what we
            # have written so a collision does not silently destroy the earlier file.
            used_names: dict[str, str] = {}

            for internal_path in file_list:
                # Skip directories (they end with /)
                if internal_path.endswith("/"):
                    continue

                member = _safe_relative_member(internal_path, input_epub)

                # Determine output path
                if preserve_structure:
                    # Preserve full directory structure
                    output_path = output_dir / Path(*member.parts)
                else:
                    # Flatten to single directory
                    filename = member.name
                    if filename in used_names:
                        # Disambiguate deterministically instead of overwriting.
                        stem, dot, suffix = filename.partition(".")
                        counter = 1
                        candidate = filename
                        while candidate in used_names:
                            counter += 1
                            candidate = f"{stem}-{counter}{dot}{suffix}"
                        filename = candidate
                    used_names[filename] = internal_path
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

    # An EPUB may legitimately carry several .opf/.ncx files. Iterating the mapping
    # and overwriting made the winner depend on dict order; pick deterministically
    # instead — shallowest path first, then alphabetically.
    def _rank(internal_path: str) -> tuple[int, str]:
        normalized = internal_path.replace("\\", "/")
        return (normalized.count("/"), normalized.lower())

    for internal_path in sorted(mapping, key=_rank):
        extracted_path = mapping[internal_path]
        normalized = internal_path.replace("\\", "/").lower()
        basename = normalized.rsplit("/", 1)[-1]

        if normalized == "mimetype":
            result.setdefault("mimetype", extracted_path)
        elif basename == "container.xml":
            # Match the basename, not a substring: "OEBPS/not-a-container.xml.html"
            # previously matched and could shadow the real META-INF/container.xml.
            result.setdefault("container", extracted_path)
        elif basename.endswith(".opf"):
            result.setdefault("opf", extracted_path)
        elif basename.endswith(".ncx"):
            result.setdefault("ncx", extracted_path)

    return result
