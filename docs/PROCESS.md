# pdf-toc-splitter - Development Process

Companion to `agent-routing/pdf-toc-splitter-overlay.yaml`. The tier model,
budgets and profiles are the base policy's (`agent-routing/policy.yaml`) and
are used unchanged, apart from two repository-local overrides at the end of its
`overrides:` list (section 1.2). This document says how a change moves through
them *in this codebase*.

## 0. What we are working on

A Python 3.10+ command-line tool that splits a PDF into one file per table-of-
contents entry and names each file after the entry. It runs locally; the one
dependency is `pypdf` (`>=5,<7`). Output and messages are Vietnamese.

| File | Lines | Role | Risk character |
|---|---:|---|---|
| `pdf_toc_splitter/core.py` | 95 | `parse_toc`, `bookmark_entries`, `safe_name`, `plan`, `split` | Everything that matters. `split()` is the only code that writes |
| `pdf_toc_splitter/__main__.py` | 64 | argparse, password prompt, exit codes | The command-line contract |
| `tests/test_core.py` | 55 | Original unit tests | Kept as is |
| `examples/toc.txt` | 32 entries | The TOC the README tells people to try | Parsed by `check_toc_parser_tables` |

The inputs are people's own documents. The tool's failures are therefore of two
kinds, and the process is organised around them:

1. **Loud and destructive** - writing over a file that exists (including the
   source PDF), or writing outside the output directory. Guarded by the
   no-overwrite invariant and `check_split_roundtrip` / `check_filename_safety`.
2. **Silent and wrong** - the right file name holding the wrong pages (an
   off-by-one in `plan()`), or a TOC line read as a different page. Nothing
   raises. Guarded by `check_page_ranges` / `check_toc_parser_tables`, whose
   assertions are exact page numbers, not "it ran".

## 1. The loop

```
intake -> classify -> execute -> verify -> gate -> PR -> Codex review -> merge -> next
```

### 1.1 Intake

State the change as an action, in one sentence, before scoring anything:
*"this change makes `plan()` end each section one page earlier"*, not *"this
change is about page ranges"*. Signals are verbs; a noun-shaped requirement
produces noun-shaped signals, which produce false overrides.

### 1.2 Classify

`/classify-requirement` (the overlay-aware skill) does both paths and they must
agree:

- **Fast path.** Look the change up in `change_classes`. Each class names the
  signals its change performs.
- **Full path.** Score the six rubric dimensions, extract signals, drop the
  read-only ones, map domain signals onto base signals, and run
  `python3 scripts/route.py --dimensions ... --signals ...`.

The class floors are *machine-applied*, not read off a table by a model.
`scripts/route.py` reads `policy.yaml` and nothing else, so each floor is an
override in the marked block at the end of `policy.yaml`:

| Signal (what the change does) | Override | Minimum tier |
|---|---|---|
| `changes-cli-contract`, `changes-toc-parsing`, `changes-bookmark-reading`, `changes-page-arithmetic`, `changes-output-naming`, `changes-packaging-manifest` | `pdf-toc-splitter-behavior` | T1 |
| `changes-output-write-path` | `pdf-toc-splitter-output-write` | T2 |
| `changes-password-handling` (-> `writes-secret`) | `security-sensitive` | T2 |
| `overwrites-user-file`, `deletes-user-file`, `executes-release-publish` (-> `executes-irreversible-operation` / `deletes-data`) | `irreversible-or-destructive` | T3, human gate |

`tests/harness/test_overlay.py` asserts that every class's `tier_floor` equals
what its signals resolve to through the policy, so the table and the resolver
cannot drift. Scoring may raise the tier above the floor; nothing lowers it.

Before attaching a signal, run it past `discrimination_rules`. The recurring
false positives here:

- reading a PDF or computing a plan vs. writing to disk (exempt vs. T2),
- using the existing `--ask-password` prompt vs. changing how the password is
  obtained (exempt vs. T2),
- rewording the README or a message vs. changing a flag, default or exit code
  (T0 vs. T1).

### 1.3 Execute

