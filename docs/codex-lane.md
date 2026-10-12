# Codex lane: Codex architects and implements, a second Codex run critiques

The claude lane — `profiles` in `agent-routing/policy.yaml` — has Claude
subagents supply the independent judgement the main session cannot supply on
its own work: `architect`, `architecture-critic`, `code-reviewer`. The codex
lane gives that work to Codex Cloud instead. Codex designs and writes the code;
a separate `@codex review` run, a fresh context that wrote none of it, critiques
the diff; the main session orchestrates through GitHub comments and validates
every result. Posting a review command is cheaper than building a Claude
reviewer context that must re-read the requirement, the design and the diff.

Everything goes through `docs/claude-to-codex.md` and nothing else. The lane
adds no Codex invocation path, and changes none of that protocol's rules.

## What the lane changes, and what it cannot

A lane is **who** executes a tier, never how hard the work is. Classification,
the score tier, every override and every reserve are resolved first and are
identical in both lanes, so a lane cannot lower a tier or skip an override
(`test_a_lane_never_changes_tier_overrides_or_reserves`). The classifier never
picks a lane: whether Codex Cloud is connected, the account linked and quota
left is a fact about the operator's setup, not about the requirement. So the
lane is a flag:

```bash
python3 scripts/route.py --dimensions 1,1,2,1,1,1 --lane codex
python3 scripts/route.py --dimensions ... --signals ... --lane codex --record --requirement REQ-031
```

| Tier | Codex steps (in order) | Claude subagents | Claude base |
|---|---|---|---:|
| T0 | — the lane does not run T0; the main session does the work | — | 0 |
| T1 | implement → review | none | 0 |
| T2 | design¹ → implement → review → security review² | none | 0 |
| T3 | design → design critique → implement → review → security review² | `code-reviewer-t3` (+ `requirement-analyst` on ambiguity 2) | 1 |

¹ exactly when the claude lane would fire `architect` (architecture 2, or a
contract/ownership signal). ² when the `security-sensitive` override applied.

How a step is tied to the claude lane: a step that `replaces` a Claude agent
runs exactly when the claude lane would require that agent for the same scores
and signals, and that agent leaves the roster. The T2 architect's two arms are
therefore not restated in the lane, and cannot drift from it. `route.py`
prints each step with the reason it runs, the agents it replaced, and the Codex
rounds the plan spends against section 16's caps; a plan past a cap is an
error, not a warning.

## Why it stays independent, and what it gives up

- **The review run did not write the code.** Each `@codex` comment starts a new
  Codex Cloud task with its own context, so the review is not the author
  re-reading its own diff. It is the same model family as the author, though,
  so on its own it is a fresh reading, not a cross-model one.
- **The cross-model check is the main session's §14 validation.** The main
  session never writes the code in this lane, so its validation — complete
  diff, every acceptance criterion separately, test evidence re-run — is
  independent of the author. That is what the claude lane needed a subagent
  for. It is not independent of the *contract*: at T1, and at T2 without a
  design step, the main session wrote the goal, scope and acceptance criteria
  Codex implemented, and validates against what it wrote.
- **T3's design critique is same-family, deliberately.** In the claude lane the
  design is challenged by `architecture-critic` before implementation; here a
  second Codex run challenges a Codex proposal, and the only non-Codex reading
  of the design before implementation is the main session validating it. That
  is a downgrade, accepted because it is the point of the lane: Codex
  critiques instead of Claude.
- **T3 keeps one Claude verifier.** A Codex review reads a diff. T3 verification
  is integration/e2e, migration, security and rollback evidence that has to be
  designed and run, and two runs of one model family share blind spots exactly
  where an escaped defect is irreversible. `code-reviewer-t3` stays, and is
  the cross-model reading of the code that the design critique is not.
  Removing it is one line in `policy.yaml` (`claude_agents` and
  `max_agent_invocations.T3`), and the tests say what else must then agree.

## The process

Read `docs/claude-to-codex.md` in full first (CLAUDE.md requires it before any
comment mentions Codex). The skill `/codex-lane` walks the same steps.

1. **Route.** Classify as usual, then
   `route.py ... --lane codex --record --requirement <id>`. The ledger keeps the
   lane, so the `PreToolUse` hook refuses any Claude spawn past the lane's
   ceiling — at T1/T2 that is every spawn but a granted reserve.
2. **Analyst (T3, ambiguity 2 only).** Spawn `requirement-analyst` before any
   contract is written: an open product decision must not reach Codex as an
   assumption.
3. **Design (when the step is listed).** On the requirement's Issue, a
   BRAINSTORM contract, round 1, `write_policy: none`, `target_sha` the base
   head. Validate the response per §14, then save the validated proposal to
   `artifacts/architecture/<id>.md` (200-line cap).
