#!/usr/bin/env bash
# The local gate: every check that must pass before a commit is called done.
#
# Runs each step in order and stops at the first failure, naming it. Uses the
# project's own interpreter so a stray global python cannot answer for it.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=".venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "verify: no interpreter at $PY; create it with: uv venv .venv && uv pip install -e '.[dev]'" >&2
  exit 2
fi

step() {
  local name="$1"
  shift
  echo "verify: $name"
  if ! "$@"; then
    echo "verify: FAILED at step: $name" >&2
    exit 1
  fi
}

step "lint (blocking: F, E9)" "$PY" -m ruff check src tests --select F,E9
step "tests" "$PY" -m pytest -q -p no:cacheprovider --no-cov -rs

echo "verify: all steps passed"
