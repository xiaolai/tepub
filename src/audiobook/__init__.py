from .controller import run_audiobook

# Intentional re-export: cli.commands.audiobook imports run_audiobook from here.
__all__ = ["run_audiobook"]
