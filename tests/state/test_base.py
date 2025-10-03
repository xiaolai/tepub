"""Tests for generic state management base module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from state.base import (
    atomic_write,
    load_generic_state,
    save_generic_state,
    update_state_item,
)


# Test models
class SimpleState(BaseModel):
    """Simple test state model."""
    counter: int = 0
    name: str = "test"


class ComplexState(BaseModel):
    """Complex test state model with nested data."""
    items: dict[str, int] = {}
    metadata: dict[str, str] = {}


class TestAtomicWrite:
    """Tests for atomic_write function."""

    def test_atomic_write_creates_file(self, tmp_path: Path):
        """Test that atomic_write creates the target file."""
        target = tmp_path / "test.json"
        payload = {"key": "value"}

        atomic_write(target, payload)

        assert target.exists()
        assert json.loads(target.read_text()) == payload

    def test_atomic_write_uses_tmp_file(self, tmp_path: Path):
        """Test that atomic_write uses temporary file for atomicity."""
        target = tmp_path / "test.json"
        payload = {"key": "value"}

        # The .tmp file should not exist after successful write
        atomic_write(target, payload)

        tmp_file = target.with_suffix(target.suffix + ".tmp")
        assert not tmp_file.exists()
        assert target.exists()

    def test_atomic_write_overwrites_existing(self, tmp_path: Path):
        """Test that atomic_write overwrites existing files."""
        target = tmp_path / "test.json"

        # Write initial content
        atomic_write(target, {"version": 1})
        assert json.loads(target.read_text())["version"] == 1

        # Overwrite with new content
        atomic_write(target, {"version": 2})
        assert json.loads(target.read_text())["version"] == 2

    def test_atomic_write_preserves_unicode(self, tmp_path: Path):
        """Test that atomic_write preserves non-ASCII characters."""
        target = tmp_path / "test.json"
        payload = {"chinese": "中文", "emoji": "🚀"}

        atomic_write(target, payload)

        loaded = json.loads(target.read_text())
        assert loaded["chinese"] == "中文"
        assert loaded["emoji"] == "🚀"

    def test_atomic_write_formats_json(self, tmp_path: Path):
        """Test that atomic_write formats JSON with indentation."""
        target = tmp_path / "test.json"
        payload = {"nested": {"key": "value"}}

        atomic_write(target, payload)

        content = target.read_text()
        # Should have indentation (multiple lines)
        assert "\n" in content
        assert "  " in content  # 2-space indent


class TestLoadGenericState:
    """Tests for load_generic_state function."""

    def test_load_simple_state(self, tmp_path: Path):
        """Test loading a simple state document."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"counter": 42, "name": "loaded"}))

        state = load_generic_state(state_file, SimpleState)

        assert isinstance(state, SimpleState)
        assert state.counter == 42
        assert state.name == "loaded"

    def test_load_complex_state(self, tmp_path: Path):
        """Test loading a complex state document."""
        state_file = tmp_path / "state.json"
        data = {
            "items": {"a": 1, "b": 2},
            "metadata": {"version": "1.0"}
        }
        state_file.write_text(json.dumps(data))

        state = load_generic_state(state_file, ComplexState)

        assert isinstance(state, ComplexState)
        assert state.items == {"a": 1, "b": 2}
        assert state.metadata == {"version": "1.0"}

    def test_load_missing_file_raises(self, tmp_path: Path):
        """Test that loading non-existent file raises FileNotFoundError."""
        missing_file = tmp_path / "missing.json"

        with pytest.raises(FileNotFoundError):
            load_generic_state(missing_file, SimpleState)

    def test_load_invalid_json_raises(self, tmp_path: Path):
        """Test that loading invalid JSON raises JSONDecodeError."""
        invalid_file = tmp_path / "invalid.json"
        invalid_file.write_text("not valid json{")

        with pytest.raises(json.JSONDecodeError):
            load_generic_state(invalid_file, SimpleState)

    def test_load_validation_error(self, tmp_path: Path):
        """Test that loading data with wrong schema raises ValidationError."""
        state_file = tmp_path / "state.json"
        # Invalid type for counter field (string instead of int)
        state_file.write_text(json.dumps({"counter": "not_a_number", "name": "test"}))

        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            load_generic_state(state_file, SimpleState)


