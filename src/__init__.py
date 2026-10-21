"""Project package root with compatibility aliases.

The installed layout puts this directory on the import path, so the modules
inside it import each other by their canonical top-level names (`config`,
`state`, ...). Importing `src` from a checkout must establish those names
first; importing `src.config` directly ran its `import config...` lines before
the alias existed.
"""

import sys
from importlib import import_module
from pathlib import Path

# The canonical namespace: the directory that holds config, state and the rest.
_src = Path(__file__).resolve().parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_aliases = {
    "config": "config",
    "debug_tools": "debug_tools",
    "epub_io": "epub_io",
    "extraction": "extraction",
    "injection": "injection",
    "logging_utils": "logging_utils",
    "state": "state",
    "translation": "translation",
    "translation.providers": "translation.providers",
    "webbuilder": "webbuilder",
    "web_templates": "web_templates",
}

for alias, target in _aliases.items():
    module = import_module(target)
    # Registered under both names before a dependent imports a sibling, so a
    # file is never loaded twice as `config` and `src.config`.
    sys.modules.setdefault(alias, module)
    sys.modules.setdefault(f"{__name__}.{target}", module)
    # A pre-registered submodule does not get the attribute import normally
    # sets on its parent, so `import src` followed by `src.config` failed.
    parent_name, _, leaf = target.rpartition(".")
    parent = (
        sys.modules.get(f"{__name__}.{parent_name}")
        if parent_name
        else sys.modules[__name__]
    )
    if parent is not None:
        setattr(parent, leaf, module)
