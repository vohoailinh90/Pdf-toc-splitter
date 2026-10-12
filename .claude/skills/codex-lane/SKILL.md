---
name: codex-lane
description: Run a classified T1-T3 requirement in the codex lane — Codex Cloud designs and implements it, a separate `@codex review` run critiques it, and the main session orchestrates through GitHub comments and validates every result instead of spawning Claude critics. Use when the user asks for Codex to architect, implement or review the work ("codex architect", "để codex viết code", "gọi codex review", "Codex に実装させる"), or picks the codex lane. Never use it to classify, and never for a T0 requirement.
argument-hint: "[requirement id or text]"
---

Requirement: $ARGUMENTS

The lane is defined in `agent-routing/policy.yaml` (`lanes.codex`) and explained
in `docs/codex-lane.md`. This skill is the runbook. It changes who executes the
tier, never the tier: classification, overrides and reserves are exactly what
`/classify-requirement` and `scripts/route.py` decide.

## 0. Before anything mentions Codex

Read `docs/claude-to-codex.md` **in full** (CLAUDE.md requires it). Every rule
there applies unchanged: one active request per thread, round caps, the §14
validation, §17 trigger failures, §20 done and merge. Never put a literal Codex
mention in any GitHub comment except the one comment meant to trigger it, and
never in a PR description or commit message.

## 1. Route in the lane

```bash
python3 scripts/route.py --dimensions <a>,<s>,<ar>,<d>,<r>,<v> --signals <...> \
    --lane codex --record --requirement <id>
```

Exit 2 on T0 means do it in the main session. Follow the printed
`codex.steps` in order; `required_agents` is the whole Claude roster. When it
holds `requirement-analyst` (T3, ambiguity 2), spawn it now, before any
contract is written: an open product decision must not reach Codex as an
assumption.

## 2. Each Codex step

For BRAINSTORM, IMPLEMENT and FIX, write the contract to a YAML file (the
fields in `docs/codex-lane.md`; `target_sha` from `git rev-parse`, full
length), fetch the thread's issue comments, and render:

```bash
python3 scripts/codex_task.py render contract.yaml --comments c1.json --comments-after c2.json > body.md
# on a PR also --reviews/--review-comments, each with its -after second fetch
```

Post `body.md` verbatim as one comment (GitHub MCP `add_issue_comment`) only
when it exits 0. Exit 1 is a refusal: do not post, act on the reason it gives.

| Step | Where | Contract |
|---|---|---|
| design | Issue | BRAINSTORM round 1, `write_policy: none`, base head SHA |
| design-critique (T3) | same Issue | same task_id, round 2, same SHA; lead says "independently challenge the round-1 proposal, you did not write it, critical/high only, then ACCEPT / ACCEPT_WITH_CHANGES / REJECT" |
| implement | Issue | IMPLEMENT, new feature branch, `feature_branch_only`, approved design in `notes` |
| review / security-review | PR | no contract, and never automatic — Codex's review on PR open is turned off, so request the first one as soon as the PR is open: `python3 scripts/codex_round_count.py ...` must exit 0, then the exact review command focused on the acceptance criteria |
| fix | PR | FIX, PR branch, `current_pr_branch`, the confirmed finding in `notes`; at most 2 rounds |

After every response: validate per §14 before anything else (complete diff,
each acceptance criterion separately, test evidence re-run, `STALE` check).
Save the validated design to `artifacts/architecture/<id>.md` and the critique
verdict to `artifacts/critiques/<id>.md`, under their line caps. Ask Codex to
fix only findings you confirmed against the code.

## 3. Verify and finish

- Run the tests, lint, `scripts/mutation_check.py --all`
  and `scripts/layout_check.py` yourself on the PR head — you wrote none of it,
  so this is independent verification, not self-review.
- T3: spawn `code-reviewer-t3` on the PR diff.
- Verification that needs designing: `python3 scripts/hooks/agent_budget.py
  grant escalation_reserve --agent test-engineer`, then spawn it.
- Done and merge: §20, unchanged. Every PR runs CLAUDE.md's *Pull request
  lifecycle*: the review row above is its step 1, the fix row its step 3. Repeat
  until a review of the current head is clean (reviews have no cap, §16),
  merge under §20 without asking, then take the next outstanding task.

## 4. When Codex is blocked

Trigger failure past its one retry, a stalled or failed task, the wrong account:
set the task BLOCKED, tell the user, and re-record the same requirement in the
claude lane (`--lane claude --record --requirement <id>`). Charges carry over;
validated Codex output is input to the claude lane, not discarded. This moves who does the work and never waives the PR's Codex review (CLAUDE.md, *Pull request lifecycle*): an open PR stays BLOCKED until its head is reviewed.
