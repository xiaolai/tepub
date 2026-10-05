# Improving tepub: findings against origin/main v0.3.3

Written 2026-09-23. Basis: a grill of the repository, re-verified on a detached
worktree of `origin/main` at `01e7360` (v0.3.3). The local `main` at the time
was `4f7da12` (v0.2.3), 36 commits behind, with no unpushed work.

Everything in this file was either reproduced by running code or confirmed by
reading it. Each finding says which. Nothing here is inferred from memory of
how a library "usually" behaves.

## Verdict

The peripheral modules (translation, CLI, config, audiobook, web builder) are
salvageable with targeted fixes, and several of those fixes are release
blockers. The EPUB core (`src/epub_io`, `src/extraction`, `src/injection`,
about 1,100 lines) is not patchable: each of its four foundations produces a
class of defects that the August remediation pass demonstrably could not close.
That argument is made in section F and continued in `02-rewriting-tepub.md`.

## How this was verified

| Step | Command or method |
|---|---|
| Fresh environment | `uv venv --python 3.12 .venv && uv pip install -e '.[dev,all-providers]'` (lxml 6.1.3, ebooklib 0.20.0, mutagen 1.48.1) |
| Test suite, real HOME | `python -m pytest -q --no-cov` |
| Test suite, isolated HOME | `HOME=/tmp/empty python -m pytest -q --no-cov` |
| End to end | `tepub extract book.epub`, fake every translation in `state.json` as completed, `tepub export book.epub --epub`, then `epubcheck` on both outputs |
| ebooklib alone | `load_book` then `epub.write_epub` with no edits, diff the package files, `epubcheck` |
| Unit probes | The script in Appendix A, run against `src/` of the worktree |
| Independent readers | `epubcheck` for EPUBs; `ffprobe` for chapter markers, alongside mutagen |

Test results on `origin/main`:

| Environment | Result | Cause of failures |
|---|---|---|
| Real HOME | 311 passed, 1 failed | `test_write_chapter_markers_roundtrip`: the test is right, the code is wrong (A1) |
| Isolated HOME | 308 passed, 4 failed | The same, plus three sentence-splitting tests that need `punkt_tab` downloaded from the network into HOME first |

## A. EPUB core

Status column: PRESENT means reproduced on `origin/main`; FIXED means it
existed on v0.2.3 and is gone; PARTIAL means the remediation changed it without
closing the mechanism.

| ID | Defect | Mechanism | Evidence | Status |
|---|---|---|---|---|
| E1 | Every content document is written with an empty `<head/>`: stylesheet links, meta tags, inline styles all gone | ebooklib's `EpubHtml.get_content()` rebuilds `<head>` from `self.title` and `self.links`, both empty for items read from an existing file | Pure ebooklib round trip of a clean sample yields `<head/>`; tepub output on origin/main identical | PRESENT |
| E2 | Output fails epubcheck: 2 errors, 4 warnings, from a sample that had 0 errors | ebooklib moves every file into `EPUB/`, regenerates `nav.xhtml` (heading, ids replaced), and writes `<spine toc="ncx">` with no NCX item (OPF-049, RSC-005) | epubcheck on both exports | PRESENT |
| E3 | `_restore_document_structure` cannot restore anything | It runs before ebooklib strips the head again on write. On v0.2.3 it also ran once after the loop on a leaked loop variable, after serialization | Read; output confirms | PRESENT, ineffective |
| E4 | SVG and MathML broken in output | The HTML parser case-folds: `viewBox` becomes `viewbox`, `linearGradient` becomes `lineargradient` | Appendix A, P7 and P9 | PRESENT |
| E5 | Any namespaced element crashes the whole export | HTML parser keeps `epub:switch` as a literal tag; the stored XPath has an undefined prefix; `XPathEvalError` is not caught in `_apply_translations_to_document` | Appendix A, P8, and a direct call: "EXPORT CRASHES: XPathEvalError" | PRESENT |
| E6 | Intermediate XHTML is not well formed | `etree.tostring(method="html")` emits unclosed `meta`, `link`, `br`, `hr` and drops the XML declaration and doctype. ebooklib masks it by re-parsing; it surfaces the moment the writer changes | Appendix A, P7 | PRESENT |
| E7 | `<blockquote><p>…</p></blockquote>` is extracted twice, translated twice, injected twice | The atomic-ancestor guard covers tables and lists only; blockquote is a simple tag | Appendix A, P1 | PRESENT |
| E8 | Nested blockquotes with own text duplicate the inner text | The outer segment's content includes the inner; the inner translation is inserted inside the element later marked `data-lang="original"` | Appendix A, P6 | PRESENT |
| E9 | Translations lose every inline element | Paragraphs are extracted via `text_content()`; translated-only output replaces the element with bare text, so footnote references, links, emphasis and inline images vanish | Appendix A, P5 | PRESENT |
| E10 | Endnote targets orphaned in translated-only output | `_set_html_content` now keeps the container's attributes but every child `id` such as `<li id="fn1">` is gone | Appendix A, P4 | PARTIAL |
| E11 | HTML-mode translation clone lost `data-lang` | `clear()` wiped attributes; now preserved | Appendix A, P3 | FIXED |
| E12 | Segment ids collide across directories with the same basename | Id is `stem + sha1(xpath)[:12]`. Origin/main keeps the scheme and re-keys only colliding groups after extraction, to avoid invalidating workspaces | Appendix A, P2; `resolve_segment_id_collisions` in `extraction/pipeline.py` | PARTIAL |
| E13 | Positional XPath as the join key | Injection must run in reverse `order_in_file` so inserted siblings do not shift later paths; any parser change breaks resolution | Read | Design |
| E14 | No validation gate anywhere | No epubcheck, no well-formedness check, in code or CI | `grep -rniE "epubcheck|XMLParser" src tests .github` | PRESENT |
| E15 | Reading an EPUB whose nav has no `toc` nav crashes with `IndexError` | ebooklib `_parse_nav` indexes an empty list | Observed on a landmarks-only fixture | PRESENT, uncaught |
| E16 | Translated-only CSS is appended to the first stylesheet only and is unused in that mode | Originals are removed in translated-only mode, so the rule has nothing to hide | Read | Minor |