Per the base profile for the final tier. The main session analyses, designs and
implements; subagents supply only independent judgement on work it already did.
This repository adds **no role** to the roster.

| Tier | Main session | Subagents | Budget |
|---|---|---|---:|
| T0 | everything | none | 0 |
| T1 | analysis + implementation | `code-reviewer` | 1 |
| T2 | analysis + design + implementation | `code-reviewer`; `architect` only if architecture scored 2 or a `when_any_signal` entry fires | 2 (+1 escalation reserve) |
| T3 | analysis + design + implementation | `architecture-critic`, `code-reviewer-t3`; `requirement-analyst` if ambiguity is 2 | 3 (+1 analyst, +1 escalation) |

The ceiling is enforced by `scripts/hooks/agent_budget.py`, wired in
`.claude/settings.json`, once `route.py --record` has written the ledger. See
`docs/agent-budget.md`. The Codex lanes execute the same tiers with Codex
instead of Claude subagents: `docs/codex-lane.md`, `docs/codex-cli-lane.md`.

### 1.4 Verify

| Tier | Required evidence |
|---|---|
| T0 | `python -m unittest discover -s tests` passes |
| T1 | The checks guarding the touched class (section 2), plus an independent diff review |
| T2 | The above, plus `check_split_roundtrip` for any change to the write path, plus independent review **and** verification by `code-reviewer` |
| T3 | The above, plus an explicit rollback statement, and for `destructive-io` a run showing the original files byte-identical afterwards |

Never report a check as passing that you did not execute. Never call a
`plan()` or `parse_toc` change verified because "the output looked right": the
failure mode is the wrong page, so the evidence is an exact page number.

### 1.5 Gate

Confirm with the user *before* writing a change that would overwrite or delete
an existing file, alter how the PDF password is obtained or handled, or publish
a release (`human_gates` in the overlay). These gate the **change**, not the
running tool. The product invariants - the tool never overwrites an existing
file and never deletes one - hold unconditionally; clearing a gate for one
deliberate change never relaxes them anywhere else.

### 1.6 Pull request, Codex review, merge

CLAUDE.md's *Pull request lifecycle* applies to every PR at every tier: request
the Codex review as soon as the PR is open (`docs/claude-to-codex.md` section 11,
only when `python3 scripts/codex_round_count.py` exits 0), validate each finding
against the code, fix with a regression test or answer with evidence, request
the next review on the new head, and merge under section 20 when the current
head is clean. A PR Codex cannot review is blocked, never merged.

## 2. Deterministic work is not agent work

Every entry has a computable answer, so each is a test module under
`tests/checks/`, not a role. `python -m unittest discover -s tests` runs them
together with the product tests; one at a time:
`python -m unittest tests.checks.check_page_ranges`.

| Check | Guards | Asserts |
|---|---|---|
| `check_page_ranges` | `page-planning` | A table of boundaries through `plan()` (one entry, last section of one page, positive and negative offset, one page per entry), the rejected inputs (page 0, past the end, equal, decreasing, an offset that leaves the document), and over 300 seeded random tables that the sections tile the document with no gap or overlap from the first entry to the last page. |
| `check_toc_parser_tables` | `toc-parser`, `docs-and-examples` | One row per accepted line shape (dot leader, spaced leader, plain space, tab, ellipsis, digits inside the title, Vietnamese text, emoji) and per rejected shape (no page, empty title, trailing junk, negative or decimal page, empty text); the error names the line; `examples/toc.txt` has 32 strictly increasing entries and plans to 8-11 ... 75-80 on an 80-page PDF. |
| `check_filename_safety` | `filename-safety` | Over a hostile corpus (path traversal, drive letters, reserved names with and without extensions, control characters, trailing dots and spaces, 300-character and multi-byte titles, NFD/NFC spellings, case-only duplicates, a title equal to a generated suffix): no separator or forbidden character, no reserved name, at most 180 bytes, NFC, never cut inside a character; and planned file names are case-insensitively unique and every destination resolves inside the output directory. |
| `check_split_roundtrip` | `output-write`, `bookmark-reader`, `page-planning`, `destructive-io` | On a PDF built on the fly with a distinct width per page: every exported page is the planned source page, the exports tile the document from the first entry, bookmarks drive the same contract at levels 1 and 2, the source's SHA-256 is unchanged, a second run is refused and leaves every output byte-identical, one existing destination blocks the run **before** any file is written, and the source PDF cannot be an output destination. |
| `check_cli_contract` | `cli-surface`, `password-handling`, `docs-and-examples`, `packaging` | The flags in `--help` and the flags the README presents are the same set, and exactly the expected seven; no flag takes a secret value and the sources never read the password from `args`, the environment or a log, or print or interpolate it; `--help` exits 0, `--dry-run` prints `start-end name.pdf` lines and writes nothing, errors exit 1 with `Lỗi:` on stderr and nothing on stdout, conflicting TOC sources exit 2; the console script resolves to a callable; the Python floor matches CI. |
| `check_no_documents_committed` | always | No tracked `*.pdf` (any letter case) or `split-output/` file, and `.gitignore` ignores documents, the output directory, the virtualenv and local Claude state. |
| `check_required_checks_exist` | always | Every overlay entry states a `status`; every `implemented` entry is a module that defines a `TestCase`; no `planned` entry already exists; every `tests/checks/check_*.py` is declared. Both directions: one stops policy advertising a gate that does not run, the other stops a gate running outside the policy that governs it. |

