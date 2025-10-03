"""Custom exceptions for TEPUB application."""

from __future__ import annotations

from pathlib import Path


class TepubError(Exception):
    """Base exception for all TEPUB errors."""

    pass


class StateFileNotFoundError(TepubError):
    """Raised when a required state file is missing.

    This provides user-friendly error messages with actionable next steps.
    """

    def __init__(self, state_type: str, epub_path: Path):
        """Initialize error with context.

        Args:
            state_type: Type of state file ("segments" or "translation")
            epub_path: Path to the EPUB file being processed
        """
        self.state_type = state_type
        self.epub_path = epub_path
        super().__init__(self._get_message())

    def _get_message(self) -> str:
        """Generate user-friendly error message based on state type."""
        if self.state_type == "segments":
            return (
                f"No extraction state found for '{self.epub_path.name}'.\n"
                f"Please run: tepub extract '{self.epub_path}'"
            )
        elif self.state_type == "translation":
            return (
                f"No translation state found for '{self.epub_path.name}'.\n"
                f"Please run the following commands first:\n"
                f"  1. tepub extract '{self.epub_path}'\n"
                f"  2. tepub translate '{self.epub_path}'"
            )
        return f"State file '{self.state_type}' not found."


class WorkspaceNotFoundError(TepubError):
    """Raised when workspace directory doesn't exist for an EPUB."""

    def __init__(self, epub_path: Path, work_dir: Path):
        """Initialize error with context.

        Args:
            epub_path: Path to the EPUB file
            work_dir: Expected workspace directory path
        """
        self.epub_path = epub_path
        self.work_dir = work_dir
        message = (
            f"No workspace found for '{epub_path.name}'.\n"
            f"Expected workspace at: {work_dir}\n"
            f"Please run: tepub extract '{epub_path}'"
        )
        super().__init__(message)


class CorruptedStateError(TepubError):
    """Raised when a state file is corrupted or has invalid format."""

    def __init__(self, file_path: Path, state_type: str, reason: str):
        """Initialize error with context.

        Args:
            file_path: Path to the corrupted file
            state_type: Type of state file (e.g., "state", "segments")
            reason: Specific reason for corruption
        """
        self.file_path = file_path
        self.state_type = state_type
        self.reason = reason
        message = (
            f"State file is corrupted: {file_path.name}\n"
            f"Reason: {reason}\n"
            f"This may indicate file corruption or incompatibility.\n"
            f"You may need to re-run: tepub extract"
        )
        super().__init__(message)
