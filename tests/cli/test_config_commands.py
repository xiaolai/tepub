"""config validate, reset and show: which file they act on, and what they report."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.main import app
from tests.epub_builder import build_epub


def _run(*args: str):
    result = CliRunner().invoke(app, list(args), prog_name="tepub")
    return result, " ".join(result.output.split())


def _extracted(tmp_path: Path) -> tuple[Path, Path, Path]:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    root = tmp_path / "w"
    assert _run("--work-dir", str(root), "extract", str(book))[0].exit_code == 0
    (book_config,) = list(root.rglob("config.yaml"))
    return book, root, book_config


def _global_config(text: str) -> None:
    folder = Path.home() / ".tepub"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text(text, encoding="utf-8")


def test_reset_keeps_the_backup_it_was_asked_for(tmp_path: Path) -> None:
    book, root, book_config = _extracted(tmp_path)
    edited = book_config.read_text(encoding="utf-8") + "\n# my edit\n"
    book_config.write_text(edited, encoding="utf-8")
    result, _ = _run("--work-dir", str(root), "config", "reset", str(book), "--force", "--backup")
    assert result.exit_code == 0, result.output
    backup = book_config.with_name("config.yaml.bak")
    assert backup.is_file() and "# my edit" in backup.read_text(encoding="utf-8")
    assert "# my edit" not in book_config.read_text(encoding="utf-8")
    assert not list(book_config.parent.glob("*.reset-rollback"))


def test_validate_rejects_a_file_that_is_not_a_mapping(tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text("- {a: 1}\n- {b: 2}\n", encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 1 and not isinstance(result.exception, TypeError)
    assert "must hold settings" in output and "not a list" in output


@pytest.mark.parametrize("text", ["false\n", "0\n", '""\n', "[]\n"])
def test_validate_rejects_a_falsy_value_in_place_of_settings(tmp_path: Path, text: str) -> None:
    """These became {} and validated as PASSED."""
    config = tmp_path / "c.yaml"
    config.write_text(text, encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 1, result.output
    assert "must hold settings" in output and "PASSED" not in output


def test_validate_file_with_a_book_checks_the_file_on_its_own(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    config = tmp_path / "c.yaml"
    config.write_text("translation_workers: 2\n", encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config), str(book))
    assert result.exit_code == 0, result.output
    assert "Status: PASSED" in output


def test_validate_shows_a_nested_error_under_its_setting(tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(
        "primary_provider:\n  name: ollama\n  model: m\n  max_tokens: lots\n", encoding="utf-8"
    )
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 1
    assert "✗ primary_provider" in output and "✓ primary_provider" not in output
    assert "Error: max_tokens:" in output and "Invalid: 1" in output


def test_show_for_an_unextracted_book_writes_nothing(tmp_path: Path) -> None:
    book = build_epub(tmp_path / "book.epub", [("c.xhtml", "C", "<p>One.</p>")])
    root = tmp_path / "w"
    result, _ = _run("--work-dir", str(root), "config", "show", str(book))
    assert result.exit_code == 0, result.output
    assert not root.exists()


def test_show_names_the_book_config_it_read_when_that_config_moves_work_dir(tmp_path: Path) -> None:
    book, root, book_config = _extracted(tmp_path)
    book_config.write_text(
        f"work_dir: {tmp_path / 'elsewhere'}\ntranslation_workers: 7\n", encoding="utf-8"
    )
    result, output = _run("--work-dir", str(root), "config", "show", str(book))
    assert result.exit_code == 0, result.output
    # Long paths wrap; the workspace folder and its verdict are on one line.
    assert f"{book_config.parent.name}/config.yaml found" in output
    assert "elsewhere" not in output
    assert "translation_workers 7 book" in output


def test_show_credits_settings_from_dotenv(tmp_path: Path) -> None:
    Path(".env").write_text("target_language=German\n", encoding="utf-8")
    result, output = _run("config", "show")
    assert result.exit_code == 0, result.output
    assert "target_language German .env" in output


def test_show_resets_provider_fields_a_later_file_replaced(tmp_path: Path) -> None:
    _global_config("primary_provider:\n  name: ollama\n  model: a\n  max_tokens: 4096\n")
    Path("config.yaml").write_text(
        "primary_provider:\n  name: ollama\n  model: b\n", encoding="utf-8"
    )
    result, output = _run("config", "show")
    assert result.exit_code == 0, result.output
    assert "primary_provider.max_tokens 8192 default" in output
    assert "primary_provider.model b ./config.yaml" in output


def test_show_credits_a_book_provider_over_ollama_base_url(tmp_path: Path, monkeypatch) -> None:
    """The book's own address overrides OLLAMA_BASE_URL, which overrides the
    files before it; the address used and the origin shown agree."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-host:11434")
    _global_config("primary_provider:\n  name: ollama\n  model: a\n  base_url: http://g:1\n")
    book, root, book_config = _extracted(tmp_path)
    output = _run("config", "show")[1]
    assert "primary_provider.base_url http://env-host:11434 OLLAMA_BASE_URL" in output
    book_config.write_text(
        "primary_provider:\n  name: ollama\n  model: a\n  base_url: http://book-host:11434\n",
        encoding="utf-8",
    )
    result, output = _run("--work-dir", str(root), "config", "show", str(book))
    assert result.exit_code == 0, result.output
    assert "primary_provider.base_url http://book-host:11434 book" in output, output