## B. Translation, CLI, config, state

Verified on `origin/main` by a sub-review that re-ran probes in an isolated
HOME. "Code" means confirmed by reading only.

| ID | Defect | Location on origin/main | Evidence | Status |
|---|---|---|---|---|
| C1 | Fatal provider error did not stop the run | `translation/controller.py:427-444` | 12 segments, missing key: 4 calls, 1 ERROR, 11 PENDING | FIXED |
| N1 | Cooldown waits 30 minutes, then the run exits without a retry pass | `controller.py:454-480`, `:520-523` | Cooldown shrunk to 0.4 s: 6 calls, 6 ERROR, 6 PENDING, run ended | PRESENT, new |
| C2 | The documented `.env` onboarding path never delivers the API key | `config/loader.py:91-93`; `providers/openai.py:61` | `.env` merged into the pydantic payload, not `os.environ`; `api_key is None` | PRESENT |
| C3 | PyYAML undeclared; fallback parser produced garbage from nested YAML | `pyproject.toml`; `loader.py:46-49` | Declared; fallback now raises `RuntimeError` | FIXED |
| C4 | Changing provider or model silently wipes every completed translation | `state/store.py:68-102` | COMPLETED became PENDING, translation None, no warning | PRESENT |
| C5 | Per-book config bypassed validation; per-book prompt never installed | `config/models.py:212-221`; `config/workspace.py:39-42` | Now re-validated; per-book prompt reaches `build_prompt` | FIXED |
| N6 | Per-book `skip_rules` in string form raises | `workspace.py:34` vs `loader.py:126-133` | Normalisation is not applied on the per-book path | PRESENT, new |
| C6 | Refusals and truncations saved as COMPLETED | `providers/anthropic.py:54-59`; `cli/debug/commands.py:63` | Anthropic checks `stop_reason`; refusal filter still runs only in the debug command; no truncation check for OpenAI, Grok, Gemini, Ollama | PARTIAL |
| C7 | A literal brace in a custom prompt preamble errors every segment | `translation/prompt_builder.py:65-70` | `str.format` on user text: `KeyError` | PRESENT |
| C8 | OpenAI reasoning output misparsed; no backoff on 429 | `providers/openai.py:15-49`; `providers/http.py:59-66` | Output items scanned now; 429 and 5xx retried with fixed 1 s and 2 s, no Retry-After; DeepL has no retry; no output-token cap except Anthropic | PARTIAL |
| C9 | Config keys that do nothing | `config.example.yaml:118-148`; `providers/base.py:35-42` | `retry`, `rate_limit`, `fallback_provider` parsed and read nowhere; `supports_html` is now enforced | PRESENT |
| C10 | Cross-process lost updates | `state/base.py:44-70`; `store.py:105-116`, `:224-236` | Sidecar lock held in some paths; the hot path `mark_status` and `update_state_atomic` take only the thread lock; no IN_PROGRESS marking | PARTIAL |
| N2 | A transient error aborts the whole run for Anthropic, Gemini, DeepL | `gemini.py:59`; `anthropic.py:47`; `deepl.py:66-68` | Blanket `ProviderFatalError` now meets the new abort | PRESENT, code |
| N3 | `update_state_atomic` does not hold the lock its docstring promises | `store.py:214-236` | Read | PRESENT, code |
| N4 | Moving the EPUB or workspace blocks `translate` and `export` with no override | `controller.py:201-207`; `workspace.py:64-79` | Only `config reset --force` exists | PRESENT, code |
| N5 | `--work-dir` routes commands to different directories | `cli/main.py:95-100`; `core.py:56-57` | `translate` and `extract` use a slug subdirectory; `resume`, `format`, `debug show-pending` read the root | PRESENT, code |
| P1 | State file rewritten in full twice per successful segment | `controller.py:404-412, 421-426, 482-490, 498`; `store.py:105-116` | 5000 records, 3 MB: 405 to 472 ms per success, serialised under a lock; 25 to 35 minutes of main-thread time per book; worker count barely matters | PRESENT |

