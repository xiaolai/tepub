"""Tests for custom exceptions."""

from __future__ import annotations

from pathlib import Path

import pytest

from exceptions import CorruptedStateError, StateFileNotFoundError, TepubError, WorkspaceNotFoundError


class TestTepubError:
    """Tests for base TepubError exception."""

    def test_is_exception(self):
        """TepubError should be an Exception subclass."""
        assert issubclass(TepubError, Exception)

    def test_can_raise(self):
        """TepubError can be raised and caught."""
        with pytest.raises(TepubError) as exc_info:
            raise TepubError("test error")
        assert str(exc_info.value) == "test error"


class TestStateFileNotFoundError:
    """Tests for StateFileNotFoundError."""

    def test_is_tepub_error(self):
        """StateFileNotFoundError should be a TepubError subclass."""
        assert issubclass(StateFileNotFoundError, TepubError)

    def test_segments_error_message(self):
        """Test error message for missing segments file."""
        epub_path = Path("/path/to/book.epub")
        error = StateFileNotFoundError("segments", epub_path)

        expected = (
            "No extraction state found for 'book.epub'.\n"
            "Please run: tepub extract '/path/to/book.epub'"
        )
        assert str(error) == expected
        assert error.state_type == "segments"
        assert error.epub_path == epub_path

    def test_translation_error_message(self):
        """Test error message for missing translation state file."""
        epub_path = Path("/downloads/novel.epub")
        error = StateFileNotFoundError("translation", epub_path)

        expected = (
            "No translation state found for 'novel.epub'.\n"
            "Please run the following commands first:\n"
            "  1. tepub extract '/downloads/novel.epub'\n"
            "  2. tepub translate '/downloads/novel.epub'"
        )
        assert str(error) == expected
        assert error.state_type == "translation"
        assert error.epub_path == epub_path

    def test_unknown_state_type_message(self):
        """Test fallback error message for unknown state type."""
        epub_path = Path("/test/book.epub")
        error = StateFileNotFoundError("unknown", epub_path)

        expected = "State file 'unknown' not found."
        assert str(error) == expected

    def test_can_be_caught_as_tepub_error(self):
        """StateFileNotFoundError can be caught as TepubError."""
        epub_path = Path("/test.epub")
        with pytest.raises(TepubError):
            raise StateFileNotFoundError("segments", epub_path)

    def test_preserves_path_type(self):
        """Test that Path objects are preserved correctly."""
        epub_path = Path("/some/path/to/book.epub")
        error = StateFileNotFoundError("segments", epub_path)

        assert isinstance(error.epub_path, Path)
        assert error.epub_path == epub_path


class TestWorkspaceNotFoundError:
    """Tests for WorkspaceNotFoundError."""

    def test_is_tepub_error(self):
        """WorkspaceNotFoundError should be a TepubError subclass."""
        assert issubclass(WorkspaceNotFoundError, TepubError)

    def test_error_message(self):
        """Test error message for missing workspace."""
        epub_path = Path("/books/mybook.epub")
        work_dir = Path("/workspace/mybook")
        error = WorkspaceNotFoundError(epub_path, work_dir)

        expected = (
            "No workspace found for 'mybook.epub'.\n"
            "Expected workspace at: /workspace/mybook\n"
            "Please run: tepub extract '/books/mybook.epub'"
        )
        assert str(error) == expected
        assert error.epub_path == epub_path
        assert error.work_dir == work_dir

    def test_can_be_caught_as_tepub_error(self):
        """WorkspaceNotFoundError can be caught as TepubError."""
        epub_path = Path("/test.epub")
        work_dir = Path("/workspace")
        with pytest.raises(TepubError):
            raise WorkspaceNotFoundError(epub_path, work_dir)

    def test_preserves_path_types(self):
        """Test that Path objects are preserved correctly."""
        epub_path = Path("/path/to/book.epub")
        work_dir = Path("/path/to/workspace")
        error = WorkspaceNotFoundError(epub_path, work_dir)

        assert isinstance(error.epub_path, Path)
        assert isinstance(error.work_dir, Path)
        assert error.epub_path == epub_path
        assert error.work_dir == work_dir


class TestCorruptedStateError:
    """Tests for CorruptedStateError."""

    def test_is_tepub_error(self):
        """CorruptedStateError should be a TepubError subclass."""
        assert issubclass(CorruptedStateError, TepubError)

    def test_error_message_structure(self):
        """Test error message for corrupted state file."""
        file_path = Path("/workspace/state.json")
        error = CorruptedStateError(file_path, "translation", "Invalid JSON format (line 1, column 5)")

        assert "state.json" in str(error)
        assert "Invalid JSON format" in str(error)
        assert "tepub extract" in str(error)
        assert error.file_path == file_path
        assert error.state_type == "translation"
        assert error.reason == "Invalid JSON format (line 1, column 5)"

    def test_can_be_caught_as_tepub_error(self):
        """CorruptedStateError can be caught as TepubError."""
        file_path = Path("/test.json")
        with pytest.raises(TepubError):
            raise CorruptedStateError(file_path, "segments", "test reason")

    def test_preserves_path_type(self):
        """Test that Path objects are preserved correctly."""
        file_path = Path("/some/path/state.json")
        error = CorruptedStateError(file_path, "state", "test")

        assert isinstance(error.file_path, Path)
        assert error.file_path == file_path
