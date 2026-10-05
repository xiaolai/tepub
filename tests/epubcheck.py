"""Run epubcheck and return its findings, for tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

AVAILABLE = shutil.which("epubcheck") is not None


@dataclass(frozen=True)
class Finding:
    severity: str  # FATAL, ERROR, WARNING, USAGE, INFO
    code: str
    path: str
    message: str


def check(epub: Path) -> list[Finding]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            ["epubcheck", str(epub), "--json", str(report)],
            capture_output=True,
            text=True,
        )
        data = json.loads(report.read_text(encoding="utf-8"))
    findings = []
    for message in data.get("messages", []):
        locations = message.get("locations") or [{}]
        findings.append(
            Finding(
                severity=message.get("severity", ""),
                code=message.get("ID", ""),
                path=locations[0].get("path", ""),
                message=message.get("message", ""),
            )
        )
    return findings


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity in ("FATAL", "ERROR")]


def warnings(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "WARNING"]