Design notes, unchanged on origin/main: three of six providers share the HTTP
transport; key lookup differs per provider; the CLI's `extract` and `audiobook
generate` handlers hold business logic; `cli/commands/config.py` is about 490
lines with a third copy of the config documentation. No tests exist for
`store.py`, resume, provider switch, cooldown, the retry pass, or any provider.

## C. Audiobook and web builder

Verified on `origin/main` by a sub-review with ffmpeg 9, mutagen 1.48.1, nltk
3.10.3. Items 1 to 4 produce a run that reports success with wrong output.

| ID | Defect | Location | Evidence |
|---|---|---|---|
| A1 | Chapter markers scaled wrongly | `audiobook/mp4chapters.py:42` | The Nero `chpl` atom uses 100 ns ticks; the code multiplies by the movie timescale, which the pipeline sets to 24000 via `-movie_timescale`. A marker at 2 s reads back at 48 s in both mutagen and ffprobe. Fix: `int(round(seconds * 10_000_000))` |
| A2 | Footnote filtering is dead in production | `audiobook/preprocess.py:251, :312` | Calls `reader.read_document_by_path`, which does not exist on `EpubReader`; wrapped in bare `except`, so notes are narrated in full. All footnote tests pass on a `Mock()` |
| A3 | Pauses encoded at 11025 Hz, stream-copied into a 24 kHz track | `audiobook/assembly.py:660, 675, 757, 795` | A 3 s pause plays as 1.38 s; ffmpeg emits DTS warnings hidden by `capture_output`; chapter offsets drift late |
| A4 | A quote character in the work directory silently truncates the book | `assembly.py:752-804` | `_concat_entry` escaping applied to 3 of 8 concat lines; ffmpeg drops later entries and exits 0 |
| A5 | NLTK download result ignored | `preprocess.py:105-109` | Offline: `LookupError` later; unpinned runtime download |
| A6 | Whole files dropped by substring match on the segment id | `preprocess.py:357` | `authors_note.xhtml`, `keynote.xhtml`, `endnotes.xhtml` vanish, recorded as SKIPPED |
| A7 | Ctrl-C hangs, then exits 0 | `audiobook/controller.py:607, :654` | Interpreter joins in-flight workers, possibly inside a 60 s retry sleep |
| A8 | Cooldown then give up | `controller.py:563, :614-616` | Same shape as N1 |
| A9 | User statement template `.format()` uncaught, after synthesis | `assembly.py:408` | A stray brace raises after all audio is paid for; statements re-synthesised on every assembly, including `--cover-only` |
| A10 | Content before the first or after the last TOC entry is discarded silently | `assembly.py:319-324, :352-354` | Synthesised, paid for, omitted |
| A11 | CJK sentence regex splits on ASCII `.` | `preprocess.py:417` | `版本3.5` becomes two sentences with a pause between |
| A12 | Any ancestor with `class="note"` treated as a footnote definition | `preprocess.py:288-336` | Latent behind A2; activates when A2 is fixed and drops admonition boxes |
| A13 | Chapter preview uses unfiltered segments while assembly filters | `audiobook/chapters.py:120` | Preview lists chapters the book will not contain |
| A14 | `NLTK_DISABLE_IMPORT_SECURITY=1` set process-wide | `preprocess.py:90` | Also when imported as a library |
| W1 | Web builder deletes `class` and `style` while its comment says it preserves them | `webbuilder/dom.py:240-241` | Copied EPUB CSS becomes dead; CJK font and lang hints lost |

Design: `assembly.py` is 843 lines doing six jobs; `subprocess.run(check=True,
capture_output=True)` hides ffmpeg's stderr on failure; only the Nero `chpl`
atom is written, which Apple's players ignore in favour of a QuickTime chapter
track; `mp4chapters.py` depends on mutagen's name-mangled private methods; TOC
hrefs are matched to spine names verbatim without URL decoding; roman numeral
conversion turns a heading that is exactly `C`, `L` or `X` into a number; no
length guard exists against OpenAI's 4096-character limit. Dependencies: pydub
is unmaintained and needs the removed `audioop`; edge-tts is a reverse
engineered endpoint. Web export sanitisation on origin/main is solid and has
real tests.

## D. Packaging, environment, tests

- The local `.venv` pointed at a deleted pyenv interpreter. Rebuilt with uv on Python 3.12; gitignored, nothing committed.
- v0.2.3 imported `html2text` and `yaml` without declaring them, so a clean install could not start the CLI. Both are declared on origin/main.
- Tests read the real `~/.tepub/config.yaml`. On v0.2.3 that caused six failures through the YAML fallback parser; on origin/main it passes only because the real file now parses. There is still no HOME isolation fixture.
- Three tests need NLTK data downloaded into HOME before they can pass.
- CI (`.github/workflows/publish.yml`) runs pytest with ffmpeg installed and gates on `ruff --select F,E9`. No EPUB validation step.

## E. Fix plan, in order

| Order | Work | Agent-hours | Notes |
|---|---|---|---|
| 1 | A1 to A4, plus the reader method behind A2 | 1 | Release blockers; add one end-to-end assembly test with two tone segments, checked by ffprobe |
| 2 | C2, C7, C6 refusal check inside the pipeline | 2 | The path a new user actually walks |
| 3 | P1: keep `StateDocument` in memory, one writer, batched flush; add IN_PROGRESS | 2 to 3 | Or move to SQLite; either removes the quadratic rewrite |
| 4 | N1, N2, C8 backoff with Retry-After | 1 | Cooldown must resume, not exit |
| 5 | Split `assembly.py` into chapter plan, concat, tagging; extract one pass from `controller.run()` | 2 | Makes cooldown and interrupt testable |
| 6 | Test hygiene: HOME isolation fixture, NLTK fixture, tests for `store.py` and resume | 2 | |
| 7 | EPUB core stopgaps if the rewrite is deferred: catch `XPathEvalError` per segment, leaf-block rule for blockquote and paragraph, preserve child ids | 1 | Band-aids; see F |

## F. What patches cannot fix

Four foundations, four defect classes, none closable in place:

| Foundation | Class it produces | Why a patch fails |
|---|---|---|
| ebooklib as the writer | E1, E2, E3, E15 | The head rebuild and package rewrite happen inside `write_epub`; the only fix is not calling it |
| HTML parser on XHTML | E4, E5, E6 | Case folding and prefix loss happen at parse time; every downstream XPath is already wrong |
| Positional XPath keyed by file stem | E12, E13 | Any correct id scheme invalidates existing workspaces, which is exactly why the remediation kept the broken one |
| Plain-text extraction | E7, E8, E9, E10 | Inline structure is gone before translation starts |

The remediation of 2026-08-01 closed 236 findings and left the output unstyled
and failing epubcheck. That is the strongest evidence that the core needs a
different design rather than a longer patch list. The design is in
`02-rewriting-tepub.md`.

## Appendix A: probe script

Run from the repository root with the project's interpreter. Each section is
independent; the expected output on origin/main is noted in the comments.

```python
import sys
sys.path.insert(0, "src")
from pathlib import Path
from lxml import html, etree
from extraction.segments import iter_segments, _build_segment_id
from injection.html_ops import (build_translation_element, prepare_original,
                                insert_translation_after, _set_html_content,
                                _set_text_only)

