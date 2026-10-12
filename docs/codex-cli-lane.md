# Codex CLI lane: the codex lane, run by the Codex CLI on your own machine

The codex lane (`docs/codex-lane.md`) reaches Codex Cloud through `@codex`
GitHub comments. The **codex-cli lane** runs the same tiers with the Codex CLI
(`codex exec`) on the machine the main session runs on. There is no GitHub
round trip and no Codex Cloud task, so it spends neither GitHub Actions minutes
nor Codex Cloud task quota. It still uses your Codex account's local usage.
Use it where you can install the CLI. On a machine where you can't, use the
codex lane or the claude lane.

The lane is `lanes.codex-cli` in `agent-routing/policy.yaml`. The skill
`/codex-cli-lane` is the runbook. `scripts/codex_cli.py` is the **only**
approved way to start Codex in this lane. Never run `codex exec` by hand, and
never through another wrapper, the SDK or the API.

## Three processes, chosen by the operator

| Process | Who writes the code | How Claude reaches it | Switch |
|---|---|---|---|
| claude lane (default) | the main session | Claude subagents review | `scripts/lane.py reset` |
| codex lane | Codex Cloud | `@codex` comments, `docs/claude-to-codex.md` | `scripts/lane.py set codex` |
| codex-cli lane | the Codex CLI, locally | `scripts/codex_cli.py` | `scripts/lane.py set codex-cli` |

The lanes don't share state.

Changing your mind is one command. `scripts/lane.py set <lane>` saves the
choice in `.claude/routing-lane.local`. That file is gitignored, belongs to
this machine only, and is left out of blind eval runs. `scripts/route.py`
uses the saved lane whenever `--lane` is not given:

```bash
python3 scripts/lane.py                 # which lane is in effect, and why
python3 scripts/lane.py set codex-cli   # from now on (this machine)
python3 scripts/route.py --dimensions 1,2,1,1,1,1          # prints "lane codex-cli (from ...)"
python3 scripts/route.py --dimensions 1,2,1,1,1,1 --lane claude   # one run in another lane
python3 scripts/lane.py reset           # back to the policy default
```

A lane never changes the tier, an override or a reserve, and `--check`
ignores it. T0 stays in the main session: a saved lane falls back to claude
for a T0 route, and an explicit `--lane codex-cli` on T0 exits 2. To move a
requirement that is already recorded, record it again in the new lane with
the same label. Charges carry over, exactly as in `docs/codex-lane.md`.

## Roster

The same as the codex lane, and for the same reasons.

| Tier | Codex CLI steps | Claude subagents | Claude base |
|---|---|---|---:|
| T1 | implement → review | none | 0 |
| T2 | design¹ → implement → review → security review² | none | 0 |
| T3 | design → design critique → implement → review → security review² | `code-reviewer-t3` (+ `requirement-analyst` on ambiguity 2) | 1 |

¹ exactly when the claude lane would fire `architect`. ² when `security-sensitive` applied.

## What `scripts/codex_cli.py` guarantees

| Step | Command it runs | Sandbox | Where |
|---|---|---|---|
| design / design critique (BRAINSTORM) | `codex exec --sandbox read-only` | read-only | detached worktree at `target_sha`, removed afterwards |
| implement (IMPLEMENT) | `codex exec --sandbox workspace-write` | write, network off | new worktree `.codex-worktrees/<branch>` on a new branch from `target_sha` |
| fix (FIX) | `codex exec --sandbox workspace-write` | write, network off | the branch's worktree, which must be clean and at `target_sha` |
| review | `codex exec --sandbox read-only` + a review prompt over `git diff <base>...HEAD` | read-only | detached worktree at the branch head |
| security review | `codex exec --sandbox read-only` + a security prompt over the same diff | read-only | detached worktree at the branch head |

Reviews deliberately avoid the built-in `codex exec review`. That command has
no `--sandbox` flag, so its sandbox would depend on how config precedence
treats your own `~/.codex` profile. A flag can't lose that contest.

- **Never** `danger-full-access`, never `--dangerously-bypass-approvals-and-sandbox`.
  `route.py` refuses a policy step whose sandbox does not match its mode.
  The network is always off for a write run. There is no switch to open it,
  because a write run that can reach the network can push or exfiltrate.
  A write run's only writable places are its worktree and its own scratch
  directory, `.codex-worktrees/<branch>.tmp`. `TMPDIR` points at the scratch
  directory, and the directory is removed after the run. `/tmp`, `$TMPDIR`
  and any configured `writable_roots` are closed by explicit overrides. No
  Codex config profile is passed, so none can widen this.
- **One state root, no symlinks.** All state (ledger, lock, reports) lives
  under the repository's top level. `CLAUDE_PROJECT_DIR` must name that top
  level exactly, so two subdirectories of one repository can't keep two
  ledgers and two locks. The script refuses to run if `artifacts/`,
  `artifacts/handoff/`, `.codex-worktrees/` or any directory under
  `artifacts/handoff/codex-cli/` is a symlink. It never writes a report or
  log over a path that already exists: logs are created with `O_EXCL`. A
  worktree, report, ledger or lock can therefore never land outside its root.
- **The prompt goes in last.** Codex receives its prompt on stdin only after
  the lock names the Codex process. If the script is killed in between,
  Codex reads an empty stdin and exits without doing anything. Codex CLI
  0.162.1 prints "No prompt provided via stdin." in that case.
