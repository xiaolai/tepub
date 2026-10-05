"""The suite must not read anything from the machine it runs on.

Before this existed, tests loaded the developer's real ~/.tepub/config.yaml, a
.env in the repository root, and whatever provider keys were exported, so the
same commit passed on one machine and failed on another.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.loader import load_settings
from tests.conftest import PROVIDER_ENV_VARS, REAL_HOME

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_home_is_not_the_real_home() -> None:
    assert Path.home() != REAL_HOME
    assert Path.home().is_dir()
    assert not (Path.home() / ".tepub").exists()


def test_working_directory_is_not_the_repository() -> None:
    assert Path.cwd().resolve() != REPO_ROOT


@pytest.mark.parametrize("name", PROVIDER_ENV_VARS)
def test_provider_keys_are_cleared(name: str) -> None:
    assert name not in os.environ


def test_no_tepub_variables_leak_in() -> None:
    assert not [key for key in os.environ if key.startswith("TEPUB_")]


def test_settings_load_from_the_isolated_home_only() -> None:
    # A config written to the isolated home is read, which proves that the
    # isolation is what keeps the real one out rather than the loader ignoring
    # the home directory altogether.
    config_dir = Path.home() / ".tepub"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("target_language: Klingon\n", encoding="utf-8")
    assert load_settings().target_language == "Klingon"


def test_each_test_gets_a_fresh_home() -> None:
    assert not (Path.home() / ".tepub").exists()
