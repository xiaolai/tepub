"""Settings are checked and derived the same way however they arrive."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.models import AppSettings, ProviderConfig
from config.workspace import with_book_workspace
from console_singleton import PlainProgress
from exceptions import TepubError


def test_a_blank_skip_keyword_is_refused() -> None:
    """Blank became "", which is in every title, so it matched first."""
    with pytest.raises(ValidationError):
        AppSettings(skip_rules=[{"keyword": "   "}])


def test_back_matter_triggers_are_normalised() -> None:
    settings = AppSettings(back_matter_triggers=[" Index ", "NOTES"])
    assert settings.back_matter_triggers == ["index", "notes"]
    with pytest.raises(ValidationError):
        AppSettings(back_matter_triggers=["index", " "])


def test_artifact_paths_expand_the_home_directory(tmp_path: Path) -> None:
    settings = AppSettings(work_dir=tmp_path, segments_file=Path("~/segments.json"))
    assert settings.segments_file == Path.home() / "segments.json"


def test_a_copy_with_a_new_root_moves_the_derived_paths(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    copied = AppSettings(work_root=old).model_copy(update={"work_root": new})
    assert copied.work_dir == new
    assert copied.segments_file == new / "segments.json"
    assert copied.state_file == new / "state.json"


def test_a_copy_keeps_a_work_dir_that_was_set(tmp_path: Path) -> None:
    settings = AppSettings(work_root=tmp_path / "old", work_dir=tmp_path / "book")
    copied = settings.model_copy(update={"work_root": tmp_path / "new"})
    assert copied.work_dir == tmp_path / "book"


def test_an_invalid_provider_instance_is_refused() -> None:
    provider = ProviderConfig(name="openai", model="gpt").model_copy(update={"max_tokens": 0})
    with pytest.raises(ValidationError):
        AppSettings().model_copy(update={"primary_provider": provider})


def test_plain_progress_refuses_a_zero_interval() -> None:
    with pytest.raises(ValueError):
        PlainProgress("Translated", 10, every=0)


# --- a book's own config.yaml -------------------------------------------------


def _book(tmp_path: Path, config: str | None) -> Path:
    workspace = tmp_path / "book"
    workspace.mkdir()
    if config is not None:
        (workspace / "config.yaml").write_text(config, encoding="utf-8")
    return tmp_path / "book.epub"


@pytest.mark.parametrize("text", ["- target_language: Spanish\n", "false\n", "0\n", '""\n', "[]\n"])
def test_a_book_config_that_is_not_a_mapping_is_refused(tmp_path: Path, text: str) -> None:
    """`false`, `0`, `""` and `[]` were read as no settings and passed."""
    epub = _book(tmp_path, text)
    with pytest.raises(TepubError, match="config.yaml"):
        with_book_workspace(AppSettings(), epub)


def test_a_config_holding_only_comments_sets_nothing(tmp_path: Path) -> None:
    from config.loader import _parse_yaml_file

    config = tmp_path / "config.yaml"
    config.write_text("# nothing here yet\n", encoding="utf-8")
    assert _parse_yaml_file(config) == {}


def test_a_book_config_takes_bare_skip_keywords(tmp_path: Path) -> None:
    epub = _book(tmp_path, "skip_rules:\n  - prologue\n")
    settings = with_book_workspace(AppSettings(), epub)
    assert "prologue" in {rule.keyword for rule in settings.skip_rules}


def test_book_skip_rules_add_to_the_inherited_ones(tmp_path: Path) -> None:
    epub = _book(tmp_path, "skip_rules:\n  - keyword: prologue\n  - keyword: Index\n")
    inherited = AppSettings()
    settings = with_book_workspace(inherited, epub)
    keywords = [rule.keyword for rule in settings.skip_rules]
    assert keywords == [rule.keyword for rule in inherited.skip_rules] + ["prologue"]


def test_a_book_skip_rule_given_twice_is_added_once(tmp_path: Path) -> None:
    epub = _book(tmp_path, "skip_rules:\n  - prologue\n  - ' Prologue '\n")
    inherited = AppSettings()
    settings = with_book_workspace(inherited, epub)
    keywords = [rule.keyword for rule in settings.skip_rules]
    assert keywords == [rule.keyword for rule in inherited.skip_rules] + ["prologue"]


def test_a_book_provider_gets_the_environment_address(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434")
    epub = _book(tmp_path, "primary_provider:\n  name: ollama\n  model: qwen3:8b\n")
    settings = with_book_workspace(AppSettings(), epub)
    assert settings.primary_provider.model == "qwen3:8b"
    assert settings.primary_provider.base_url == "http://gpu-box:11434"


def test_a_book_address_overrides_the_environment(tmp_path: Path, monkeypatch) -> None:
    """The book's config sits above OLLAMA_BASE_URL; re-applying the variable
    after the book's overlay replaced the address the book set itself."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434")
    epub = _book(
        tmp_path,
        "primary_provider:\n  name: ollama\n  model: qwen3:8b\n  base_url: http://book-box:11434\n",
    )
    settings = with_book_workspace(AppSettings(), epub)
    assert settings.primary_provider.base_url == "http://book-box:11434"


def test_a_book_api_key_overrides_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    epub = _book(
        tmp_path, "primary_provider:\n  name: openai\n  model: m\n  api_key: sk-from-book\n"
    )
    settings = with_book_workspace(AppSettings(), epub)
    assert settings.primary_provider.api_key == "sk-from-book"


def test_unknown_book_settings_are_named(tmp_path: Path, caplog) -> None:
    epub = _book(tmp_path, "target_langauge: Spanish\n")
    with caplog.at_level("WARNING"):
        with_book_workspace(AppSettings(), epub)
    assert "target_langauge" in caplog.text


def test_a_book_without_a_prompt_does_not_keep_the_last_one(tmp_path: Path) -> None:
    from translation import prompt_builder

    first = tmp_path / "first"
    first.mkdir()
    epub_a = _book(first, "prompt_preamble: Translate as a poet.\n")
    second = tmp_path / "second"
    second.mkdir()
    epub_b = _book(second, None)
    try:
        with_book_workspace(AppSettings(), epub_a)
        assert prompt_builder._PROMPT_PREAMBLE == "Translate as a poet."
        with_book_workspace(AppSettings(), epub_b)
        assert prompt_builder._PROMPT_PREAMBLE is None
    finally:
        prompt_builder.configure_prompt(None)
