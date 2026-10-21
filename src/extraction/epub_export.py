"""Extract complete EPUB internal structure to workspace."""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from lxml import etree

from epub_io.container import (
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_TOTAL_BYTES,
    EpubStructureError,
    secure_xml_parser,
)
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

            # Validate every member up front. Validating inside the write loop
            # meant a malicious member partway through an archive was rejected
            # only after everything before it had already been written to disk.
            for candidate in file_list:
                if not candidate.endswith("/"):
                    _safe_relative_member(candidate, input_epub)

            # Flattening collapses distinct members onto one name; track what we
            # have written so a collision does not silently destroy the earlier file.
            used_names: dict[str, str] = {}
            total_bytes = 0

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
                    # Two names that differ only in case collide on the
                    # case-insensitive filesystem this export is often written
                    # to, and the later one silently replaced the earlier.
                    if filename.casefold() in used_names:
                        # Disambiguate deterministically instead of overwriting.
                        stem, dot, suffix = filename.partition(".")
                        counter = 1
                        candidate = filename
                        while candidate.casefold() in used_names:
                            counter += 1
                            candidate = f"{stem}-{counter}{dot}{suffix}"
                        filename = candidate
                    used_names[filename.casefold()] = internal_path
                    output_path = output_dir / filename

                # Create parent directories if needed
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Extract file, bounded: a zip bomb used to be decompressed
                # whole into memory and then written to disk.
                info = epub_zip.getinfo(internal_path)
                total_bytes += info.file_size
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise EpubStructureError(
                        f"{internal_path} declares {info.file_size:,} bytes, over the "
                        f"{MAX_ARCHIVE_MEMBER_BYTES:,}-byte limit"
                    )
                if total_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                    raise EpubStructureError(
                        f"the archive expands past the {MAX_ARCHIVE_TOTAL_BYTES:,}-byte limit"
                    )
                written = 0
                with epub_zip.open(internal_path) as source, output_path.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_ARCHIVE_MEMBER_BYTES:
                            raise EpubStructureError(
                                f"{internal_path} expanded past the "
                                f"{MAX_ARCHIVE_MEMBER_BYTES:,}-byte limit"
                            )
                        destination.write(chunk)

                # Store mapping
                mapping[internal_path] = output_path

    except zipfile.BadZipFile as e:
        raise zipfile.BadZipFile(f"Invalid EPUB/ZIP file: {input_epub}") from e

    return mapping


_CONTAINER_PATH = "meta-inf/container.xml"
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"


def _parse_extracted_xml(path: Path):
    """The extracted file's XML root, or None when it is missing or malformed."""
    try:
        return etree.parse(str(path), parser=secure_xml_parser()).getroot()
    except (OSError, etree.XMLSyntaxError):
        return None


def _local_name(element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _member(mapping: dict[str, Path], internal_path: str) -> str | None:
    """The mapping key for ``internal_path``; an exact match, else by case."""
    if internal_path in mapping:
        return internal_path
    folded = internal_path.casefold()
    return next((key for key in mapping if key.casefold() == folded), None)


def _resolve_member(base_internal: str, href: str) -> str:
    directory = base_internal.rsplit("/", 1)[0] if "/" in base_internal else ""
    return posixpath.normpath(posixpath.join(directory, unquote(href)))


def _rootfile(mapping: dict[str, Path]) -> str | None:
    """The package document named by META-INF/container.xml."""
    container_key = next(
        (
            key
            for key in mapping
            if key.replace("\\", "/").casefold() == _CONTAINER_PATH
        ),
        None,
    )
    if container_key is None:
        return None
    root = _parse_extracted_xml(mapping[container_key])
    if root is None:
        return None
    for element in root.iter():
        if _local_name(element) != "rootfile":
            continue
        full_path = (element.get("full-path") or "").replace("\\", "/")
        if not full_path:
            continue
        found = _member(mapping, full_path)
        if found is not None:
            return found
    return None


def _ncx_reference(mapping: dict[str, Path], opf_internal: str) -> str | None:
    """The NCX the package's spine or manifest declares."""
    root = _parse_extracted_xml(mapping[opf_internal])
    if root is None:
        return None
    items: dict[str, tuple[str, str]] = {}
    spine_toc: str | None = None
    for element in root.iter():
        name = _local_name(element)
        if name == "item":
            item_id = element.get("id")
            href = element.get("href")
            if item_id and href:
                items[item_id] = (href, (element.get("media-type") or "").lower())
        elif name == "spine":
            spine_toc = element.get("toc")
    candidates: list[tuple[str, str]] = []
    if spine_toc and spine_toc in items:
        candidates.append(items[spine_toc])
    candidates += [item for item in items.values() if item[1] == _NCX_MEDIA_TYPE]
    for href, _media_type in candidates:
        found = _member(mapping, _resolve_member(opf_internal, href))
        if found is not None:
            return found
    return None


def _shallowest(mapping: dict[str, Path], suffix: str) -> str | None:
    """The shallowest, then alphabetically first, member with ``suffix``."""
    ranked = [
        key
        for key in mapping
        if key.replace("\\", "/").lower().rsplit("/", 1)[-1].endswith(suffix)
    ]
    if not ranked:
        return None
    return min(
        ranked, key=lambda key: (key.replace("\\", "/").count("/"), key.lower())
    )


def get_epub_metadata_files(mapping: dict[str, Path]) -> dict[str, Path]:
    """
    Extract key metadata files from the structure mapping.

    The container names the package document, which names the NCX. Following
    those references replaces choosing by path depth, which could report an
    unrelated container, OPF or NCX; depth is only a fallback when no usable
    reference exists.

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

    mimetype = _member(mapping, "mimetype")
    if mimetype is not None:
        result["mimetype"] = mapping[mimetype]

    container_key = next(
        (
            key
            for key in mapping
            if key.replace("\\", "/").casefold() == _CONTAINER_PATH
        ),
        None,
    )
    if container_key is not None:
        result["container"] = mapping[container_key]

    opf_key = _rootfile(mapping) or _shallowest(mapping, ".opf")
    if opf_key is not None:
        result["opf"] = mapping[opf_key]
        ncx_key = _ncx_reference(mapping, opf_key)
        if ncx_key is not None:
            result["ncx"] = mapping[ncx_key]
    else:
        # Depth is only a fallback when there is no package to follow. With a
        # package that names no NCX, borrowing another .ncx from the archive
        # reported an unrelated navigation document as this book's.
        ncx_key = _shallowest(mapping, ".ncx")
        if ncx_key is not None:
            result["ncx"] = mapping[ncx_key]

    return result
