"""Run a translation whose third call blocks, for the interrupt test.

Usage: python interrupt_driver.py <work_dir> [guarded]
Prints READY once the blocking call has started.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console  # noqa: E402

import translation.controller as controller  # noqa: E402
from config import AppSettings  # noqa: E402
from state.models import (  # noqa: E402
    ExtractMode,
    Segment,
    SegmentMetadata,
    SegmentsDocument,
)
from state.store import save_segments  # noqa: E402

work = Path(sys.argv[1])
guarded = len(sys.argv) > 2 and sys.argv[2] == "guarded"
settings = AppSettings(work_dir=work, translation_workers=1)
settings.ensure_directories()
epub = work / "book.epub"
epub.write_text("stub")
segments = [
    Segment(
        segment_id=f"c-{i}",
        file_path=Path("c.xhtml"),
        xpath=f"/html/body/p[{i}]",
        extract_mode=ExtractMode.TEXT,
        source_content=f"Sentence {i}.",
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=i),
    )
    for i in range(1, 6)
]
save_segments(
    SegmentsDocument(epub_path=epub, generated_at="x", segments=segments), settings.segments_file
)


class SlowThird:
    name = "fake"
    model = "fake"
    calls = 0

    def preflight(self):
        pass

    def translate(self, segment, source_language, target_language):
        SlowThird.calls += 1
        if SlowThird.calls == 3:
            # Give the main thread time to record the first two results, so the
            # test measures the save on interrupt, not this race.
            time.sleep(0.5)
            print("READY", flush=True)
            time.sleep(30)
        return "译文"


controller.create_provider = lambda cfg: SlowThird()
controller.console = Console(file=open("/dev/null", "w"))


def go():
    controller.run_translation(settings, epub, source_language="en", target_language="zh")


# On SIGUSR1, print every thread's stack to stderr: the test asks for it when
# the run does not exit, so a hang shows where it is stuck.
import faulthandler  # noqa: E402
import signal  # noqa: E402

faulthandler.register(signal.SIGUSR1, all_threads=True)

if guarded:
    from cli.main import run_guarded

    run_guarded(go)
else:
    go()
