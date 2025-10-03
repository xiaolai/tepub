from __future__ import annotations

import hashlib
from pathlib import Path

from config.models import AppSettings
from config.workspace import build_workspace_name


def test_build_workspace_name_uses_first_word_and_hash() -> None:
    epub_path = Path("/library/The-Great Book.epub")
    expected_hash = hashlib.sha1(
        str(epub_path.expanduser().resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:8]

    workspace_name = build_workspace_name(epub_path)

    assert workspace_name == f"the-{expected_hash}"


def test_build_workspace_name_handles_non_ascii() -> None:
    epub_path = Path("/library/你好.epub")
    expected_hash = hashlib.sha1(
        str(epub_path.expanduser().resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:8]

    workspace_name = build_workspace_name(epub_path)

    assert workspace_name == f"book-{expected_hash}"


def test_with_book_workspace_derives_directory(tmp_path: Path) -> None:
    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)
    epub_path = tmp_path / "Great Expectations.epub"

    derived = settings.with_book_workspace(epub_path)

    expected_dir = tmp_path / "Great Expectations"
    assert derived.work_dir == expected_dir
    assert derived.work_root == tmp_path
    assert derived.segments_file.parent == expected_dir
    assert derived.state_file.parent == expected_dir


def test_with_override_root_creates_hashed_child(tmp_path: Path) -> None:
    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)
    epub_path = tmp_path / "The Book.epub"
    base_root = tmp_path / "custom"

    derived = settings.with_override_root(base_root, epub_path)

    expected_dir = base_root / build_workspace_name(epub_path)
    assert derived.work_root == base_root
    assert derived.work_dir == expected_dir


def test_with_override_root_detects_existing_workspace(tmp_path: Path) -> None:
    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)
    epub_path = tmp_path / "The Book.epub"
    existing_workspace = tmp_path / build_workspace_name(epub_path)
    existing_workspace.mkdir()
    (existing_workspace / "segments.json").touch()

    derived = settings.with_override_root(existing_workspace, epub_path)

    assert derived.work_dir == existing_workspace
    assert derived.work_root == existing_workspace.parent


def test_with_override_root_accepts_matching_name_without_files(tmp_path: Path) -> None:
    settings = AppSettings(work_root=tmp_path, work_dir=tmp_path)
    epub_path = tmp_path / "The Book.epub"
    workspace = tmp_path / build_workspace_name(epub_path)

    derived = settings.with_override_root(workspace, epub_path)

    # Since workspace doesn't have segments.json or state.json, it creates a hash-based subdir
    expected_dir = workspace / build_workspace_name(epub_path)
    assert derived.work_dir == expected_dir
    assert derived.work_root == workspace
