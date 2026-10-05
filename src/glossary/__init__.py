"""Book glossaries: approved renderings of recurring terms."""

from .model import (
    GLOSSARY_FILE,
    Glossary,
    GlossaryError,
    Term,
    glossary_for,
    load_glossary,
    plain_text,
)

__all__ = ["GLOSSARY_FILE", "Glossary", "GlossaryError", "Term", "glossary_for", "load_glossary", "plain_text"]