`scripts/mutation_check.py --all` proves these tests notice: each entry in
the manifests in `tests/mutations/` breaks one behavior in `core.py` or
`__main__.py` and requires the named test to fail. When you ship a fix with a
test, add the mutation that would have caught it.

What is deliberately *not* a unit check: the claim that the pypdf range
`>=5,<7` works. It is a CI matrix (Python 3.10 and 3.13, pypdf 5 and 6), because
it needs two installed versions of a third-party library.

## 3. Standing risks the process keeps visible

Properties of the code as it stands, not bugs to fix in passing.

| # | Risk | Where | Class / tier |
|---|---|---|---|
| 1 | The no-overwrite guarantee is two layers: a validation loop before any write, and `open('xb')`. Removing either one is a one-line change that the normal tests do not notice unless `check_split_roundtrip` runs. | `core.split` | `output-write` / T2, `destructive-io` / T3 |
| 2 | Pages before the first TOC entry are not exported, and the last section runs to the end of the document (appendices included). Documented in the README, easy to "fix" by accident. | `core.plan` | `page-planning` / T1 |
| 3 | `parse_toc` takes the *last* number on a line as the page, so a title ending in a number needs a dot leader or tab to be read as intended. Documented behaviour; changing it moves pages. | `core.parse_toc` | `toc-parser` / T1 |
| 4 | Text TOCs read from a PDF (`--toc-pages`) depend on the PDF's text layer; scanned PDFs have none and the tool does not OCR. | `__main__.main` | out of scope; say so, do not add OCR in passing |
| 5 | The pypdf range spans two major versions. `outline`, `get_destination_page_number` and `PdfWriter.add_page` are the surface used. | `core.py` | `packaging` / T1, CI matrix |
| 6 | Encrypted PDFs: the password only ever comes from a `getpass` prompt. A `--password VALUE` flag would put it in shell history and the process list. | `__main__.main` | `password-handling` / T2, gated |

## 4. Not installed, on purpose

This repository carries the routing process of `claude-agent-routing-template`
but not the parts that serve other kinds of project:

- evaluation corpus and runner for the classifier (`evals/`) - template-only;
- the agent office dashboard, the free-model VPS team and the CI runner switch -
  separate tools, not lanes;
- the Japanese/English `bilingual` skill - this tool's text is Vietnamese by the
  owner's decision, and adding a language is a product change that needs an
  explicit requirement;
- the `ui-kit` skill - the tool is a CLI with no screen.

`policy.yaml`, `scripts/` and `docs/` are vendored from the template. A few
comments in them mention the tools above; those references are to the template,
not to something missing here. Re-sync by copying the files again and
re-applying the marked block at the end of `policy.yaml`'s `overrides:`.
