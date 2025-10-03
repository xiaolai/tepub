from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from importlib import import_module


def _load_app():
    return import_module("cli.main").app


def test_debug_analyze_skips_invokes_analysis(monkeypatch, tmp_path) -> None:
    called = {}

    def _fake_analyze(settings, library, limit, top_n, report_path):
        called["library"] = library
        called["limit"] = limit
        called["top_n"] = top_n
        called["report"] = report_path

    monkeypatch.setattr("debug_tools.analysis.analyze_library", _fake_analyze)

    runner = CliRunner()
    result = runner.invoke(
        _load_app(),
        [
            "debug",
            "analyze-skips",
            "--library",
            str(tmp_path),
            "--limit",
            "5",
            "--top-n",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert called["library"] == tmp_path
    assert called["limit"] == 5
    assert called["top_n"] == 3
