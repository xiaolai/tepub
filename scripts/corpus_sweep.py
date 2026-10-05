"""Run every EPUB in a folder through extraction and injection, no model involved.

    python scripts/corpus_sweep.py BOOKS_DIR --out results.jsonl [--jobs 6] [--epubcheck 100]

Each book runs in its own subprocess with a time limit, so a crash or a hang
is a finding about that book, not the end of the sweep. Per book it records
one JSON line: the stage that failed and why, or the unit counts and the
invariants that held. Translations are faked: the source text, marked, so
injection exercises every unit. ``--epubcheck N`` also validates the output
of N books chosen at random (seeded) against their sources.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 600


def check_book(book: Path, epubcheck: bool) -> dict:
    """Extract, fake-translate and inject one book; report what held."""
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    import logging
    import os

    from rich.console import Console

    import extraction.pipeline as pipeline
    import injection.engine as engine
    from config import AppSettings
    from extraction.segments import SPLIT_ABOVE_CHARS
    from injection.engine import run_injection
    from state.store import load_segments

    quiet = Console(file=open(os.devnull, "w"))
    pipeline.console = engine.console = quiet
    unmatched: list[str] = []

    class Unmatched(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "no longer matches" in record.getMessage():
                unmatched.append(record.getMessage()[:160])

    logging.getLogger("injection.engine").addHandler(Unmatched())
    result: dict = {"book": book.name}
    stage = "extract"
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as work:
            settings = AppSettings(work_dir=Path(work) / "w")
            pipeline.run_extraction(settings, book)
            segments = load_segments(settings.segments_file).segments
            ids = [s.segment_id for s in segments]
            result.update(
                units=len(segments),
                html_units=sum(s.extract_mode.value == "html" for s in segments),
                longest_unit=max((len(s.source_content) for s in segments), default=0),
                oversized_lists=sum(
                    len(s.source_content) > SPLIT_ABOVE_CHARS
                    and s.metadata.element_type in ("ul", "ol", "dl", "table")
                    for s in segments
                ),
                duplicate_ids=len(ids) - len(set(ids)),
            )

            stage = "inject"
            state = json.loads(settings.state_file.read_text(encoding="utf-8"))
            for segment in segments:
                text = segment.source_content
                translation = text if segment.extract_mode.value == "html" else f"【译】{text}"
                state["segments"][segment.segment_id] = {
                    "segment_id": segment.segment_id,
                    "translation": translation,
                    "provider_name": "sweep",
                    "status": "completed",
                }
            settings.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            output = Path(work) / "out.epub"
            updated, _ = run_injection(settings, book, output, mode="bilingual")
            result.update(updated_documents=len(updated), unmatched_units=len(unmatched))
            if unmatched:
                result["unmatched_sample"] = unmatched[:3]

            stage = "reread"
            if updated:
                # Through the reader tepub itself uses: every spine document of
                # the output must parse as XML again.
                from epub_io.reader import EpubReader

                reader = EpubReader(output, AppSettings(work_dir=Path(work) / "reread"))
                bad = [d.path.as_posix() for d in reader.iter_documents() if d.tree is None]
                result["unparseable_output_documents"] = bad[:5]
                result["unparseable_output_count"] = len(bad)
                source = EpubReader(book, AppSettings(work_dir=Path(work) / "source"))
                result["unparseable_source_count"] = sum(d.tree is None for d in source.iter_documents())

            if epubcheck and updated:
                stage = "epubcheck"
                from collections import Counter

                from tests import epubcheck as checker

                def findings(path: Path) -> list:
                    return checker.check(path)

                source, output_findings = findings(book), findings(output)
                # A file epubcheck could not read to the end in the source (a
                # FATAL, such as an undeclared &nbsp;) cannot be compared: tepub
                # writes it as clean XML, and errors that were always there,
                # unseen, appear. Such files are reported, not counted.
                fatal = {f.path for f in source if getattr(f, "severity", "") == "FATAL"}

                def counts(found) -> Counter:
                    return Counter(
                        (f.path, f.code) for f in checker.errors(found) if f.path not in fatal
                    )

                added = counts(output_findings) - counts(source)
                result["epubcheck_added"] = dict(Counter(code for (_, code), n in added.items() for _ in range(n)))
                if added:
                    result["epubcheck_added_where"] = sorted({path for path, _ in added})[:5]
                result["epubcheck_source_fatal_files"] = len(fatal)

        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 - a finding, recorded
        frames = traceback.extract_tb(exc.__traceback__)
        own = [f for f in frames if "/src/" in f.filename] or frames
        result.update(
            status="error",
            stage=stage,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
            where=f"{Path(own[-1].filename).name}:{own[-1].lineno} {own[-1].name}",
        )
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def _run_one(book: Path, epubcheck: bool) -> dict:
    command = [sys.executable, __file__, "--one", str(book)] + (["--with-epubcheck"] if epubcheck else [])
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"book": book.name, "status": "timeout", "seconds": TIMEOUT_SECONDS}
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if done.returncode != 0 or not lines:
        return {
            "book": book.name,
            "status": "crash",
            "returncode": done.returncode,
            "stderr": done.stderr[-500:],
        }
    return json.loads(lines[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("books", nargs="?", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--epubcheck", type=int, default=0, help="validate this many random books")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--one", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--with-epubcheck", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.one:
        print(json.dumps(check_book(args.one, args.with_epubcheck), ensure_ascii=False))
        return 0
    if args.books is None or args.out is None:
        parser.error("BOOKS_DIR and --out are required")

    books = sorted(args.books.rglob("*.epub"))
    if not books:
        parser.error(f"no .epub files under {args.books}")
    checked = set(random.Random(args.seed).sample(books, min(args.epubcheck, len(books))))
    done = set()
    if args.out.exists():  # resume: skip books already recorded
        done = {json.loads(line)["book"] for line in args.out.read_text(encoding="utf-8").splitlines() if line}
    todo = [book for book in books if book.name not in done]
    print(f"{len(books)} books, {len(done)} already done, {len(checked)} with epubcheck", flush=True)
    with args.out.open("a", encoding="utf-8") as out, ThreadPoolExecutor(args.jobs) as pool:
        futures = {pool.submit(_run_one, book, book in checked): book for book in todo}
        for count, future in enumerate(as_completed(futures), start=1):
            out.write(json.dumps(future.result(), ensure_ascii=False) + "\n")
            out.flush()
            if count % 50 == 0:
                print(f"{count}/{len(todo)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
