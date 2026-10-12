---
name: codex-cli-lane
description: Run a classified T1-T3 requirement in the codex-cli lane — the Codex CLI on this machine designs, implements and reviews it through scripts/codex_cli.py, and the main session orchestrates and validates. Use when the operator has picked the codex-cli lane (`scripts/lane.py` says so, or `route.py` printed `lane codex-cli`), or asks for Codex to work locally ("chạy codex bằng CLI", "codex CLI", "Codex CLI で実装"). Never use it to classify, never for T0, and never on a machine without the Codex CLI.
argument-hint: "[requirement id or text]"
---

Requirement: $ARGUMENTS

The lane is `lanes.codex-cli` in `agent-routing/policy.yaml`. `docs/codex-cli-lane.md`
is the protocol, and this skill is the runbook. The lane changes who executes the
tier, never the tier.

## 0. Rules

- `python3 scripts/codex_cli.py` is the only way to start Codex in this lane.
  Never run `codex exec` yourself, never with another sandbox, and never through
  the SDK, the API or another wrapper.
- This lane posts nothing that mentions Codex on GitHub. The cloud protocol
  (`docs/claude-to-codex.md`) governs only the codex lane. Its §20 still governs
  done and merge.
- Exit 1 from `codex_cli.py` is a refusal or a failure. Act on the reason it
  gives. Never edit the ledger to get past it, except to mark a `running` entry
  whose process is gone as `failed`, and say so.

## 1. Route

```bash
python3 scripts/codex_cli.py doctor        # once per machine/session
python3 scripts/route.py --dimensions <a>,<s>,<ar>,<d>,<r>,<v> --signals <...> \
    --lane codex-cli --record --requirement <id>
```

Every run is charged to this recorded requirement. Its label must be letters,
digits, `.`, `_` or `-`. A contract's `round` is the next round for that
requirement and mode, whatever its `task_id`. Follow the printed `codex.steps` in order. `required_agents` is the whole
Claude roster. If it holds `requirement-analyst`, spawn it before writing any
contract.

## 2. Each step

Write the contract as YAML. Use the codex lane's fields: `task_id`, `mode`,
`repository`, `base_branch`, `target_branch`, `target_sha` (the full SHA from
`git rev-parse`), `goal`, `scope`, `out_of_scope`, `write_policy`,
`constraints`, `acceptance_criteria`, and optionally `round`, `notes`. Run
`codex_cli.py run <file> --dry-run` first, then without `--dry-run`.

| Step | Contract / command |
|---|---|
| design | BRAINSTORM round 1, `target_branch: null`, `write_policy: none` |
| design-critique (T3) | same task_id, `round: 2`, same SHA; lead: "independently challenge the round-1 proposal, you did not write it, critical/high only, then ACCEPT / ACCEPT_WITH_CHANGES / REJECT" |
| implement | IMPLEMENT, new `target_branch`, `feature_branch_only`, approved design in `notes` |
| review / security-review | `codex_cli.py review --branch <branch> [--security]` |
| fix | FIX, same branch, `current_pr_branch`, `target_sha` = the committed branch head, the confirmed finding in `notes`; at most 2 rounds |

After every run:

1. Read the report the script prints.
2. Validate it: the complete diff in the worktree, each acceptance criterion
   separately, and the tests re-run by you.
3. Commit in `.codex-worktrees/<branch>` only after validation.
4. Save the design to `artifacts/architecture/<id>.md` and the critique verdict
   to `artifacts/critiques/<id>.md`, within their line caps.
5. Ask Codex to fix only findings you have confirmed against the code.

## 3. Verify and finish

- Run the tests, lint, `scripts/mutation_check.py --all`
  and `scripts/layout_check.py` yourself on the branch.
- T3: spawn `code-reviewer-t3` on the diff.
- If verification needs designing:
  `python3 scripts/hooks/agent_budget.py grant escalation_reserve --agent test-engineer`.
- Push the branch, open the PR (its description never mentions Codex), and
  finish under §20.
- Every PR runs CLAUDE.md's *Pull request lifecycle*: review the head the PR
  carries with `codex_cli.py review` (the ledger refuses a second review of one
  head, so the clean pre-push review is the PR's review
  only if the pushed head is exactly the reviewed SHA — compare them), review
  each new head (a fix, a rebase, an amend), merge under §20 without asking once clean, then take the next
  outstanding task.
- Remove the worktree.

## 4. When the CLI is blocked

The CLI is blocked when it is not installed, you are not logged in, you have
hit a usage limit, or a round is BLOCKED. Tell the user, then re-record the
same requirement in another lane:
`route.py ... --lane claude --record --requirement <id>`. This moves who does the work and never waives the PR's Codex review (CLAUDE.md, *Pull request lifecycle*): an open PR stays BLOCKED until its head is reviewed.
