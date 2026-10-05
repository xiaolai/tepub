# CLI audit

2026-10-06, tepub 0.4.1. Every command's help was read, and each command was
run against a real book with the mistakes users make: typos, steps out of
order, options in the wrong place, and flag combinations. Exit codes were
measured directly, not through a pipe.

## What works

- Running steps out of order is caught with a clear next step: "No extraction
  state found for 'book.epub'. Please run: tepub extract 'book.epub'". The exit
  code is 1, and usage errors exit with 2.
- tepub's own errors print one red line, not a traceback (fixed in 0.4.1).
- `config validate`, `debug workspace` and the `glossary` commands do what
  their help says.

## Findings

### A. Wrong or misleading (fix first)

| # | Finding | Evidence |
|---|---|---|
| A1 | `export` ignores `--epub` and `--output-mode` when choosing EPUBs | `export --epub` ("Export only the translated EPUB") writes both EPUBs. `export --output-mode translated-only` writes the bilingual EPUB, the translated EPUB and the web version. |
| A2 | `translate` ends with no summary and always exits 0 | A run that leaves units failed looks the same as a clean one, to people and to scripts. |
| A3 | An unknown command is routed to `pipeline` | `tepub transalte book.epub` says "Path 'transalte' does not exist", not "no such command, did you mean translate?". `tepub book.epub` starts a full translation of the book without asking, which costs money on a cloud provider. |
| A4 | `extract` writes far more than units | On one book it wrote 286 files, 28 MB: a full unzip (`epub_raw`, 157 files) and a Markdown export with 99 images. None of it is mentioned, needed for translating, or optional. |
| A5 | `-q` does not silence progress bars | `tepub -q extract` still draws the progress bar. Without a terminal, live displays fill logs with escape codes. |
| A6 | The audiobook voice is chosen silently without a terminal | Run without `--voice` and without a terminal, it picked `en-AU-NatashaNeural` for an American book and started synthesising. |

### B. Inconsistent

| # | Finding |
|---|---|
| B1 | Global options (`--work-dir`, `--config`, `-v`, `-q`) work only before the subcommand: `tepub resume --work-dir X` says "No such option". |
| B2 | Some commands take the book and some do not: `resume`, `format`, `debug show-pending`, `inspect-segment` and `purge-refusals` look for a workspace in the current folder (`./.tepub/state.json`). |
| B3 | One setting has two spellings: the config wants `translated_only`, the command line `translated-only`, and each rejects the other. |
| B4 | Output EPUBs land inside the workspace (`book/book_bilingual.epub`), named after the workspace, not next to the source book. |
| B5 | `export`'s options overlap: `--epub`, `--web` and `--output-mode` describe the same choice three ways. |

### C. Missing

| # | Missing | Why it matters |
|---|---|---|
| C1 | `--version` | It is the first thing anyone checks when reporting a problem. |
| C2 | A per-run `--model` (and `--provider`) | Comparing two local models means editing the config file between runs. |
| C3 | Status for one book | `resume` takes no book and shows three numbers. A book's status should show translated, pending and failed units, glossary misses, and the provider and model it will use. |
| C4 | `--dry-run` | Units, characters and estimated time or cost before a long run. |
| C5 | `config show` | The merged configuration, and which file each value came from. |

### D. Help text

- The top-level help lists commands alphabetically, with no workflow order and
  no example; a new user cannot see that extract → translate → export is the
  path.
- `translate`'s help does not say which provider and model it will use, and
  `pipeline` repeats `export`'s misleading `--epub` text.

## Proposed design

Keep the four verbs, make them honest, and add status:

```
tepub extract   BOOK                 units only; --raw / --markdown opt-in
tepub translate BOOK [--to] [--model M] [--provider P] [--dry-run]
                                     summary at the end; exit 3 if units failed
tepub export    BOOK [--mode bilingual|translated|both] [--format epub|web]...
                     [--out DIR]     default: next to the book
tepub status    BOOK                 units, failures, glossary, provider/model
tepub pipeline  BOOK ...             extract + translate + export
tepub BOOK.epub                      kept as a shortcut only when the argument
                                     is an existing .epub; anything else is
                                     "No such command 'x'. Did you mean …?"
```

Plus, across commands:
- global options accepted after the subcommand too;
- `--version`;
- `-q` and the absence of a terminal both turning live displays off;
- the commands that take no book (`resume`, `format`, debug) taking `BOOK`
  like the rest, with `resume` folded into `status`.

### Breaking changes and their cost

| Change | Who it breaks | Mitigation |
|---|---|---|
| `export` writes what its flags say, next to the book | Scripts that expect files inside the workspace | One release in which old flags still work, with a warning |
| `extract` stops writing `epub_raw` and Markdown by default | Anyone using those folders | `--raw` and `--markdown`, or `export --format markdown` |
| `translate` exits 3 when units failed | Scripts treating any non-zero exit as fatal | Documented code; `--allow-failures` keeps exit 0 |
| An unknown command is no longer `pipeline` | Nobody relying on typos | None needed |

## Effort

About 4 to 6 agent-hours, mostly mechanical: options, help, tests per
command. The irreducible part is the design above, chiefly `export`'s flags
and the exit-code contract, about an hour, and the owner's decision on the
breaking changes. There are no clock-time waits beyond the test suite.