print("=== P1: blockquote>p double extraction (expect 2 segments, same text) ===")
tree = html.fromstring("<html><body><blockquote><p>Quote text.</p></blockquote></body></html>")
for s in iter_segments(tree, Path("c.xhtml"), 0):
    print("  ", s.metadata.element_type, s.xpath, repr(s.source_content))

print("=== P2: id collision across directories (expect COLLIDE) ===")
a = _build_segment_id(Path("part1/chapter.xhtml"), "/html/body/p[1]")
b = _build_segment_id(Path("part2/chapter.xhtml"), "/html/body/p[1]")
print("  ", "COLLIDE" if a == b else "distinct")

print("=== P3: data-lang on HTML-mode clone (fixed on origin/main) ===")
tree = html.fromstring("<html><body><ul id='n' class='c'><li id='fn1'>x</li></ul></body></html>")
seg = next(iter_segments(tree, Path("c.xhtml"), 0))
ul = tree.xpath(seg.xpath)[0]; prepare_original(ul)
print("  ", dict(build_translation_element(ul, seg, "<li>t</li>").attrib))

print("=== P4: translated-only HTML segment (expect child ids gone) ===")
ul2 = html.fromstring("<html><body><ul id='n'><li id='fn1'>x</li></ul></body></html>").xpath("//ul")[0]
_set_html_content(ul2, "<li>t</li>")
print("  ", dict(ul2.attrib), [c.get("id") for c in ul2])