class TestSaveGenericState:
    """Tests for save_generic_state function."""

    def test_save_simple_state(self, tmp_path: Path):
        """Test saving a simple state document."""
        state_file = tmp_path / "state.json"
        state = SimpleState(counter=99, name="saved")

        save_generic_state(state, state_file)

        assert state_file.exists()
        loaded_data = json.loads(state_file.read_text())
        assert loaded_data["counter"] == 99
        assert loaded_data["name"] == "saved"

    def test_save_complex_state(self, tmp_path: Path):
        """Test saving a complex state document."""
        state_file = tmp_path / "state.json"
        state = ComplexState(
            items={"x": 10, "y": 20},
            metadata={"author": "test"}
        )

        save_generic_state(state, state_file)

        loaded_data = json.loads(state_file.read_text())
        assert loaded_data["items"] == {"x": 10, "y": 20}
        assert loaded_data["metadata"] == {"author": "test"}

    def test_save_uses_atomic_write(self, tmp_path: Path):
        """Test that save_generic_state uses atomic write."""
        state_file = tmp_path / "state.json"
        state = SimpleState(counter=1)

        save_generic_state(state, state_file)

        # Temporary file should not exist after save
        tmp_file = state_file.with_suffix(state_file.suffix + ".tmp")
        assert not tmp_file.exists()
        assert state_file.exists()

    def test_save_overwrites_existing(self, tmp_path: Path):
        """Test that saving overwrites existing state file."""
        state_file = tmp_path / "state.json"

        # Save initial state
        state1 = SimpleState(counter=1, name="first")
        save_generic_state(state1, state_file)

        # Save new state
        state2 = SimpleState(counter=2, name="second")
        save_generic_state(state2, state_file)

        # Verify only second state persists
        loaded_data = json.loads(state_file.read_text())
        assert loaded_data["counter"] == 2
        assert loaded_data["name"] == "second"


class TestUpdateStateItem:
    """Tests for update_state_item function."""

    def test_update_modifies_state(self, tmp_path: Path):
        """Test that update_state_item correctly modifies state."""
        state_file = tmp_path / "state.json"
        initial_state = SimpleState(counter=0, name="initial")
        save_generic_state(initial_state, state_file)

        def increment_counter(state: SimpleState) -> SimpleState:
            state.counter += 1
            return state

        updated = update_state_item(state_file, SimpleState, increment_counter)

        assert updated.counter == 1
        assert updated.name == "initial"

    def test_update_persists_changes(self, tmp_path: Path):
        """Test that update_state_item persists changes to disk."""
        state_file = tmp_path / "state.json"
        initial_state = SimpleState(counter=5, name="test")
        save_generic_state(initial_state, state_file)

        def double_counter(state: SimpleState) -> SimpleState:
            state.counter *= 2
            return state

        update_state_item(state_file, SimpleState, double_counter)

        # Reload from disk to verify persistence
        reloaded = load_generic_state(state_file, SimpleState)
        assert reloaded.counter == 10

    def test_update_complex_state(self, tmp_path: Path):
        """Test updating complex state with nested structures."""
        state_file = tmp_path / "state.json"
        initial_state = ComplexState(items={"a": 1}, metadata={})
        save_generic_state(initial_state, state_file)

        def add_item(state: ComplexState) -> ComplexState:
            state.items["b"] = 2
            state.metadata["updated"] = "true"
            return state

        updated = update_state_item(state_file, ComplexState, add_item)

        assert updated.items == {"a": 1, "b": 2}
        assert updated.metadata == {"updated": "true"}

    def test_update_returns_updated_state(self, tmp_path: Path):
        """Test that update_state_item returns the updated state."""
        state_file = tmp_path / "state.json"
        initial_state = SimpleState(counter=0)
        save_generic_state(initial_state, state_file)

        def modify(state: SimpleState) -> SimpleState:
            state.name = "modified"
            return state

        result = update_state_item(state_file, SimpleState, modify)

        assert result.name == "modified"
        assert isinstance(result, SimpleState)

    def test_update_multiple_times(self, tmp_path: Path):
        """Test multiple sequential updates."""
        state_file = tmp_path / "state.json"
        initial_state = SimpleState(counter=0)
        save_generic_state(initial_state, state_file)

        def increment(state: SimpleState) -> SimpleState:
            state.counter += 1
            return state

        # Apply update 3 times
        for _ in range(3):
            update_state_item(state_file, SimpleState, increment)

        final_state = load_generic_state(state_file, SimpleState)
        assert final_state.counter == 3


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_full_lifecycle(self, tmp_path: Path):
        """Test complete lifecycle: create, save, load, update."""
        state_file = tmp_path / "lifecycle.json"

        # 1. Create and save
        state = SimpleState(counter=1, name="lifecycle")
        save_generic_state(state, state_file)

        # 2. Load
        loaded = load_generic_state(state_file, SimpleState)
        assert loaded.counter == 1

        # 3. Update
        def increment(s: SimpleState) -> SimpleState:
            s.counter += 1
            return s

        updated = update_state_item(state_file, SimpleState, increment)
        assert updated.counter == 2

        # 4. Reload to verify
        final = load_generic_state(state_file, SimpleState)
        assert final.counter == 2

    def test_concurrent_updates_use_atomic_write(self, tmp_path: Path):
        """Test that concurrent updates don't leave tmp files."""
        state_file = tmp_path / "concurrent.json"
        state = SimpleState(counter=0)
        save_generic_state(state, state_file)

        def increment(s: SimpleState) -> SimpleState:
            s.counter += 1
            return s

        # Multiple updates
        for _ in range(5):
            update_state_item(state_file, SimpleState, increment)

        # No tmp files should remain
        tmp_file = state_file.with_suffix(state_file.suffix + ".tmp")
        assert not tmp_file.exists()
        assert state_file.exists()

        # Final state should be correct
        final = load_generic_state(state_file, SimpleState)
        assert final.counter == 5
