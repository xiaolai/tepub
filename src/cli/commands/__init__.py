"""CLI commands module."""

import click

from cli.commands.audiobook import audiobook
from cli.commands.config import config
from cli.commands.export import export_command
from cli.commands.extract import extract
from cli.commands.format import format_cmd
from cli.commands.pipeline import pipeline_command
from cli.commands.resume import resume
from cli.commands.translate import translate


def register_commands(app: click.Group) -> None:
    """Register all CLI commands with the app."""
    app.add_command(extract)
    app.add_command(translate)
    app.add_command(audiobook)
    app.add_command(export_command)
    app.add_command(pipeline_command, name="pipeline")
    app.add_command(resume)
    app.add_command(format_cmd, name="format")
    app.add_command(config)
