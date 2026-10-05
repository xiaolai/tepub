"""Fill named placeholders in user-written templates.

str.format treats every brace as syntax, so a custom prompt that asked for JSON
output, {"translation": "..."}, raised KeyError, and so did any statement with a
stray brace. Only the names the caller offers are filled here; any other brace
is text. A doubled brace still means one brace, so templates written for
str.format keep working.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fill_placeholders(template: str, values: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        name = match.group(1)
        return str(values[name]) if name in values else token

    return _TOKEN.sub(replace, template)
