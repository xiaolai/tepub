import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Captured once, before any test replaces HOME, so the isolation tests can prove
# the suite never sees it.
REAL_HOME = Path.home()

# Every variable the source reads (grep for os.getenv / os.environ in src/).
# A test that needs one sets it with monkeypatch.
PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "DEEPL_API_KEY",
    "GEMINI_API_KEY",
    "GROK_API_KEY",
    "OLLAMA_BASE_URL",
    "OPENAI_API_KEY",
)


def _find_punkt_data() -> Path | None:
    """Locate NLTK data holding the English Punkt tables, without downloading.

    The sentence splitter needs ``tokenizers/punkt_tab/english``. A test must
    never fetch it from the network, so the data is found where a developer or
    CI already put it, and NLTK_DATA is pinned to that place before HOME moves.
    """
    candidates = [Path(p) for p in os.environ.get("NLTK_DATA", "").split(os.pathsep) if p]
    candidates.append(REAL_HOME / "nltk_data")
    for base in candidates:
        if (base / "tokenizers" / "punkt_tab" / "english").is_dir():
            return base
    return None


PUNKT_DATA = _find_punkt_data()
if PUNKT_DATA is not None:
    os.environ["NLTK_DATA"] = str(PUNKT_DATA)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "needs_punkt: requires NLTK's punkt_tab data, which tests never download"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if PUNKT_DATA is not None:
        return
    skip = pytest.mark.skip(
        reason="NLTK punkt_tab data not found; run: python -m nltk.downloader punkt_tab"
    )
    for item in items:
        if "needs_punkt" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_machine(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    """Give every test an empty HOME and working directory and no provider keys.

    load_settings() reads ~/.tepub/config.yaml and ./.env and ./config.yaml from
    the working directory, so without this a developer's own configuration
    decided whether the suite passed.
    """
    home = tmp_path_factory.mktemp("home")
    work = tmp_path_factory.mktemp("cwd")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in [key for key in os.environ if key.startswith("TEPUB_")]:
        monkeypatch.delenv(name, raising=False)
    yield
