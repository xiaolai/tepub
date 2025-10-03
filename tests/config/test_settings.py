from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_settings
from config.models import AppSettings
from exceptions import StateFileNotFoundError, WorkspaceNotFoundError


def test_load_settings_reads_config_yaml(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        """
work_dir: ./workspace
source_language: en
target_language: Traditional Chinese
        """.strip(),
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.work_dir == tmp_path / "workspace"
    assert settings.source_language == "en"
    assert settings.target_language == "Traditional Chinese"


def test_cli_config_file_overrides_env(monkeypatch, tmp_path, tmp_path_factory) -> None:
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env").write_text("target_language=Spanish\n", encoding="utf-8")
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("target_language: Simplified Chinese\n", encoding="utf-8")

    monkeypatch.chdir(env_dir)
    settings = load_settings(config_path=yaml_path)
    assert settings.target_language == "Simplified Chinese"


class TestAppSettingsValidation:
    """Tests for AppSettings validation methods."""

    def test_validate_for_export_succeeds_when_all_files_exist(self, tmp_path):
        """Test validation passes when all required files exist."""
        import json
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "workspace"
        work_dir.mkdir()

        # Create valid segments file
        segments_data = {
            "epub_path": str(epub_path),
            "generated_at": "2024-01-01T00:00:00",
            "segments": []
        }
        (work_dir / "segments.json").write_text(json.dumps(segments_data))

        # Create valid state file
        state_data = {
            "provider_name": "test",
            "model_name": "test-model",
            "source_language": "en",
            "target_language": "zh",
            "segments": {}
        }
        (work_dir / "state.json").write_text(json.dumps(state_data))

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
            state_file=work_dir / "state.json",
        )

        # Should not raise any exception
        settings.validate_for_export(epub_path)

    def test_validate_for_export_raises_when_workspace_missing(self, tmp_path):
        """Test validation fails when workspace doesn't exist."""
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "nonexistent"

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
            state_file=work_dir / "state.json",
        )

        with pytest.raises(WorkspaceNotFoundError) as exc_info:
            settings.validate_for_export(epub_path)

        assert exc_info.value.epub_path == epub_path
        assert exc_info.value.work_dir == work_dir

    def test_validate_for_export_raises_when_segments_missing(self, tmp_path):
        """Test validation fails when segments file missing."""
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "workspace"
        work_dir.mkdir()
        (work_dir / "state.json").write_text("{}")

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
            state_file=work_dir / "state.json",
        )

        with pytest.raises(StateFileNotFoundError) as exc_info:
            settings.validate_for_export(epub_path)

        assert exc_info.value.state_type == "segments"
        assert exc_info.value.epub_path == epub_path

    def test_validate_for_export_raises_when_state_missing(self, tmp_path):
        """Test validation fails when state file missing."""
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "workspace"
        work_dir.mkdir()
        (work_dir / "segments.json").write_text("{}")

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
            state_file=work_dir / "state.json",
        )

        with pytest.raises(StateFileNotFoundError) as exc_info:
            settings.validate_for_export(epub_path)

        assert exc_info.value.state_type == "translation"
        assert exc_info.value.epub_path == epub_path

    def test_validate_for_translation_succeeds_when_segments_exist(self, tmp_path):
        """Test translation validation passes when segments file exists."""
        import json
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "workspace"
        work_dir.mkdir()

        # Create valid segments file
        segments_data = {
            "epub_path": str(epub_path),
            "generated_at": "2024-01-01T00:00:00",
            "segments": []
        }
        (work_dir / "segments.json").write_text(json.dumps(segments_data))

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
        )

        # Should not raise any exception
        settings.validate_for_translation(epub_path)

    def test_validate_for_translation_raises_when_workspace_missing(self, tmp_path):
        """Test translation validation fails when workspace doesn't exist."""
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "nonexistent"

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
        )

        with pytest.raises(WorkspaceNotFoundError) as exc_info:
            settings.validate_for_translation(epub_path)

        assert exc_info.value.epub_path == epub_path
        assert exc_info.value.work_dir == work_dir

    def test_validate_for_translation_raises_when_segments_missing(self, tmp_path):
        """Test translation validation fails when segments file missing."""
        epub_path = tmp_path / "book.epub"
        epub_path.write_text("fake epub")

        work_dir = tmp_path / "workspace"
        work_dir.mkdir()

        settings = AppSettings(
            work_dir=work_dir,
            segments_file=work_dir / "segments.json",
        )

        with pytest.raises(StateFileNotFoundError) as exc_info:
            settings.validate_for_translation(epub_path)

        assert exc_info.value.state_type == "segments"
        assert exc_info.value.epub_path == epub_path
