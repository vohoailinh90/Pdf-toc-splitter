---
name: quick-review
description: Self-review checklist to run after implementing a change and before calling it done — checks scope drift (including missed requirements), runs real verification, applies a mandatory money/order-safety invariant checklist, and classifies findings by confidence. Not a substitute for independent review on risk-bearing changes. No network calls, no external CLI beyond local git, no telemetry.
argument-hint: "[optional: what this change was supposed to do]"
---

Distilled and rewritten from the `/review` "Eng Review" gate pattern in
`garrytan/gstack` (MIT-licensed) — no shared code, no external binaries, no
telemetry, no GitHub/GitLab CLI dependency. Portable: copy this folder into
`.claude/skills/` (or `~/.claude/skills/` for a global install) in any repo.

## When to use this

Run this after implementing a change and before marking it done — including
in solo repos where nobody else will look at the diff. For a change to money
or order logic specifically, also do a separate pre-implementation safety
pass first: walk the money/order invariants checklist below against the
*plan*, before writing code. This skill's post-implementation pass does not
replace that pre-implementation pass.

This compensates for skipping a second pair of eyes on routine changes. See
Guardrails below: it is not a substitute for a genuinely independent
reviewer on anything risk-bearing.

## Procedure

1. **Find everything that changed — committed, staged, unstaged, and
   untracked. These are independent categories; check all of them every
   time, regardless of which ones turn out to be non-empty.**
   - Run `git status --short` first — a map of what's staged, unstaged, and
     untracked, not a substitute for reading any of it.
   - Always run `git diff --staged` (staged changes) and `git diff`
     (unstaged changes) if `git status --short` shows either. A base branch
     existing does not excuse skipping these — a commit-range diff and a
     working-tree diff are different comparisons and neither substitutes
     for the other.
   - Always read every untracked file in full — it appears in neither diff.
   - If `git status --short` marks a submodule's line with **any**
     non-space character in the submodule-state column — `m`/`M` for
     modified tracked content, `?` for untracked-only content (verified: a
     submodule with nothing but an untracked file shows as ` ? nested`, not
     `m`/`M`), or any combination — recurse into it: run `git -C
     <submodule-path> status --short`, then `git diff --staged` and `git
     diff` inside it, and read its untracked files too. The superproject's
     own diff exposes none of this directly (a dirty submodule shows at
     most `Subproject commit <sha>-dirty`, and untracked-only content
     shows nothing at all in the outer diff), so skipping this means a
     change made entirely inside a submodule gets a clean review without
     anything in it ever being read. Repeat for any submodule nested inside
     that one.
   - Separately, if any diff (committed, staged, or unstaged) shows a
     submodule *pointer* change — a gitlink entry rendered as `-Subproject
     commit <old>` / `+Subproject commit <new>` — that bump is itself a real
     code change even when the submodule's own worktree is perfectly clean:
     the actual change lives in the commits between `<old>` and `<new>`
     inside the submodule. Diff it directly with `git -C <submodule-path>
     diff <old> <new>` — the plain two-endpoint form, **never**
     `<old>...<new>`: triple-dot resolves to `git diff $(git merge-base
     <old> <new>) <new>`, which silently produces an empty diff whenever
     `<new>` is an ancestor of `<old>` (a rollback). Or re-run the outer
     diff with `--submodule=diff`, which expands every gitlink change into
     the nested commit diff using the correct comparison automatically.
   - Additionally, if there is committed history to review (this branch has
     commits beyond a single starting point): resolve a base with `git
     merge-base origin/<base> HEAD` when `origin/<base>` exists and is
     reachable, otherwise a local base branch (e.g. `main`) reachable from
     HEAD's history, then diff it with `git diff <base>...HEAD`. If no
     reliable base exists — no remote, no recognizable local base branch,
     or the branch's history is otherwise ambiguous — do not guess and do
     not silently treat that as "nothing to review": name the problem and
     ask which base or commit range to compare against (`git log --oneline`
     to show candidates).
   - No git repository at all (a quick script, no repo): re-read every file
     just touched.

2. **Scope check.** Compare what actually changed (from step 1, tracked and
   untracked) against `$ARGUMENTS` or the last stated goal in the
   conversation.
   - **Scope creep** — files/changes unrelated to the stated goal.
     Informational: report it, but don't block on it unless it introduces
     real risk (money, security, data loss) — in that case it's a normal
     finding under step 4 instead.
   - **Missing required behavior** — something the stated goal explicitly
     required that the diff doesn't do. This is *not* informational: record
     it as an ASK finding. It forces the verdict to `NEEDS INPUT`, the same
     as any other unresolved ASK item.
   - If neither `$ARGUMENTS` nor a usable prior stated goal exists, say so
     explicitly and skip the scope check rather than guessing at intent —
     proceed to step 3.

3. **Read everything in full before judging anything.** Don't flag
   something already handled elsewhere in the same change.

