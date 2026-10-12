---
name: repo-layout
description: Keep a repository's root to the files a user needs to run the app — entry-point launchers, README, dependency files — and put tests, library modules, helpers and data in their own directories. Use whenever a requirement adds a new Python file, test, script or module to a repository created from this template, when scaffolding a new app or tool, when the user says the root is cluttered, hard to tell which file starts the app, "sắp xếp lại thư mục", "dọn thư mục gốc", "フォルダ構成を整理", or asks to restructure/reorganize a repository's layout.
---

# Repository layout

A user opening the repository must see, in the root, which file starts the app.
Forty `.py` files side by side — the app, its helpers and every test — hide
that. This skill fixes where each kind of file goes, and
`python3 scripts/layout_check.py` (repository root) is the gate: whether a root
`.py` is a test, a library module or an entry point is computable, so it is a
script and a CI step, not something a reviewer eyeballs.

Like the other implementation rules, this changes what the implementation step
produces, not who performs it: it adds no role and raises no rubric dimension.
This repository's own harness (`scripts/`, hooks) already follows it.

## The layout

```text
<repo>/
├── README.md  CLAUDE.md  AGENTS.md  LICENSE  .gitignore
├── requirements.txt  requirements-dev.txt  (or pyproject.toml)
├── <app>.py              # entry point(s) only — thin launchers, at most 3
├── <app>_gui.py          #   e.g. one CLI + one GUI
├── <app>/                # the app's package: every module the launchers import
│   ├── __init__.py
│   ├── core.py
│   ├── report.py
├── tests/                # every test_*.py, conftest.py
│   ├── support/          # test helpers, fakes (was test_support/)
│   └── fixtures/         # sample input files
├── scripts/              # developer/ops tooling that is not the app
├── docs/
└── data/ or samples/     # sample inputs a user may need; never generated output
```

Rules, each one checked by `layout_check.py` unless marked *(judgement)*:

1. **Tests live in `tests/`.** `test_*.py`, `*_test.py`, `conftest.py`, and
   anything named like a test (`run_tests_sample.py`), and JavaScript/TypeScript
   tests (`x.test.mjs`, `test-x.js`, `x_test.js`, `test.js`, `app.spec.ts`, `CalculatorSpec.js`) never
   sit in the root.
   Test helper directories go under `tests/` (`tests/support/`), never a root
   `test_support/`. A root `spec/`/`specs/` that holds test code (`user_spec.rb`)
   is a test directory too; a Spec Kit `specs/` of documents is not.
2. **A root `.py` (or `.pyw`, any letter case) is an entry point** when it has a top-level
   `if __name__ == "__main__":`, or declares itself with the comment
   `# layout: entry-point` in its first 10 lines. A launcher without a main
   guard (a Streamlit app started by `streamlit run app.py`, a script a
   scheduler runs) carries the marker. Both are read exactly; the check never
   guesses from what the code does. Anything else is a library module and
   belongs in the package. `setup.py` and `noxfile.py` are exempt. The marker
   is a declaration, so it goes on real launchers only, never on a module to
   wave it through.
3. **At most 3 Python entry points in the root** (`.py`/`.pyw`; a `.bat` or
   `.sh` shortcut that starts one of them is not counted, and is not checked)
   (`--max-root-scripts` changes the limit when
   a repository genuinely ships more launchers; record why in its CLAUDE.md).
4. **A packaging exemption is a named list, never a pattern.** When the build
   needs library modules beside the entry point (a PyInstaller EXE whose
   tooling asserts the sibling layout), pass each one as
   `--allow-root-module <name>.py` (the exact file name, letter case included)
   in CI and record why in CLAUDE.md. Every
   other root module still fails, and a listed name that no longer is a root
   library module fails too, so the list cannot rot. Prefer moving the modules
   when the packaging allows it; the exemption is for when it does not.
5. **A launcher stays thin** *(judgement)*: parse arguments, call into the
   package. Logic in a launcher cannot be imported by a test without running
   the launcher.
6. **Generated output never lands in the root** *(judgement)*: reports, logs,
   exports go to an `output/` (gitignored) or a user-chosen path.

For a new tool, use a plain `<app>/` package, not `src/<app>/`: users run it
from a checkout, and `python <app>.py` then imports it with no install step.
Use `src/` when the project is installed as a distribution (`pip install .`).

**Keep an established source layout.** The rule is about the root, not the
shape of the package. A repository that already keeps its modules under
`src/` (as a package, or flat with `src/` on `PYTHONPATH`) keeps that layout;
new modules go where the existing ones are. Migrating a repository from one
source layout to another is a separate, explicitly requested change, never a
side effect of adding a file.

## Making tests find the package

With modules in `<app>/` and tests in `tests/`, `import <app>` must work from
a test run started in the root. Pick the one that matches the test runner:

- **pytest** (7.0+, for `pythonpath`) — `pytest.ini` (or `[tool.pytest.ini_options]`):
  ```ini
  [pytest]
  testpaths = tests
  pythonpath = .
  ```
- **unittest** — run from the root: `python -m unittest discover -s tests -t .`
  (add an empty `tests/__init__.py`).

## Restructuring an existing repository

1. **Inventory.** `python3 scripts/layout_check.py` lists every file that must
   move. For each remaining root `.py`, decide: launcher users run (stays),
   module (→ package), tool (→ `scripts/`).
2. **Move with history.** `git mv test_x.py tests/`, `git mv x.py <app>/x.py`
   — never delete-and-recreate, which loses `git log --follow`.
3. **Fix imports.** `import x` → `from <app> import x`; inside the package use
   relative or absolute `<app>.` imports. Grep for the old module names,
   including string uses (`importlib`, `mock.patch("x.func")`, PyInstaller
   specs, `.bat`/`.sh` launchers, CI workflows, README commands).
4. **Fix paths.** Code that reads files with `Path(__file__).parent` now sits
   one level deeper; tests that build paths from the root likewise.
5. **Verify.** The full test suite, `python3 scripts/layout_check.py`, and
   each launcher started once (`python <app>.py --help`). A test suite that
   passes but was never collected from its new place proves nothing — check
   the collected count did not drop.
6. **Commit the move alone**, separate from any behavior change, so a reviewer
   can confirm it is a pure move (`git diff -M --stat`).

## Guardrails

- A restructure touches every import; it is not a T0 change. Route it through
  the normal flow (usually T1: scope 1, verification 1).
- Do not rename public entry points a user or a scheduled task already calls
  (`.bat` shortcuts, Task Scheduler, cron) without keeping a launcher at the
  old name or telling the user.
- Do not move files the template itself owns (`scripts/`, `.claude/`,
  `agent-routing/`).
