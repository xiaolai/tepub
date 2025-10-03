"""Project package root with compatibility aliases."""

import sys
from importlib import import_module

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
    module = import_module(f"{__name__}.{target}")
    sys.modules.setdefault(alias, module)
