"""Book metadata is interpolated into YAML comments, where a newline is code."""

from __future__ import annotations

from pathlib import Path

from config.templates import create_book_config_template


def test_a_newline_in_the_epub_name_cannot_inject_settings(tmp_path: Path) -> None:
    create_book_config_template(
        tmp_path,
        "book.epub\nprimary_provider:\n  base_url: http://attacker.example",
        {"title": "T"},
    )

    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert "book.epub" in header
    assert header.startswith("# Per-book configuration for:")
    # Collapsed onto the comment line, so nothing the filename carries becomes
    # a key of its own.
    assert "\nprimary_provider:" not in text
    assert "\n  base_url: http://attacker.example" not in text
    active = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert all("primary_provider" not in line for line in active)
