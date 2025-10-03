"""Tests for safe_load_state function."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from exceptions import CorruptedStateError
from state.base import safe_load_state


class SimpleModel(BaseModel):
    """Simple test model."""

    name: str
    count: int


class TestSafeLoadState:
    """Tests for safe_load_state function."""

    def test_loads_valid_file_successfully(self, tmp_path):
        """Test loading a valid state file."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"name": "test", "count": 42}))

        result = safe_load_state(state_file, SimpleModel, "test")

        assert isinstance(result, SimpleModel)
        assert result.name == "test"
        assert result.count == 42

    def test_raises_file_not_found_for_missing_file(self, tmp_path):
        """Test that missing file raises FileNotFoundError."""
        missing_file = tmp_path / "missing.json"

        with pytest.raises(FileNotFoundError):
            safe_load_state(missing_file, SimpleModel, "test")

    def test_raises_corrupted_error_for_invalid_json(self, tmp_path):
        """Test that invalid JSON raises CorruptedStateError."""
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json{")

        with pytest.raises(CorruptedStateError) as exc_info:
            safe_load_state(state_file, SimpleModel, "test")

        error = exc_info.value
        assert error.file_path == state_file
        assert error.state_type == "test"
        assert "Invalid JSON format" in error.reason
        assert "line" in error.reason

    def test_raises_corrupted_error_for_schema_mismatch(self, tmp_path):
        """Test that schema validation failure raises CorruptedStateError."""
        state_file = tmp_path / "state.json"
        # Wrong type for 'count' field
        state_file.write_text(json.dumps({"name": "test", "count": "not_a_number"}))

        with pytest.raises(CorruptedStateError) as exc_info:
            safe_load_state(state_file, SimpleModel, "test")

        error = exc_info.value
        assert error.file_path == state_file
        assert error.state_type == "test"
        assert "Schema validation failed" in error.reason
        assert "count" in error.reason

    def test_raises_corrupted_error_for_missing_required_field(self, tmp_path):
        """Test that missing required field raises CorruptedStateError."""
        state_file = tmp_path / "state.json"
        # Missing 'count' field
        state_file.write_text(json.dumps({"name": "test"}))

        with pytest.raises(CorruptedStateError) as exc_info:
            safe_load_state(state_file, SimpleModel, "test")

        error = exc_info.value
        assert "Schema validation failed" in error.reason
        assert "count" in error.reason

    def test_error_message_includes_helpful_suggestion(self, tmp_path):
        """Test that error message suggests re-running extract."""
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid")

        with pytest.raises(CorruptedStateError) as exc_info:
            safe_load_state(state_file, SimpleModel, "segments")

        error_msg = str(exc_info.value)
        assert "tepub extract" in error_msg
        assert "corrupted" in error_msg.lower()

    def test_preserves_file_path_in_error(self, tmp_path):
        """Test that file path is preserved in error for debugging."""
        state_file = tmp_path / "deeply" / "nested" / "state.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("bad json")

        with pytest.raises(CorruptedStateError) as exc_info:
            safe_load_state(state_file, SimpleModel, "test")

        assert exc_info.value.file_path == state_file
        assert str(state_file) not in str(exc_info.value)  # Shows only filename
        assert "state.json" in str(exc_info.value)