print("=== P5: translated-only TEXT segment (expect noteref, em, img gone) ===")
p = html.fromstring("<html><body><p id='p1'>T<a id='r1' href='n.xhtml#fn1'><sup>1</sup></a> <em>e</em><img src='i.png'/></p></body></html>").xpath("//p")[0]
_set_text_only(p, "t")
print("  ", html.tostring(p, encoding="unicode"))

print("=== P6: nested blockquote bilingual (expect inner text twice) ===")
tree = html.fromstring("<html><body><blockquote>Outer <blockquote>Inner</blockquote></blockquote></body></html>")
segs = list(iter_segments(tree, Path("c.xhtml"), 0)); root = tree.getroottree()
for s in sorted(segs, key=lambda s: s.metadata.order_in_file, reverse=True):
    el = root.xpath(s.xpath)[0]; prepare_original(el)
    insert_translation_after(el, build_translation_element(el, s, "[T]" + s.source_content))
print("  ", etree.tostring(tree.find("body"), encoding="unicode", method="html"))

print("=== P7: XHTML through html parser + method=html (expect not well-formed, viewbox lowercased) ===")
x = b'<?xml version="1.0"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8"/><link rel="stylesheet" href="s.css"/></head><body><p>a<br/>b</p><svg viewBox="0 0 1 1"/></body></html>'
out = etree.tostring(html.fromstring(x), encoding="utf-8", method="html")
print("  ", out.decode())
try: etree.fromstring(out); print("   well-formed: yes")
except etree.XMLSyntaxError as e: print("   well-formed: NO:", e)

print("=== P8: namespaced element (expect XPathEvalError on re-resolve) ===")
t = html.fromstring(b'<html xmlns:epub="http://www.idpf.org/2007/ops"><body><epub:switch><epub:default><p>d</p></epub:default></epub:switch></body></html>')
xp = t.getroottree().getpath(t.xpath("//p")[0]); print("  ", xp)
try: t.getroottree().xpath(xp); print("   resolves")
except Exception as e: print("   re-resolve FAILS:", type(e).__name__, e)
```

## Appendix B: end-to-end check

```sh
mkdir -p /tmp/t && cp sample.epub /tmp/t/book.epub && cd /tmp/t
HOME=/tmp/t/home OPENAI_API_KEY=sk-fake tepub extract book.epub
python - <<'PY'
import json
seg = json.load(open("book/segments.json")); st = json.load(open("book/state.json"))
for s in seg["segments"]:
    st["segments"][s["segment_id"]] = {"segment_id": s["segment_id"],
        "translation": "T " + s["source_content"][:40], "provider_name": "fake",
        "model_name": "fake", "status": "completed", "error_message": None}
json.dump(st, open("book/state.json", "w"), ensure_ascii=False)
PY
HOME=/tmp/t/home OPENAI_API_KEY=sk-fake tepub export book.epub --epub
epubcheck book/book_bilingual.epub
epubcheck book/book_translated.epub
unzip -p book/book_bilingual.epub EPUB/chapter-1.xhtml | head -8   # observe <head/>
```