- **Codex never commits or pushes.** It leaves its changes uncommitted in its
  worktree. If it commits anyway, the run says so, and you validate those
  commits too. You commit after validation, and you push and open the PR
  yourself. A run that switched branches or rewrote history is `violated`.
- **Contracts** follow the codex lane's format and pass
  `scripts/codex_task.py`'s rules: an exact SHA, the mode's `write_policy`,
  and no write to the base, `main` or `master`.
- **Every run is charged to the recorded requirement.** That means the label
  `route.py --lane codex-cli --record --requirement <id>` wrote to
  `artifacts/handoff/agent-budget.json`. The script never uses a `task_id` or
  a label you pass it. It refuses to run when no requirement is recorded, when
  the recorded one is in another lane, or when its label holds anything but
  letters, digits, `.`, `_` and `-`. That label names a report directory, so
  it must never contain a path.
- **The ledger** is `artifacts/handoff/codex-cli-ledger.json`, with reports in
  `artifacts/handoff/codex-cli/<requirement>/`. It enforces
  `lanes.codex-cli.round_caps` for each requirement and mode: BRAINSTORM 2,
  IMPLEMENT 1 and FIX 2, the same as §16 of the Cloud protocol; reviews have no
  cap (`review: null`, removed 2026-10-12) and are bounded by the same-head rule.
  A new `task_id` doesn't reset a count. It also refuses:
  - a round that already ran, or one that skips the round before it (a
    contract's `round` must be the next one for its requirement and mode);
  - a BRAINSTORM round 2 at a different SHA;
  - a second write at a SHA another write already used;
  - the same review twice at one head, whatever requirement it is charged to;
  - a third attempt at the same task round, or at the same review of one head,
    after two failures, or any retry of a violated run (BLOCKED). Review
    attempts belong to the head, so re-recording under a new label doesn't
    reset them. A failed review isn't a done review, and a
    new head starts its attempts afresh.

  A write run that changes nothing counts as a failed attempt, so it doesn't
  spend IMPLEMENT's single round. A run interrupted by Ctrl-C or an error is
  recorded as failed, never left as "running".
- **One run at a time.** A lock in `artifacts/handoff/` makes runs sequential.
  The lock is linked into place already holding its pid, so it is never seen
  empty. Once Codex starts, the lock names the Codex child too. It is taken
  over only when every process it names is gone, so killing the script while
  Codex survives still keeps the next run out. A lock that names no process
  is refused, never taken over.
  On Windows a stale lock has to be deleted by hand. WSL is the tested path.

## The process

1. **Set up once:** install the CLI (`npm i -g @openai/codex`), run
   `codex login`, then `python3 scripts/lane.py set codex-cli`, then
   `python3 scripts/codex_cli.py doctor`.
2. **Route:** classify as usual, then
   `route.py ... --record --requirement <id>`. The saved lane applies. Follow
   the printed `codex.steps`, and spawn `requirement-analyst` first when it
   is listed.
3. **Design** (when listed): write a BRAINSTORM contract (`target_branch: null`,
   `write_policy: none`, `target_sha` from `git rev-parse`), then
   `codex_cli.py run design.yaml`. Validate the report, then save it to
   `artifacts/architecture/<id>.md`. **Design critique** (T3) uses the same
   task_id with `round: 2` at the same SHA, led by the critique sentence from
   `docs/codex-lane.md` step 4. Record the verdict in `artifacts/critiques/<id>.md`.
4. **Implement:** write an IMPLEMENT contract (a new `target_branch`,
   `write_policy: feature_branch_only`, the approved design in `notes`), then
   `codex_cli.py run impl.yaml`. Validate the worktree's diff against every
   acceptance criterion, run the tests there, then commit there.
5. **Review:** `codex_cli.py review --branch <branch>`.
   Add `--security` when the step is listed.
6. **Fix:** confirm each finding against the code. Then write a FIX contract
   (`write_policy: current_pr_branch`, `target_sha` set to the branch head
   you committed), validate the result, commit, and review the new head
   again.
7. **Verify and finish:** run the tests, lint, `scripts/mutation_check.py --all`
   and `scripts/layout_check.py` yourself. At T3,
   spawn `code-reviewer-t3`. Then push the branch, open the PR, and finish
   under §20 of `docs/claude-to-codex.md` (CI, merge rules).
   Remove the worktree with `git worktree remove .codex-worktrees/<branch>`.
8. **Loop until clean, merge, continue.** Every PR goes through CLAUDE.md's
   *Pull request lifecycle*. Here the review is `codex_cli.py review` on the
   head the PR carries; the ledger refuses a second review of one head, so a
   clean review run before the push is the PR's review only while the pushed head is exactly the reviewed SHA:
   compare them, and a rebase, amend or extra commit makes a new head to review.
   A fix makes a new head, which gets its own review. When the head is
   clean, merge under §20 without asking, then take the next task.

## When the CLI is blocked

The CLI can be blocked in several ways: it isn't installed, you aren't logged
in, you've hit a usage limit, or a round is BLOCKED. Don't work around it.
Re-record the requirement in another lane under the same label, for example
`route.py ... --lane claude --record --requirement <id>`. Anything the main
session already validated is input, not lost work. Switching lane moves who does the work and never waives the PR's Codex review: an open PR stays BLOCKED until Codex has reviewed its head.
