# TEPUB Changelog

All notable changes to TEPUB are documented in this file.

---

## [Unreleased]

The command line, audited command by command against a real book.

### ⬆️ Upgrading

- **`export` writes beside the book**, named after it and the language
  (`book.zh-CN.bilingual.epub`, `book.zh-CN.epub`, `book.zh-CN.web.zip`), and
  writes what its options say: `--mode bilingual|translated|both` (default the
  config's `output_mode`), `--format epub|web`, `--out DIR`. It used to write
  both EPUBs and a web version inside the workspace whatever `--epub` or
  `--output-mode` said. The old flags still work this release, with a warning.
- **`extract` writes only the workspace.** The full unzip and the Markdown
  export, hundreds of files, are now `--raw` and `--markdown`.
- **`translate` and `pipeline` exit with 3 when units failed**;
  `--allow-failures` keeps 0.
- **Only an existing `.epub` runs the pipeline by itself**: a typo is now
  "No such command … Did you mean …?" instead of a missing-file error.
- **`resume` is now `status`**; `resume` still works this release.
- **`export` exits with 3 when translated units could not be inserted**, and
  refuses to run while a translation holds the same workspace.
- **A config file must hold settings**: one holding a lone value, a list or an
  explicit `null` is an error instead of being read as no settings.
- **A book's `skip_rules` add to the inherited ones**, as its `config.yaml`
  says; they replaced them. A `base_url` the book sets is kept over
  `OLLAMA_BASE_URL`.

### ✨ Added

- `tepub status BOOK`: units translated, failed and pending, the engines used,
  glossary misses, outputs, the failed units and the next command.
- `translate --model`, `--provider` and `--dry-run`; an end-of-run summary.
- `tepub config show [BOOK]`: the settings in effect and the file each came
  from, API keys never printed.
- `--version`; global options accepted after the command too; help in
  workflow order with an example.

### 🐛 Fixed

- A book's own `config.yaml` is applied under `--work-dir` too; it was ignored.
- `-q` silences progress bars, and without a terminal progress is plain lines
  instead of escape codes.
- An audiobook run with no terminal no longer picks a voice by itself.
- `format` and the debug commands take the book like every other command.
- The config's `output_mode` accepts `translated`, as `--mode` does.
- Re-extracting an edited book resets the translations of units whose text
  changed, and keeps the rest; a translation was kept by position and
  exported as another paragraph's. Units a skip rule leaves out keep theirs.
- Commands given a book refuse a workspace that belongs to another book.
- `format` and translate leave link targets and other attributes alone; Chinese
  spacing was applied inside them.
- Audiobooks: edited text is narrated again instead of reusing old audio; a
  chapter title can no longer steer which folder is deleted; failed rebuilds
  keep the previous output; permanent errors are not retried.
- Paths and names with brackets print as they are; `[draft]` vanished from
  messages, and some errors crashed the error printer.
- EPUBs made on Windows no longer unzip into folders that cannot be opened.

---

## [0.4.1] - 2026-10-06

Local translation by default, a glossary that holds a book's terms to one
rendering, and inline markup a local model can keep.

### ⬆️ Upgrading from 0.4

- **The default translator is now local**: Ollama with TranslateGemma 12B.
  Install [Ollama](https://ollama.com/) and run `ollama pull translategemma:12b`,
  or, to keep using OpenAI, set it in `~/.tepub/config.yaml`:
  ```yaml
  primary_provider:
    name: openai
    model: gpt-4o
  ```
  Before translating, tepub checks that Ollama answers and has the model, and
  says how to fix it if not.
- **Run `tepub extract` once on each book you are working on.** Links that
  carry no target or anchor are no longer part of a unit, and long lists,
  tables and definition lists are split into their items (segments format 5).
  Every translation carries over: one whose unit changed between text and HTML
  is converted to its new form, a list translated whole is distributed over its
  items, and anything that cannot be carried faithfully is translated again.
  Re-running extract is safe from this release on; see the first fix below.

### ✨ Added

- **A glossary per book.** Each paragraph used to be translated alone, so a
  book's key terms drifted: in 124 paragraphs of one book, "scam compound" came
  out ten different ways. `tepub glossary build` proposes terms from the book's
  index and its recurring names, with renderings from the model, for review;
  saved as `glossary.yaml`, the renderings of the terms in each paragraph go
  into its prompt, and a reply that misses one is retried once with the term
  named. TranslateGemma used an injected rendering in 10 of 10 paragraphs, and
  in 0 of 10 without it. `tepub glossary check` lists finished translations
  that miss a rendering; `--retranslate` redoes only those.
- **Local translation by default** with Ollama and TranslateGemma 12B: free,
  private, no API key. The docs lead with it and show how to choose a cloud
  service instead.
- **`think: false`** for reasoning models on Ollama, such as Qwen 3.5, which
  otherwise spend minutes thinking before each paragraph.

### 🐛 Fixed — data loss

- **Re-running `tepub extract` no longer erases a translated book.** Translate
  stored the target language as a code ("zh-CN") and extract passed its name
  ("Simplified Chinese"); the mismatch rebuilt the whole state as untranslated,
  with no warning and no copy. A workspace's state is now only ever merged
  into, and a real language change is decided only by `translate`, which
  compares language codes, keeps a copy and says so.

### 🐛 Fixed — translation

- **Links, note references and anchors survive a local model** (markers).
  Asked to keep raw HTML, TranslateGemma moved or dropped them in a quarter of
  a link-dense book's paragraphs, which then stayed untranslated. Inline markup
  now travels as numbered markers, ⟦1⟧…⟦/1⟧, and the tags are rebuilt from the
  source; line breaks travel as newlines. On a 20-paragraph sample, 20 passed
  instead of 15. DeepL, which handles tags itself, still gets HTML.
- **A custom prompt can no longer drop what every translation needs.** The
  per-book config that `extract` writes left out `{mode_instruction}`, so the
  model was never told to keep markup; a prompt without it, or naming no target
  language, now gets the missing instruction appended.
- **Long lists, tables and definition lists are split into their items.** A
  book's endnotes, one list of hundreds of notes, made units of up to 34,000
  characters that no request finished. In bilingual output an item's
  translation sits inside it, so list numbering and table columns are kept.
- **A local model gets longer to answer a longer unit**, instead of a fixed two
  minutes that cut long units off on every retry.
- **Replies rejected for their content no longer start a cooldown.** Three in a
  row stopped a run for 30 minutes though the model was answering. The count
  also restarts with each run, instead of carrying over from the last one.
- **A local model is checked, not waited for.** When failures pile up on
  Ollama, tepub asks the server instead of pausing for 30 minutes: if it
  answers, the run carries on at once; if not, the run stops and says how to
  start it. Cloud providers keep the cooldown, which suits rate limits and
  outages.
- **An empty anchor the model dropped is put back** at the start of its
  paragraph, instead of failing the paragraph over a mark no reader sees.
- **Replies fenced as code are unwrapped**, and tags a model invents in plain
  text are refused: a reply returned as "```html … ```" put the fence inside a
  list and showed "<p>" to readers.
- **A note's link survives a model that forgets to close it**: when the reply
  has the note's number right after its opening marker, the pair is closed.
- **A lost line break no longer leaves a unit untranslated**; like emphasis,
  it carries no link or note.
- **Chinese output has no spaces beside full-width punctuation** ("改善。 那个").
- **Bare `<a>` elements left by converters** are unwrapped like spans, instead
  of making a paragraph an HTML unit.

- **`max_tokens` reaches every provider.** It was documented as capping each
  reply, but only Anthropic sent it; OpenAI, Gemini, Grok and Ollama now do,
  and a reply cut at the limit is reported as truncated.

### 🐛 Fixed — reading books

Run over 1,958 real books, extraction and injection found these:

- **A unit's leading text is escaped.** A code sample showing `<html` made a
  source that crashed the book, and an `&` made output that no longer parsed.
- **Oversized units are split** by the size of what is sent: lists of links
  stayed whole at up to 15,000 characters, and one converted book was a
  single unit of 5,000,000 characters.
- **Translated copies leave out page-break markers**, which marked the same
  printed page twice, and inline text filled into a `<blockquote>` sits in a
  paragraph, as EPUB 2 requires.
- **A damaged EPUB is reported plainly**, without a traceback, as are all of
  tepub's own errors.
- **Drawings stay out of requests.** A figure holding an SVG chart is walked so
  its caption is the unit (one book sent 340,000 characters of drawing), and
  an inline SVG or formula travels as one marker and comes back unchanged.

Known limit: a whole chapter converted into one paragraph, its paragraphs
separated by line breaks, is still one unit; if it passes the model's output
limit it is reported as truncated and left untranslated.

### 🐛 Fixed — choosing what to translate

- **Notes the table of contents leaves out are found** by their first line, so
  the default rules skip them as intended.
- **Plural titles match**: "Acknowledgements" is skipped like "Acknowledgement".
- **A section title no longer skips its whole chapter**: one book lost its
  chapter 10 to a closing section titled "Further Reading".

### 🐛 Fixed — docs

- `config.example.yaml` showed alternative providers under keys tepub never
  read, such as `anthropic_provider`; the README showed a `--provider` flag no
  command has. Both now show `primary_provider`.

---

## [0.4.0] - 2026-10-05

A new EPUB core. Each item names its work item in
`dev-docs/plans/2026-10-improve-tepub.md`.

### ⬆️ Upgrading from 0.3

- **Run `tepub extract` once on each book you are working on.** Segments are now
  found by a new rule and get new ids. Extraction carries every finished
  translation and synthesised audio clip over to the new units by matching file
  and text, after saving a timestamped copy of the state; it reports anything it
  could not match, which is translated again. On a real 1,623-segment workspace
  every translation carried over.

### 🐛 Fixed — the EPUB written back

- **Translated books are valid EPUBs** (WI-4.1). The writer copies the source
  book entry for entry and replaces only the chapters it translated. The old
  one, through ebooklib, rebuilt every chapter's head, moved every file and
  rewrote the navigation; written back unchanged, 15 of 19 real books gained
  epubcheck errors. Now none does, translated or not.
- **Translated chapters keep their title and stylesheet links** (WI-4.2).
  ebooklib's reading API returned chapters with an empty head.
- **SVG covers, MathML and `epub:switch` survive** (WI-4.2). Chapters are
  parsed as XML; the HTML parser lowercased SVG names and crashed on prefixed
  elements.
- **No text is translated twice or lost** (WI-4.3). A paragraph inside a
  blockquote used to be extracted twice; text beside block children, and lists
  wrapped in `<span>`, are now covered. Across 10,583 chapters of a 19-book
  corpus every piece of text is in exactly one unit, skipped on purpose (SVG,
  MathML, code listings), or reported.
- **Footnotes, links and emphasis survive translation** (WI-4.5). Paragraphs
  with inline markup are translated as HTML, and each reply must keep its
  links, anchors and images or it is retried once and then marked as an error.
  Measured with TranslateGemma 12B into Chinese: 30 of 30 paragraphs kept their
  markup.
- **Bilingual output is valid in EPUB 2 too** (WI-4.6). Originals and
  translations are marked with `tepub-original` and `tepub-translation`
  classes; `data-lang` is added only in EPUB 3, and translated copies carry no
  duplicate ids.
- **Both tables of contents are retitled** in translated-only output, the EPUB 3
  nav and the NCX, and publisher stylesheets are no longer edited.

### 🐛 Fixed — reading books

- **ebooklib is no longer used** (WI-4.7). Its navigation parser crashed on a
  nav with landmarks only. The spine, contents and metadata are read directly.
- **A moved or renamed book is still recognised** (WI-5.3). Workspaces record
  the book's SHA-256 and accept the same content wherever it sits.
- **`resume`, `format` and the debug commands find the right workspace** under
  `--work-dir` (WI-5.3).

### 🐛 Fixed — audiobooks

- **Files the table of contents leaves out are no longer dropped** (WI-5.2).
- **The chapter preview matches the book** (WI-5.2).
- **Statements are synthesised once** per wording and voice (WI-5.2).
- **Chinese sentences no longer split inside numbers** such as 3.5 (WI-5.2).

### 🔧 Changed

- **The web export keeps classes and language attributes** (WI-5.4), so its
  original/translation toggles work for EPUB 2 books.
- **`assembly.py` is split** into modules with one job each (WI-5.1).

### 🧪 Development

- Fixture books and a translating epubcheck gate run in the default suite;
  `TEPUB_CORPUS_DIR` runs the same gate over a folder of real books.

---

## [0.3.4] - 2026-10-05

Each item names its work item in `dev-docs/plans/2026-10-improve-tepub.md`.

### 🐛 Fixed — audiobooks

- **Chapter markers pointed at the wrong times** (WI-1.1). Start times were
  scaled by the movie timescale instead of the fixed 100 ns unit the chapter
  atom uses; with the pipeline's 24 kHz timescale a chapter meant for 2 s
  appeared at 48 s. The writer now also reads its chapters back and stops if
  any start time is off.
- **Footnotes were read aloud** (WI-1.2). The filter called a reader method that
  did not exist, inside a catch-all. Note bodies marked by `epub:type`, role or
  id are now skipped; an admonition box with `class="note"`, and files named like
  `authors_note.xhtml`, are no longer dropped.
- **Pauses were too short and chapters drifted late** (WI-1.3). Silence was made
  at 11025 Hz and copied beside 24 kHz speech. All joining now goes through one
  helper that makes silence in the speech's own format and refuses mixed inputs.
- **An apostrophe in the work folder cut the book short** (WI-1.4). Every concat
  path is escaped, and an output shorter than its inputs stops the run.
- **Opening and closing statements could vanish silently** (WI-1.5).

### 🐛 Fixed — translation

- **API keys in `.env` were ignored** (WI-2.1). Only the provider variables
  tepub reads may be set from `.env`; anything else is ignored with a warning.
- **A brace in a custom prompt broke every segment** (WI-2.2). Only named
  placeholders are filled; other braces are text.
- **Refusals and truncated replies were saved as translations** (WI-2.3).
- **One rate-limit reply could end a run** (WI-2.4). Errors are classified once:
  bad keys stop the run, rate limits and server errors are retried honouring
  `Retry-After`, other rejections fail only their segment. A cooldown now
  resumes the run instead of ending it.
- **Changing model erased finished translations** (WI-2.5). Only a change of
  language resets, after saving a timestamped copy of the state.
- **The Ollama address in the docs did not work** (WI-2.6). A server address
  such as `http://localhost:11434` now gets `/api/generate` appended.

### ⚡ Faster and safer runs

- **State writes no longer slow every segment** (WI-3.1). State is kept in
  memory and saved in batches; 5000 segments now cost about 1 ms each in total,
  down from about 400 ms each for state writes alone.
- **One run per workspace** (WI-3.1, WI-3.3). A second `translate`, or `format`
  during a run, stops with a clear message instead of overwriting progress.
- **Ctrl-C exits at once with code 130** (WI-3.2), after saving progress. It
  used to hang until in-flight requests finished; the audiobook command also
  exited 0.

### 🔧 Removed

- `retry`, `rate_limit` and `fallback_provider` settings, which did nothing
  (WI-2.6). Configs that still name them get a warning.

### 🧪 Development

- Tests no longer read the developer's own config, `.env` or API keys (WI-0.2).
- `scripts/verify.sh` is the local gate (WI-0.3).
- Opt-in live tests against a real model server: set `TEPUB_LIVE_BASE_URL`
  (WI-0.4).
- The PyPI publish action is pinned to a commit.

---

## [0.3.3] - 2026-08-02

### 🐛 Fixed

- **Audiobook synthesis failed when run from the home directory.** 0.3.2 moved
  nltk off the CLI startup path, which fixed `tepub --help` and every non-audio
  command, but sentence splitting still imported nltk and still hit the same
  guard — so `tepub audiobook generate` crashed from `~`, `/` or `~/.local`.

  nltk's import guard (`inisec`, added in 3.9.2) blocks any module whose file
  lives *underneath* the current working directory, rather than modules actually
  resolved *from* it. Tool installers place environments under `$HOME`, so the
  guard fires on ordinary layouts; this is upstream nltk#3730, with a fix pending
  in nltk#3731. Its documented remedies (`-P`, `PYTHONSAFEPATH=1`) do not work,
  because the check never consults `sys.path`.

  tepub now disables that guard for its own nltk import only. This is safe here
  and not a weakening of security: tepub runs as an installed console script,
  where `sys.path[0]` is the script's directory and the current working directory
  is never on `sys.path`, so the path-hijacking attack the guard defends against
  cannot occur. The override is scoped to the lazy import — nothing is set at
  startup — and should be removed once a release carrying nltk#3731 is required.

---

## [0.3.2] - 2026-08-02

### 🐛 Fixed

- **The CLI would not start from certain directories.** nltk's import guard
  refuses to load any module whose file lives under the current working
  directory. Tools installed with `uv tool` live under `$HOME`, so running
  `tepub` from `~`, `~/.local` or `/` aborted before the CLI came up. nltk was
  reaching the startup path only because command registration imports the
  audiobook module, which imported it at module scope.

  nltk is now imported on demand, inside the sentence-splitting helpers that
  actually need it. Verified working from `/`, `~`, `~/.local`, `/tmp` and a
  project directory.

### ⚡ Performance

- Roughly 76 ms shaved off every invocation: nltk is no longer imported for
  commands that never use it, which is all of them except audiobook synthesis.

---

## [0.3.1] - 2026-08-02

### 🐛 Fixed

- **tepub could not run on Python 3.13.** PEP 594 removed `audioop` from the
  standard library in 3.13, and `pydub` imports it, so `import cli.main` failed
  outright — the CLI would not start. `requires-python` had no upper bound, so
  installers were free to pick 3.13 and produce a broken install. The maintained
  `audioop-lts` backport is now required on 3.13 and above.

  This affected 0.3.0 on Python 3.13 only; 3.10-3.12 were unaffected.

### 🔧 Internal

- CI now tests Python 3.13 alongside 3.10-3.12. The matrix stopping at 3.12 is
  why this reached PyPI.

---

## [0.3.0] - 2026-08-02

Remediation of a 236-finding code audit. All 44 high-severity findings closed.

Released as a minor version rather than a patch to signal the breadth of change
(83 files) and the two newly-required dependencies. **No migration is needed and
no re-extraction is forced** — existing workspaces continue to work.

### 🔒 Security

These affect anyone who processes an EPUB from an untrusted source.

- **Path traversal on EPUB extraction.** Archive member names were joined
  directly onto the output directory. An absolute member name discards the base
  path entirely, and `..` components walk out of it, so a crafted EPUB could
  write anywhere the process had permission. Member names are now validated
  before anything is written (`UnsafeArchiveMemberError`).
- **Path traversal in the web exporter.** The same class of bug, unguarded, when
  copying manifest resources into the export directory.
- **Stored XSS in the web export.** `<script>` elements and inline event handlers
  (`onerror`, `onclick`, …) were never removed from book content, and
  `javascript:` URLs were explicitly preserved. Active content is now stripped,
  including SVG/MathML foreign content: literal `xlink:href`, animation elements
  that assign an href at runtime (`<animate>`, `<set>`), and URL-bearing
  attributes in any namespace or prefix.
- **Script breakout via book metadata.** Book data is embedded in a `<script>`
  element, but `<` was not escaped, so a title containing `</script>` closed the
  element and the remainder became live markup.
- **Sanitisation was conditional.** URL cleaning ran only when an optional
  argument was supplied, so the default code path sanitised nothing.

### 🐛 Data-loss fixes

- **`tepub extract` destroyed translations.** Re-running extraction wrote a fresh
  all-pending state, discarding every completed translation. It now merges.
- **Concurrent writes could clobber each other.** `atomic_write` locked a shared
  temporary path and replaced the target after releasing the lock. Three
  commands (`format`, `debug purge-refusals`, pre-injection polish) also did
  unlocked read-modify-write cycles that overwrote concurrent progress.
- **Segment id collisions.** Two files with the same basename in different
  directories produced identical segment ids, so one segment's state silently
  overwrote the other's. Only genuinely colliding segments are re-keyed, so
  existing workspaces keep working and no completed work is lost.

### 🔧 Correctness

- Chapter YAML could never round-trip: segment lists were written as a truncated
  comment, and unescaped titles produced invalid YAML.
- Chapter audio was reused based on file existence alone, serving stale audio
  after a voice, speed or text change. Changing any audio-affecting setting now
  invalidates completed segments.
- Audiobook synthesis crashed on a clean install (NLTK 3.9 moved the Punkt data).
- CJK text was tokenised with the English sentence splitter and came back as one
  unbroken sentence.
- `--work-dir` was silently ignored; `--quiet` had no effect on most output;
  `--verbose` was reset by the next logger created.
- Skip keywords matched as substrings, so "cover" matched "Discovering" and
  ordinary chapters were excluded from translation.
- A provider apology alone counted as a refusal, resetting good translations.
- `{language_instruction}`, documented as a prompt placeholder, raised KeyError.
- Traditional Chinese was silently translated as Simplified by DeepL.
- Anthropic responses longer than the token limit were silently truncated.
- A single 429 rate-limit response was treated as fatal instead of retried.

### 📦 Packaging

- **`html2text` and `PyYAML` are now declared dependencies.** Both were imported
  but never listed, so `tepub extract` and config parsing failed on a clean
  install. **No action needed on upgrade** — pip installs them.
- Optional provider extras added: `pip install tepub[anthropic]`,
  `tepub[gemini]`, `tepub[all-providers]`.
- Test coverage measured only six of eleven packages; now measures all of `src`.

### ⚠️ Notes

- No migration is required and no re-extraction is forced.
- Providers that cannot preserve HTML now fail loudly rather than silently
  mangling markup.
- `config validate` now reports unrecognised keys and actually validates
  per-book configs (it previously reported success regardless).

---

## [0.2.0] - 2025-01-XX

### 🎉 Major New Features

#### **Dual TTS Provider Support**
- **OpenAI TTS Integration**: Premium text-to-speech with 6 high-quality voices
  - Voices: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
  - Two quality tiers: `tts-1` (standard) and `tts-1-hd` (premium)
  - Adjustable speed: 0.25x to 4.0x
  - Direct AAC output for optimal quality
  - Cost: ~$11-22 per 300-page book
- **Edge TTS** (Microsoft): Free, 57+ voices in multiple languages (remains default)
- Provider-specific output directories: `audiobook@edgetts/` and `audiobook@openaitts/`
- CLI options: `--tts-provider`, `--tts-model`, `--tts-speed`, `--voice`
- Settings persist across sessions for easy resumption

#### **Enhanced Configuration System**
- **Comprehensive Documentation**: Completely rewritten `config.example.yaml`
  - Accurate system prompt documentation matching actual codebase
  - Clear explanations of all placeholders and auto-generated instructions
  - Directory structure clarification (work_root, work_dir, cache_dir)
  - TTS provider comparison with cost breakdowns
  - Real-world examples for different book types
- **Environment Variables**: Expanded support with better organization
  - Added `TEPUB_CACHE_DIR` for custom cache locations
  - Organized into API Keys, Service URLs, Directories, and Audiobook sections

### ⚡ Performance Improvements

#### **OpenAI TTS Optimization**
- Direct AAC output format (instead of MP3 → AAC conversion)
- Eliminates intermediate conversion step for better quality
- Faster processing with lower memory usage
- Matches M4A container format natively

### 📚 Documentation

#### **Complete Documentation Overhaul**
- **README.md**: Rewritten for clarity and completeness
  - Comprehensive OpenAI TTS documentation
  - Updated cost comparisons for all services
  - Clear examples for both TTS providers
  - Provider-specific folder structure explained
- **INSTALL.md**: Updated with OpenAI TTS setup instructions
- **config.example.yaml**: Thoroughly updated to match codebase implementation

### 🔧 Configuration Changes

**New Settings:**
- `audiobook_tts_provider`: Choose between "edge" or "openai" (default: "edge")
- `audiobook_tts_model`: OpenAI model selection ("tts-1" or "tts-1-hd")
- `audiobook_tts_speed`: Speech speed for OpenAI TTS (0.25-4.0, default: 1.0)

**Enhanced Settings:**
- `work_root`: Global TEPUB directory (default: `~/.tepub/`)
- `work_dir`: Per-book workspace (default: next to EPUB file)
- `cache_dir`: Temporary files (default: `work_root/cache`)

### 🛠️ Technical Improvements

- **TTS Abstraction Layer**: Clean provider interface for easy extensibility
- **Factory Pattern**: `create_tts_engine()` for provider instantiation
- **State Management**: TTS provider settings saved in audiobook state
- **Graceful Degradation**: Optional OpenAI dependency with clear error messages
- **Provider Detection**: Auto-selects file format based on TTS engine (.aac for OpenAI, .mp3 for Edge)

### 📦 Dependencies

**Added:**
- `openai>=1.0`: Required for OpenAI TTS support (included by default)

---

## [0.1.0] - 2024-XX-XX

### 🎉 Initial Public Release

#### **Core Features**

**Translation**
- Multi-language book translation using AI services
- Support for OpenAI, Anthropic Claude, Google Gemini, xAI Grok, DeepL, and Ollama
- Two output modes:
  - **Bilingual**: Original and translation side-by-side
  - **Translation-only**: Professional translated edition
- Automatic language detection
- Parallel processing with configurable workers
- Resume capability for interrupted translations
- Smart skip rules for front/back matter
- Customizable translation prompts

**Audiobook Generation**
- Text-to-speech using Microsoft Edge TTS
- 57+ voices in multiple languages
- Chapter-based navigation with TOC markers
- Automatic cover art detection and embedding
- M4B format with chapter metadata
- Resume capability for long books
- Configurable voice, rate, and volume

**Export Formats**
- **EPUB**: Bilingual and translation-only editions
- **Web**: Interactive HTML viewer with live translation toggle
- **Markdown**: Plain text export with images and formatting

**Configuration**
- Two-level config system (global and per-book)
- YAML-based configuration
- Environment variable support
- Custom skip rules
- Provider failover (automatic fallback)
- Retry logic with exponential backoff

#### **CLI Commands**

```bash
tepub extract <epub>           # Extract book structure
tepub translate <epub>         # Translate content
tepub export <epub>            # Generate output files
tepub audiobook <epub>         # Create audiobook
tepub pipeline <epub>          # All-in-one workflow
tepub debug                    # Diagnostic tools
```

#### **Technical Stack**
- Python 3.10+ required (3.11+ recommended)
- Pydantic for configuration validation
- Rich for terminal UI
- Click for CLI framework
- ebooklib for EPUB handling
- FFmpeg for audiobook assembly
- Edge TTS for text-to-speech

---

## Version History

- **0.2.0** (2025-01-XX): OpenAI TTS support, enhanced configuration
- **0.1.0** (2024-XX-XX): Initial public release

For detailed commit history, run: `git log --oneline --decorate`

---

## Upgrade Notes

### 0.1.0 → 0.2.0

**Breaking Changes:**
- None! Fully backward compatible.

**New Features You Can Use:**
- Set `audiobook_tts_provider: openai` in config to use OpenAI TTS
- Use `--tts-provider openai` flag for one-time OpenAI audiobook creation
- Audiobooks now save to provider-specific folders (allows creating both versions)

**Configuration Migration:**
- Old configs work without changes
- Add `OPENAI_API_KEY` environment variable to enable OpenAI TTS
- Review new `config.example.yaml` for enhanced documentation

**Directory Structure:**
- Old: `mybook/audiobook/mybook.m4b`
- New: `mybook/audiobook@edgetts/mybook.m4b` or `mybook/audiobook@openaitts/mybook.m4b`
- Legacy `audiobook/` folders remain compatible

---

**Questions?** Check [README.md](README.md) or open an issue on [GitHub](https://github.com/xiaolai/tepub/issues)