def test_show_credits_ollama_base_url_under_a_book_provider_without_one(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-host:11434")
    book, root, book_config = _extracted(tmp_path)
    book_config.write_text("primary_provider:\n  name: ollama\n  model: b\n", encoding="utf-8")
    result, output = _run("--work-dir", str(root), "config", "show", str(book))
    assert result.exit_code == 0, result.output
    assert "primary_provider.base_url http://env-host:11434 OLLAMA_BASE_URL" in output, output
    assert "primary_provider.model b book" in output


def test_show_prints_values_that_look_like_markup(tmp_path: Path) -> None:
    _global_config("prompt_preamble: 'Keep [/literal] tags'\n")
    result, output = _run("config", "show")
    assert result.exit_code == 0, result.output
    assert "Keep [/literal] tags" in output


def test_show_reports_a_provider_key_set_in_a_config_file(tmp_path: Path) -> None:
    _global_config("primary_provider:\n  name: openai\n  model: m\n  api_key: sk-secret-in-yaml\n")
    result, output = _run("config", "show")
    assert result.exit_code == 0, result.output
    assert "primary_provider.api_key set" in output and "sk-secret-in-yaml" not in output


def test_validate_accepts_what_load_settings_accepts(tmp_path: Path) -> None:
    from config.loader import load_settings

    config = tmp_path / "c.yaml"
    config.write_text(
        "skip_rules: [cover]\n"
        "target_language: ' French '\n"
        "output_mode: translated-only\n",
        encoding="utf-8",
    )
    loaded = load_settings(config)
    assert [rule.keyword for rule in loaded.skip_rules] == ["cover"]
    assert loaded.target_language == "French"
    assert loaded.output_mode == "translated_only"

    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 0, result.output
    assert "Status: PASSED" in output


@pytest.mark.parametrize("text", ["null\n", "~\n", "!!null null\n"])
def test_validate_rejects_an_explicit_null_in_place_of_settings(tmp_path: Path, text: str) -> None:
    """An explicit null root became {} and validated as PASSED."""
    config = tmp_path / "c.yaml"
    config.write_text(text, encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 1, result.output
    assert "must hold settings" in output and "not a NoneType" in output
    assert "PASSED" not in output


@pytest.mark.parametrize("text", ["", "  \n\n", "# nothing set yet\n"])
def test_validate_accepts_a_file_that_sets_nothing(tmp_path: Path, text: str) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(text, encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 0, result.output
    assert "Status: PASSED" in output


def test_validate_rejects_a_setting_name_that_is_not_text(tmp_path: Path) -> None:
    """`1: x` sorted beside text keys and raised TypeError."""
    config = tmp_path / "c.yaml"
    config.write_text("1: x\ntarget_language: French\n", encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "setting names must be text" in output


def test_validate_reports_a_path_naming_no_user_as_a_setting_error(tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text("work_dir: ~nonexistentuser-tepub/x\n", encoding="utf-8")
    result, output = _run("config", "validate", "--file", str(config))
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "work_dir" in output and "FAILED" in output