4. **Look for real problems.** Run the money/order invariants checklist
   below whenever the diff touches money, financial data, orders, or
   trades — mandatory in that case, not optional. Then check, in this
   order:

   - **Money/order invariants** (checklist below), if applicable.
   - **Correctness bugs reachable from a concrete input or state** — not
     pure speculation. But "unlikely" is never a reason to drop a finding
     whose impact includes money movement, security compromise, or
     irreversible data loss: rank those by likelihood *and* impact
     together, never filter them out by likelihood alone. A rare
     partial-fill-then-crash, a race between two concurrent submissions, or
     an ambiguous timeout after the counterparty already accepted the
     request are exactly the failures this rule exists to keep visible.
   - **Silent failure paths** — swallowed exceptions, empty
     `except:`/`catch`, a retry with no backoff or logging.
   - **Security** — hardcoded or logged secrets/API keys, shell/SQL
     injection, unvalidated external input.
   - **External API handling** — missing handling for rate limits, auth
     expiry, or timeouts on calls to brokerage/data/customer APIs.

   **Money/order invariants checklist (mandatory when the change touches
   money, orders, or trades):**
   - Currency and quantity units (dollars vs. cents, shares vs. lots) match
     on both sides of every calculation.
   - Decimal/tick/lot rounding matches the venue's rules — no silent float
     rounding on money.
   - Order side, order type, price, time-in-force, and session are exactly
     what was intended. An inverted side is a full loss, not a bug report.
   - Buying-power and position caps are checked *before* submission, not
     only logged after.
   - The quote/price used to size or price an order is fresh enough for the
     strategy's timeframe.
   - A retried or timed-out submission uses a stable client order ID — and
     the venue is confirmed to enforce that ID as a real idempotency key,
     not just correlation metadata, before relying on it to prevent
     duplicates. When that's unconfirmed, or the first submission's outcome
     is ambiguous (timeout, dropped response), reconcile by querying order
     status before retrying — never assume the ID alone protects you.
   - Partial fills, cancels, and replaces are reconciled against the
     original order, never treated as independent events.

5. **Score every finding's confidence 1-10 before reporting it:**
   - 8-10: you can point to the exact line and state the concrete failure —
     report normally.
   - 5-7: plausible but unverified — report with a "verify this" caveat.
   - Below 5: don't report it. Silence is a valid, common outcome — never
     invent a finding just to have something to say.

6. **Classify each real finding as AUTO-FIX or ASK — narrowly:**
   - **AUTO-FIX** — non-semantic only: a typo in a comment or in
     documentation (never in a runtime string, command, URL, protocol
     field, or any value the program reads, sends, or compares against),
     whitespace/formatting, or a forgotten `.gitignore` entry. Nothing that
     changes program behavior.
   - **ASK** — everything else, including a missing null check, an
     off-by-one, or any other change to logic or data handling. These
     change behavior; they are not mechanical fixes, and applying them
     unreviewed can introduce a regression nobody looked at. Money/order
     logic is always ASK, with no exception.
   - If an AUTO-FIX edit is applied, restart from step 1 and rerun the full
     review against the new state before reporting a verdict — never report
     on a diff that's already stale.

7. **Run verification before declaring CLEAN.** Discover and run the repo's
   own safe checks relevant to the changed files (lint, type-check, unit
   tests — whatever the repo's own tooling provides; don't invent a
   framework it doesn't have). For order-execution or money-movement code
   paths, require mocked/sandboxed tests — never a live brokerage/exchange
   call. Report the exact commands run and their results.
   - A check that runs and **fails** is itself an ASK finding — it always
     forces `NEEDS INPUT`, never `CLEAN`, regardless of what static
     inspection found.
   - A check that exists but **can't be run at all** here (missing
     credentials, no sandbox, offline) never claims `CLEAN` either — use
     `PASS_WITH_GAPS` and name exactly what wasn't verified and why.
     `PASS_WITH_GAPS` is only for a check that could not run; a check that
     ran and failed is never `PASS_WITH_GAPS`.
   - **Re-scan after verification, before issuing a verdict.** A formatter
     in write mode, a snapshot updater, or a codegen step invoked by a
     check can modify or create files after step 1's change-discovery
     pass already ran. Repeat step 1 (`git status --short`, the relevant
     diffs, any new untracked files) once verification is done. Anything
     new it turns up gets read and, if it needs one, its own finding —
     never report a verdict against a change set that's gone stale because
     verification itself edited the working tree.

8. **Report tersely.** One line per finding. No preamble, no restating the
   whole diff, no unrequested design commentary.

## Output format

```
Scope check: CLEAN | DRIFT DETECTED | NEEDS INPUT (missing required behavior — see findings)
<one line per issue, only if not CLEAN>

Verification: <exact commands run> -> pass/fail, or "not run: <reason>"

Findings:
[AUTO-FIXED] file:line — problem → what you did
[ASK][confidence N/10] file:line — problem → proposed fix
(none found)

Verdict: CLEAN (nothing found, every check that ran passed) | NEEDS INPUT (ASK items, missing behavior, or a failed check — see above) | PASS_WITH_GAPS (a check could not be run at all — see above)
```

## Guardrails

- **This is a self-review checklist, not a substitute for independent
  review.** It runs in the same session/context that made the change, so it
  cannot catch what that session is blind to. For anything touching
  money/order logic, security, or auth — or any change this repository's
  own routing policy would place above T0 — escalate to a genuinely
  separate `code-reviewer` subagent or session that receives the goal and
  the diff but not this session's conclusions. A clean `/quick-review`
  result is not equivalent to that independent pass.
- Never AUTO-FIX anything that changes program behavior, even a "trivial"
  one-line fix — always ASK. Money/order logic is always ASK, no exception.
- Don't flag style-only nitpicks unless they hide a real defect.
- Don't fabricate confidence scores or findings to look thorough — "no
  issues found" is a valid, common result.
- No network access, no telemetry, no hosting-platform CLI (`gh`/`glab`), no
  skill-specific binary — this file uses only Claude Code's own tools
  (Read/Grep/Glob/Bash) plus the local `git` executable when the repo has
  one. Without git, fall back to re-reading the touched files directly
  (step 1) — there is no other external dependency.