4. **Design critique (T3).** The same task_id, round 2, same SHA — §16's
   read-only correction round, allowed only after round 1 was validated. Lead
   with: *"Independently challenge the round-1 proposal above. You did not
   write it. Report critical and high findings only, each with evidence and a
   failure scenario, then ACCEPT, ACCEPT_WITH_CHANGES or REJECT."* Record the
   verdict in `artifacts/critiques/<id>.md`. Critical/high findings are resolved
   in the implement contract or escalated; there is no round 3.
5. **Implement.** An IMPLEMENT contract on the Issue: `target_sha` the base
   head, `target_branch` a new feature branch, `write_policy:
   feature_branch_only`, the approved design in `notes`. Validate per §14
   (`STALE` compares the branch head with the SHA Codex reported). If Codex
   pushed the branch without opening a PR, open it yourself; its description
   never mentions Codex (§16).
6. **Review.** Codex's automatic review on PR open is turned off (§11), so
   every review is requested, the first one as soon as the PR is open. On the
   PR: `python3 scripts/codex_round_count.py ...` must exit
   0, then the focused review command naming the acceptance criteria, and the
   security review command when the step is listed. Never quote either command
   in any other GitHub comment (§3).
7. **Fix.** Confirm every finding against the code first (§13 forbids a
   speculative one), then a FIX contract on the PR branch — at most 2 rounds —
   and a re-review of the new head (reviews have no cap, §16).
8. **Verify.** The main session runs the tests, lint and the repository's
   checks itself on the PR head. At T3 spawn `code-reviewer-t3` on the PR diff
   for the integration/migration/security/rollback verification. If
   verification needs designing, grant `escalation_reserve` for
   `test-engineer` exactly as in the claude lane.
9. **Done.** §20's definition of done and merge rules, unchanged — including
   "The loop every PR goes through": steps 6 and 7 repeat until a review of the
   current head is clean, then you merge without asking, then take the next task
   (CLAUDE.md, *Pull request lifecycle*).

Every task comment in steps 3, 4, 5 and 7 is rendered by
`scripts/codex_task.py`, never typed:

```bash
# every list once, then each once more (a post landing in between stops the render)
gh api --paginate --slurp repos/O/R/issues/N/comments > c1.json
gh api --paginate --slurp repos/O/R/issues/N/comments > c2.json
python3 scripts/codex_task.py render contract.yaml --comments c1.json --comments-after c2.json > body.md
# on a PR (a FIX), its reviews and review-thread comments too, in this order:
#   issues/N/comments > c1, pulls/N/reviews > r1, pulls/N/comments > rc1, then c2, r2, rc2
python3 scripts/codex_task.py render fix.yaml --comments c1.json --reviews r1.json --review-comments rc1.json \
    --comments-after c2.json --reviews-after r2.json --review-comments-after rc2.json > body.md
```

It exits 0 with the body only when the contract conforms (§5: every field, an
exact 40/64-hex SHA; the mode's `write_policy`; no write to the base, `main` or
`master`; §19's lines added; no second mention and no forged marker) and the
thread allows it (§6: no duplicate marker; §16: the next round, within its
cap, never one mode twice for one SHA except the next round of the same
read-only task, which also stays on the SHA its first round read; and every
request in the thread answered — a task by its final report (a Summary and a
Testing section), a merge command by the merge commit it reports, a review
request by a review result; an "On it", a task link or a status line is still
running, and a usage-limit or error notice on the newest task is §17's
BLOCKED, not a retry). A thread holding a review request is refused
without the PR's reviews and review-thread comments, where its answer lives.
Exit 1 means do not post; exit 2 means the contract or a list cannot be used.

## Falling back

When Codex is BLOCKED — a trigger failure §17 does not let you retry, a stalled
or failed task, a wrong account (§18) — the requirement does not wait. Re-record
it in the claude lane with the same label:
`route.py ... --lane claude --record --requirement <id>`. Charges already made
carry over, and the claude lane's roster applies from there; anything Codex
already produced and the main session validated is input, not lost work. Falling back moves who does the work and never waives the PR's Codex review: an open PR stays BLOCKED until Codex has reviewed its head.
Carry-over works in both directions, so moving a claude-lane requirement into
the codex lane can leave it overdrawn (say, a T2 reviewer already spawned
against a lane ceiling of 0). The hook then refuses every further spawn, which
is the intended outcome: the Claude work already done is not done again.

## What it does not change

- The classifier, its schema and the evals: no lane strategy enters the
  strategy enum, and `route.py --check` refuses `--lane`.
- `docs/claude-to-codex.md`: every limit, label and validation step applies as
  written. The lane's `round_caps` are a copy, tied to section 16 by a test.
- The claude lane: `route.py` without `--lane` prints what it always did, plus
  `lane: claude`.
