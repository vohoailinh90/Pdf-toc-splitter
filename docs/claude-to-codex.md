# Claude → Codex Cloud via GitHub Comments

## 1. Purpose

This document defines the only approved method for Claude to delegate work to Codex Cloud:
Claude must post a GitHub Issue or Pull Request comment containing the exact ASCII mention `@codex`.

Do not use:

- OpenAI API
- Codex GitHub Action
- Codex CLI
- Codex SDK
- Browser automation against the Codex UI
- Local shell commands to start Codex

GitHub comments are the shared communication channel between Claude and Codex.

This document governs the **codex lane** (Codex Cloud). The operator may pick
the separate **codex-cli lane** instead (`scripts/lane.py set codex-cli`),
which runs the Codex CLI on the operator's own machine. That lane has its own
protocol, `docs/codex-cli-lane.md`, and exactly one approved invocation path,
`scripts/codex_cli.py`. None of the bans above is lifted for the codex lane,
and a codex-lane task never falls back to the CLI on its own: switching lanes
is the operator's choice, made by re-recording the requirement.

## 2. Claude's responsibility

Claude is the orchestrator.

Claude must:

1. Inspect the repository and current task.
2. Determine the correct Codex task mode.
3. Create a precise task contract.
4. Choose the correct GitHub Issue or Pull Request.
5. Check that the same task has not already been posted.
6. Post one GitHub comment beginning with `@codex`.
7. Record the task ID, mode and exact commit SHA — except for a built-in command (review, security review, merge), which has no task ID: record `task_id: null` per sections 5/6/14 instead.
8. Stop and wait for Codex Cloud to respond.
9. Inspect Codex's response or implementation.
10. Request corrections only when supported by concrete evidence.

Claude must actually post the GitHub comment when GitHub write capability is available. Claude must not merely draft a comment and claim that Codex was called.

## 3. Correct `@codex` syntax

Claude must use the normal ASCII character:

```text
@codex
```

Do not use similar Unicode characters:

```text
＠codex
```

Do not place spaces inside the mention:

```text
@ codex
```

For code review, use the exact trigger:

```text
@codex review
```

For security review, use:

```text
@codex security review
```

For other tasks, start the comment with:

```text
@codex
```

Then provide the complete task instructions underneath.

**When posting any comment or reply on GitHub — as opposed to writing in this file, which GitHub does not scan — never include a literal `@codex ...` trigger string except in the one intentional comment meant to invoke it.** The GitHub connector matches the mention wherever it appears in a posted comment, not only at the start, so quoting the exact syntax to explain or refer to it (even inside backticks, even in a reply on a review thread) can re-trigger Codex with malformed or empty arguments. When a GitHub comment needs to refer to the syntax without invoking it, break it so it cannot match — e.g. write `@`+`codex review` or "the codex review command" — rather than the literal unbroken string. This file's own template blocks (sections 3, 7-13) are unaffected: they exist to be copied into the one intentional trigger comment, not posted as-is alongside other text.

## 4. Selecting the correct GitHub location

Use a GitHub Issue for

- Architecture brainstorming
- Repository-wide audit
- New feature implementation
- Bug implementation when no PR exists
- Investigation and root-cause analysis
- Creating a new implementation branch

Use a Pull Request for

- Reviewing the current PR diff
- Auditing an implementation
- Fixing review findings
- Fixing CI failures
- Requesting changes on the current PR branch
- Final code review

Claude must not open a new Issue when an existing Issue already represents the same task.
Claude must not post an implementation request into an unrelated Pull Request.

## 5. Required task contract

Every Codex request, except the exact built-in commands (review, security review, merge — see sections 11, 12, and 20), must contain this contract:

```yaml
task_id: "<unique-task-id>"
mode: "BRAINSTORM | INVESTIGATE | AUDIT | IMPLEMENT | FIX"
repository: "<owner/repository>"
base_branch: "<base-branch>"
target_branch: "<branch-or-null>"
target_sha: "<exact-commit-sha>"

goal: >
  A single clear description of the expected result.

scope:
  - Approved component, directory or behavior

out_of_scope:
  - Unrelated changes
  - Unapproved refactoring
  - Production deployment

write_policy: "none | current_pr_branch | feature_branch_only"

constraints:
  - Preserve existing behavior outside the approved scope.
  - Do not expose secrets.
  - Do not use destructive Git commands.

acceptance_criteria:
  - An objectively verifiable condition
  - Another objectively verifiable condition

required_output:
  - Summary
  - Evidence
  - Changed files, if applicable
  - Tests performed
  - Remaining risks
  - Recommended next action
```

Claude must resolve and include the actual commit SHA. Do not use placeholders such as `latest`, `current` or `HEAD` in a posted task.

## 6. Comment tracking

Every Claude comment that triggers Codex with a task contract (sections 7-10, 13) must end with this tracking marker:

```html
<!--
claude-codex-task
task_id: SI-0042
mode: BRAINSTORM
target_sha: a17d90c123456789
round: 1
-->
```

The built-in commands (`@codex review`, `@codex review for ...`, `@codex security review` — section 11-12; `@codex merge this pull request` — section 20) are exempt from this marker: the review forms must be posted as the exact command shown, with nothing appended, and the merge form as the exact prefix followed only by the branch/status lines section 20 specifies — a marker cannot be attached without breaking the trigger. Their duplicate check and identity tracking instead come from the PR head SHA and from Codex's own response, which reports the exact commit it acted on (`Reviewed commit: <sha>` for a review, the merge commit SHA for a merge) — see section 11's SHA-change rule.

Before posting, Claude must search the existing Issue or PR comments for the same combination:

```text
task_id + mode + target_sha + round
```

If that combination already exists, Claude must not post the request again.

**The task comment is a script.** Per `deterministic_work` in `agent-routing/policy.yaml`, render every BRAINSTORM, IMPLEMENT and FIX comment with `python3 scripts/codex_task.py render CONTRACT --comments FILE...`, giving it every page of the thread's issue comments (on a PR, its reviews and review-thread comments too), each list fetched twice as for the round count, and post its output only when it exits 0. It applies this section's duplicate check, section 5's fields and exact SHA, section 16's task round caps (BRAINSTORM, IMPLEMENT, FIX; reviews have none), same-SHA rule and one active request per thread, and section 19's required lines. The codex lane (`docs/codex-lane.md`) is a sequence of these requests and of the review commands in sections 11-12; it changes no rule in this file.

## 7. Brainstorm request

Use a GitHub Issue unless the discussion belongs to an existing PR.

```text
@codex

MODE: BRAINSTORM

Independently review the proposed solution and repository context.

Task contract:

task_id: SI-0042
mode: BRAINSTORM
repository: vohoailinh90/Shipping_Inspection
base_branch: main
target_branch: null
target_sha: a17d90c123456789

goal: >
  Design a reliable pipeline for detecting DMC codes, cropping them
  from source images and preserving the relationship between every
  crop and its original image.

scope:
  - DMC detection
  - DMC crop generation
  - Original-image tracking
  - Decode result tracking
  - Failure reporting

out_of_scope:
  - Unrelated UI redesign
  - Production deployment
  - Changes to unrelated Excel behavior

write_policy: none

constraints:
  - Do not modify files.
  - Do not create commits.
  - Do not push branches.
  - Inspect the current repository before making recommendations.
  - Prefer simple and testable designs.

acceptance_criteria:
  - Every generated crop can be traced to its original image.
  - Multiple DMC candidates in one image can be tracked independently.
  - Detection and decode failures are recorded explicitly.
  - Reprocessing does not silently duplicate results.

required_output:
  - Summary
  - Evidence
  - Changed files, if applicable
  - Tests performed
  - Remaining risks
  - Recommended next action

Required output (elaborated for this mode):
1. Current-system observations
2. Proposed architecture
3. Data-tracking design
4. Files likely to change
5. Failure cases
6. Testing strategy
7. Risks and open questions

Reference the exact target SHA in your response.

<!--
claude-codex-task
task_id: SI-0042
mode: BRAINSTORM
target_sha: a17d90c123456789
round: 1
-->
```

## 8. Investigation request

Use this when Claude needs Codex to determine a root cause without modifying code.

```text
@codex

MODE: INVESTIGATE

Investigate the reported problem at the exact commit specified below.

Do not modify files, create commits or push changes.

Task contract:

task_id: SI-0043
mode: INVESTIGATE
repository: vohoailinh90/Shipping_Inspection
base_branch: main
target_branch: null
target_sha: "<exact-sha>"

goal: >
  Determine why the generated Excel workbook is repaired by Excel
  when the user opens it.

scope:
  - Root-cause analysis of the Excel workbook generation path

out_of_scope:
  - Implementing the fix
  - Unrelated refactoring

write_policy: none

constraints:
  - Do not modify files.
  - Do not create commits.
  - Do not push branches.

acceptance_criteria:
  - A concrete, file-and-line-referenced root cause is identified.
  - The root cause is reproducible from the evidence given.

required_output:
  - Summary
  - Evidence
  - Changed files, if applicable
  - Tests performed
  - Remaining risks
  - Recommended next action

Required output (elaborated for this mode):
1. Confirmed root cause
2. Supporting evidence with file paths
3. Conditions that reproduce the problem
4. Recommended fix
5. Required regression tests

Do not implement the fix.

<!--
claude-codex-task
task_id: SI-0043
mode: INVESTIGATE
target_sha: <exact-sha>
round: 1
-->
```

## 9. Repository audit request

Use a GitHub Issue for a repository-wide audit.

```text
@codex

MODE: AUDIT

Audit the repository at the exact commit specified in this task.

Task contract:

task_id: SI-0044
mode: AUDIT
repository: vohoailinh90/Shipping_Inspection
base_branch: main
target_branch: null
target_sha: "<exact-sha>"

goal: >
  Produce a severity-ranked list of concrete, evidence-backed defects
  across the areas listed below.

scope:
  - Functional correctness
  - Data integrity
  - Image-to-DMC tracking
  - Excel workbook safety
  - Error handling
  - Logging and diagnostics
  - Security
  - Dependency risks
  - Test coverage
  - Regression risks

out_of_scope:
  - Implementing any fix
  - Speculative findings without repository evidence

write_policy: none

constraints:
  - Do not modify files.
  - Do not create commits.
  - Do not push branches.

acceptance_criteria:
  - Every finding includes severity, file path, evidence, and a recommended correction.
  - No finding is reported without repository evidence.

required_output:
  - Summary
  - Evidence
  - Changed files, if applicable
  - Tests performed
  - Remaining risks
  - Recommended next action

Do not modify files, create commits or push branches.

Audit the following areas:
1. Functional correctness
2. Data integrity
3. Image-to-DMC tracking
4. Excel workbook safety
5. Error handling
6. Logging and diagnostics
7. Security
8. Dependency risks
9. Test coverage
10. Regression risks

For every finding, include:
- Severity: P0, P1, P2 or P3
- File path
- Relevant function or code location
- Concrete evidence
- User impact
- Recommended correction

Do not report speculative findings without repository evidence.

<!--
claude-codex-task
task_id: SI-0044
mode: AUDIT
target_sha: <exact-sha>
round: 1
-->
```

## 10. Implementation request

Use a GitHub Issue for a new feature or bug fix when no implementation PR exists.

```text
@codex

MODE: IMPLEMENT

Implement the approved task contract below.

task_id: SI-0045
mode: IMPLEMENT
repository: vohoailinh90/Shipping_Inspection
base_branch: main
target_branch: feat/dmc-crop-tracking
target_sha: "<exact-base-sha>"

goal: >
  Detect DMC candidates, save traceable crop images and associate
  every decoded result with its original source image.

scope:
  - DMC candidate detection
  - Crop generation
  - Deterministic crop naming
  - Source-image metadata
  - Decode status
  - Tests

out_of_scope:
  - Unrelated UI redesign
  - Changes to existing prompt generation
  - Production deployment

write_policy: feature_branch_only

constraints:
  - Work only on the feature branch.
  - Do not force-push.
  - Preserve existing behavior outside the approved scope.
  - Do not silently change the Excel schema.
  - Do not add a dependency without explaining why it is required.
  - Do not perform unrelated refactoring.

acceptance_criteria:
  - Every crop contains an original_filename reference.
  - Crop filenames are unique and deterministic.
  - Multiple candidates from one source image are supported.
  - Detection failures and decode failures are distinguishable.
  - Reprocessing does not overwrite unrelated results.
  - Relevant automated tests pass.
  - Generated Excel files open without repair warnings.

required_output:
  - Summary
  - Evidence
  - Changed files
  - Tests performed
  - Remaining risks
  - Recommended next action

After implementation, report (elaborated for this mode):
1. Branch name
2. Commit SHA
3. Changed files
4. Implementation summary
5. Test commands
6. Test results
7. Remaining limitations
8. Pull Request link, if created

<!--
claude-codex-task
task_id: SI-0045
mode: IMPLEMENT
target_sha: <exact-base-sha>
round: 1
-->
```

## 11. Pull Request review

**Codex does not review a PR on its own here.** The repository owner has turned off Codex's automatic review when a pull request is opened or marked ready (2026-10-10). No opening run will come, so do not wait for one: as soon as the PR is open, run `scripts/codex_round_count.py` and, when it exits 0, request the first review yourself. Every review on these PRs is a requested one and counts as a round. Requesting that first review is mandatory for every PR Claude creates, at every tier; the loop it starts, through to the merge, is "The loop every PR goes through" in section 20.

For a standard Codex code review, Claude must post this exact command in the Pull Request:

```text
@codex review
```

For a focused review:

```text
@codex review for functional regressions, DMC-to-source-image
tracking errors, Excel corruption, unsafe file handling and violations
of the acceptance criteria in Issue #42.
```

Claude must request a new review only when `scripts/codex_round_count.py` exits 0 (section 16 sets no cap on review rounds), and, for the review command being requested (standard review here, security review in section 12):

- That command has not yet reviewed the current PR head SHA — a head that changed, or an unchanged head on which a required security review follows the standard review; or
- That command's previous review of this head failed to run.

Claude must not request the same review command again for a PR head SHA it already reviewed.

## 12. Security review

When a security-specific review is required, post this exact comment in the Pull Request:

```text
@codex security review
```

Do not replace this with a generic audit prompt when the purpose is specifically Codex Security Review.

## 13. Fixing a confirmed finding

Use the existing Pull Request containing the problem.

```text
@codex

MODE: FIX

Fix the confirmed P1 finding described in the review above.

Task contract:

task_id: SI-0045-FIX-01
mode: FIX
repository: vohoailinh90/Shipping_Inspection
base_branch: main
target_branch: "<current-pr-branch>"
target_sha: "<current-pr-head-sha>"

goal: >
  Fix the confirmed P1 finding described in the review above.

scope:
  - The file(s) and behavior named in the confirmed finding

out_of_scope:
  - Unrelated refactoring
  - Changes outside the confirmed finding

write_policy: current_pr_branch

constraints:
  - Modify only the current PR branch.
  - Avoid unrelated refactoring.
  - Add or update a regression test.
  - Run the relevant tests.

acceptance_criteria:
  - The confirmed defect no longer reproduces.
  - A regression test covers the confirmed defect.
  - Relevant tests pass.

required_output:
  - Summary
  - Evidence
  - Changed files
  - Tests performed
  - Remaining risks
  - Recommended next action

Confirmed problem:
- File: src/example.py
- Finding: Describe the confirmed defect.
- Required behavior: Describe the correct behavior.

Report the new commit SHA, plus every field listed in required_output above.

<!--
claude-codex-task
task_id: SI-0045-FIX-01
mode: FIX
target_sha: <current-pr-head-sha>
round: 1
-->
```

Claude must not ask Codex to fix a speculative finding. Confirm the finding first.

## 14. Validating Codex's response

Claude must not accept Codex's summary without inspecting the actual GitHub state.

After Codex responds, Claude must:

1. Confirm that the response belongs to the correct Issue or PR.
2. Confirm the task ID and task mode. For a built-in command response, there is no `task_id` (section 6/14 exempt it); confirm the mode (`REVIEW`, `SECURITY_REVIEW`, or `MERGE`) instead.
3. Confirm the SHA reviewed or modified by Codex.
4. Inspect the current PR head SHA. Two cases have no PR to inspect:
   - Read-only Issue request (BRAINSTORM, INVESTIGATE, AUDIT — `write_policy: none`): inspect the current head of `base_branch` instead and use that as `current_sha`.
   - IMPLEMENT that pushed a branch but did not open a PR (section 10 makes the PR link optional): inspect the current head of `target_branch` instead and use that as `current_sha`.
5. Inspect the complete diff.
6. Check every acceptance criterion separately.
7. Check for unrelated changes.
8. Verify reported test evidence.
9. Report unresolved risks.
10. Decide the next state.

Claude's verification output must use:

```yaml
task_id: "<task-id>"
codex_mode: "<mode>"
requested_sha: "<requested-sha>"
reported_sha: "<sha-codex-reported-producing, or null when not applicable>"
current_sha: "<current-sha>"
status: "ACCEPTED | CHANGES_REQUIRED | BLOCKED | STALE"
unresolved_findings: []
next_action: "<single-next-action>"
```

`requested_sha` is always the starting point given in the request (the SHA Codex was told to read from or build on) — it is not necessarily what the final result should match. `reported_sha` is the commit SHA Codex's own response says it produced (e.g. its "Commit SHA" line, or the merge commit SHA for a MERGE response); it applies to IMPLEMENT, FIX, and MERGE, and is `null` for everything else.

The `STALE` check compares against different things depending on the mode, because `requested_sha` means different things for read-only vs. mutating modes:

- REVIEW, SECURITY_REVIEW, and the read-only modes (BRAINSTORM, INVESTIGATE, AUDIT): `requested_sha` is the exact commit Codex looked at. Use `STALE` whenever `current_sha` (the `base_branch` head for an Issue-based read-only request, or the PR head otherwise) simply does not equal `requested_sha` — a reset, rebase, or force-push can move the head to a divergent commit that isn't a descendant, so a plain inequality catches that too, not only forward movement.
- IMPLEMENT and FIX: `requested_sha` is only the starting base commit — comparing it to `current_sha` is meaningless, since a successful run necessarily advances the branch past it. Instead use `STALE` whenever `current_sha` (the `target_branch` head — PR head if one exists, else the branch head per section 10) simply does not equal `reported_sha` — not only when it has moved further ahead. A reset, rebase, or force-push can replace `reported_sha` with a divergent commit that isn't its descendant, so an ancestry/"moved past" check alone would miss that the reported output no longer exists at the branch head; a plain inequality catches every case where the branch no longer points at what Codex reported producing. Confirming `requested_sha` was a valid starting point is a separate check (step 3), not part of the `STALE` decision.
- MERGE: does not use `STALE` at all — a completed merge is a terminal event, not something that becomes outdated the way a live branch head does. Instead: `current_sha` is the PR's actual `merge_commit_sha` on GitHub (present only once GitHub shows the PR as merged); `reported_sha` is the merge commit SHA Codex's response reports. Two things must both hold: `current_sha` equals `reported_sha`, and the PR's recorded head SHA immediately before merge equals the `validated_head_sha` from section 20 (i.e. the merge commit's non-base parent, or the PR's `head.sha`) — confirming the merge landed the exact validated content, not a head that moved after validation but before the merge executed. If either does not hold, this does not validate — use `CHANGES_REQUIRED` (Codex reported success but the merge commit or merged head doesn't match) or `BLOCKED` (the PR shows no merge at all), never `STALE`.

For a built-in command response (`@codex review`, `@codex review for ...`, `@codex security review`, or `@codex merge this pull request`), which section 6 exempts from carrying a `task_id`: set `task_id: null` and `codex_mode: "REVIEW"`, `"SECURITY_REVIEW"`, or `"MERGE"`. For REVIEW/SECURITY_REVIEW, establish identity from `requested_sha`/`current_sha` alone (matching Codex's own "Reviewed commit" line — never the `Commit` cell of the review-status summary comment, section 16). For MERGE, establish identity from `reported_sha`/`current_sha` as defined above.

## 15. State labels

Use these GitHub labels when available:

```text
ai:needs-codex-brainstorm
ai:waiting-for-codex
ai:codex-complete
ai:ready-for-implementation
ai:implementation-in-progress
ai:needs-codex-review
ai:changes-required
ai:approved
ai:blocked
```

Expected lifecycle:

```text
ai:needs-codex-brainstorm
    -> ai:waiting-for-codex
    -> ai:codex-complete
    -> ai:ready-for-implementation
    -> ai:implementation-in-progress
    -> ai:needs-codex-review
    -> ai:approved
```

After Claude posts an `@codex` comment:

1. Remove the label that requested the trigger.
2. Add `ai:waiting-for-codex`.
3. Do not post another Codex request while this label remains — except the single authorized built-in-command retry (sections 16-17, including MERGE) when the first attempt produced a trigger failure; posting it does not require removing or changing this label first.
4. When Codex completes, replace it with the appropriate next label.

## 16. Preventing duplicate calls and infinite loops

Claude must follow these rules:

- Maximum brainstorm rounds: `2`
- Maximum correction rounds: `2`
- Maximum review rounds per PR: none — the repository owner removed the limit of three on 2026-10-12. A review is bounded instead by the rules below: each review command (standard, security) runs once per head SHA, one request is active at a time, and the previous response is validated first
- Only one active Codex request per Issue or PR
- Never trigger the same task mode twice for the same SHA, with two documented exceptions:
  - a built-in command (review or merge) that ends in a trigger failure (section 17: no response, or an explicit error/rate-limit message instead of the expected output) on an unchanged PR head, as sections 11 and 20 authorize — at most once per SHA; if the retry itself also ends in a trigger failure, do not retry again: follow section 17 step 2 instead; or
  - a validated correction round, within the round limits above, on an unchanged SHA for a read-only request (BRAINSTORM, INVESTIGATE, AUDIT — `write_policy: none`), whether posted on an Issue or a PR (section 7 allows BRAINSTORM on either) — nothing moves the SHA between rounds when nothing else touches it, so a correction round necessarily re-triggers the same mode on the same SHA, and is only allowed after Claude has validated the prior response per section 14
- Never respond to a Codex comment by immediately posting another `@codex` request without first validating the result
- Never let a bot comment automatically create an unrestricted bot-to-bot loop
- Stop when business judgment or human authorization is required
- Escalate unresolved P0 and P1 disagreements to the repository owner

Claude must never use repeated `@codex` comments to test whether the integration is working.

### Counting rounds against these limits

A round is a run in which Codex actually did the work: for a review, a review result posted on the PR; for any other mode, the report its `required_output` asks for, even an incomplete one. Count rounds from those posts on GitHub, not from Claude's own notes. Every review counts, whatever its kind — standard, focused or security — and whether or not a request asked for it: Codex also runs on comments that never mention it, and that run is as real as any other. The one exception is the opening run: the PR's first review when nothing but the summary comment came before it — no request, no other post by anyone — a request being a comment or review by anyone other than the bot that asks Codex for a review (`@codex review`, `@codex review for ...` or `@codex security review`) — which is Codex's own review of a newly opened PR, or of a draft marked ready. Only that one review is left out; when a request races it, it counts. A mention that starts a task instead (a FIX, section 13) is not a request: Codex answers it with a task, never with a review, so it leaves no review pending — but it is a post, so a review after it is not the opening run. A request that nothing has answered yet — no review result, no usage-limit or error notice — counts as a round in progress: its run may land after the count was taken, or leave only a reaction. Never delete a request once posted, or edit the mention out of it, and never mention Codex in the PR description, which the count does not read: the count sees only the posts still there, and a review whose request it cannot see passes for the opening run.

A review result is a review the bot submits on the PR, or a bot comment reporting a result for a commit: a `Reviewed commit:` line, a finding, or a no-findings verdict such as "Didn't find any major issues". It counts even when its finding is wrong, repeats an earlier one, or there is no finding at all, and it counts when it lands — including late, for a request already reported as stalled under section 17.

None of these is a round, and none counts against any limit:

- a usage-limit or quota message;
- an error message in place of a result — a connection error, an account or setup notice, a failed or cancelled task (section 17 decides whether it may be retried). Both are short notices carrying no result marker; a post that opens with an error and goes on to report a review is a review;
- the review-status summary comment, below;
- a post that is nothing but an acknowledgement or a task link (`chatgpt.com/codex/tasks/…`, or the `chatgpt.com/s/…` link of a bare "View task") — a task reply with content of its own is not one;
- the opening run (above).

On a PR, the `chatgpt-codex-connector` bot keeps one review-status summary comment: an issue comment whose body begins with `<!-- codex-pull-request-review-summary -->`, headed `## Codex Review Summary`, holding a `| Review | Status | Commit | Review trigger |` table and the bot's standard "About Codex in GitHub" footer, and edited in place after every run. It is a dashboard, not a review: it never counts, it is never trigger evidence for a request (section 17), and it is never the response section 14 validates — its `Commit` cell is not a `Reviewed commit:` line. If its body ever carries anything besides that table and footer — a finding, a `Reviewed commit:` line, a verdict — that content is a review result and counts.

**Only these are excluded, and never by judgment.** A bot post carrying a result always counts. A post that is neither a result nor plainly one of the kinds above counts too: a new kind of bot post is a rule question for the repository owner, not grounds for one more request. Where a limit still applies (the task rounds above), stop at it — do not request another round on the strength of an exclusion this section does not list, even one announced in the request itself. (On `vohoailinh90/Shipping_Inspection#194`, the summary comment was counted as a review, so the cap was reached after two real reviews, and a further round was then posted by excluding it before any rule there allowed it; see `vohoailinh90/Shipping_Inspection#196` and `vohoailinh90/Shipping_Inspection#197`.)

**The review count is a script.** Per `deterministic_work` in `agent-routing/policy.yaml`, run `python3 scripts/codex_round_count.py` before every review request, and request only when it exits 0. Give it every page of three lists, fetched in this order and then once more in the same order: the PR's reviews (`--reviews`, `--reviews-after`), its issue comments (`--comments`, `--comments-after`) and its review-thread comments (`--review-comments`, `--review-comments-after`), as REST `gh api --paginate --slurp` or GitHub MCP lists. A page left out is a round the count cannot see, and a request in a thread it cannot see turns that review into the uncounted opening run. The second pass makes the first one snapshot: a post that appears in any list between its two fetches, or is edited between them, stops the count until all six are fetched again, so a request older than a result the count sees was seen too, and a run whose request and result both came while the lists were fetched is caught. Write every fetch to its own file: one file named twice (for two options or two pages, under any spelling of its path) stops the count, as a second fetch that rereads the first proves nothing. Every list must show it is complete: a `gh api --paginate --slurp` list and an MCP review-thread result with `pageInfo`, each of whose threads carries its `total_count`, prove it; a flat list (an MCP `get_reviews`/`get_comments` page) is accepted only with the empty page after it, which is Claude's word that no page was skipped. An inline comment of Codex's newer than every review in the reviews list also stops the count, and a request with no answer counts. A login that looks like Codex but is not the bot, or another bot whose post carries Codex's summary marker, its About footer or a review result marker, stops the count rather than being taken for a person. Its count is the count: it gates on completeness, not on a number (exit 0 means the lists were complete and counted; exit 1 is unused; exit 2 means the input could not be counted — fix the input, never read it as success). A post it reports as unrecognized is counted, and is a question for the repository owner.

### No review limit

Reviews are not capped, so a P0 or P1 fix is reviewed like any other: push it, then request the next review on the new head. What ends the loop is a clean review of the current head (section 20), or one of two exits:

- **A disagreement.** A finding Claude rejected with evidence on its thread. At P2 or P3 the recorded rejection stands at once and the finding blocks nothing, even if Codex raises it again. At P0 or P1 it survives only if a review keeps it: when other confirmed fixes make a new head, push it and let the next review decide — not raised again, it is closed; raised again, unchanged or reworded or moved when it is substantially the same issue, it is an unresolved disagreement. When no code change is pending there is no new head to review, so it is an unresolved disagreement at once. An unresolved disagreement goes to the repository owner with the finding, the evidence and the fixes so far, and the PR is not merged until the owner decides.
- **A genuine blocker** (section 20), including Codex being unreachable (section 17, section 18).

Never request a review command for a head that command already reviewed (a required security review may follow the standard review on the same head, as section 14 treats them as separate modes), never while a request is unanswered, and never before the previous response was validated: these three, not a count, are what keep the loop from spinning. In the Cloud lanes they are prose rules, not script gates: `scripts/codex_round_count.py` sees neither the PR head nor Codex's reaction (a clean review may leave only a thumbs-up, which no list carries), so it cannot tell an unanswered request from a clean review and gates on neither; only the codex-cli ledger enforces the same-head rule.

## 17. Confirming that Codex was triggered

Claude must not claim that Codex was successfully called merely because the comment was submitted.

Successful trigger evidence is at least one of:

- Codex reacts to the comment
- The `chatgpt-codex-connector` bot responds with a review or a linked task
- A linked Codex Cloud task appears
- Codex posts a review

The review-status summary comment (section 16) is none of these, and never the final output either: one comment serves the whole PR, created by its first review and edited in place after every run, so neither its existence nor a `Completed` in its row says anything about one particular request.

A **trigger failure** is either of:

- No evidence appears at all after waiting at least 10 minutes since the comment was posted, with no further evidence arriving in that window (via a live PR-activity subscription/notification where available, or a manual re-check otherwise) — Codex reviews normally respond within a few minutes, so this window is meant to rule out a delayed-but-still-running review, not to force a fixed delay when evidence already arrived sooner; or
- The bot responds, but with an explicit error or rate-limit message instead of a review or task link (e.g. a usage-limit notice) — the bot did respond, but the trigger still did not produce usable output.

This 10-minute wait applies only to the no-evidence-at-all case above. Do not post the retry before that wait has elapsed with no evidence — posting early risks a duplicate review landing once the first one completes, breaking the "one active request" rule in section 16. An explicit error/rate-limit response is itself immediate evidence of a trigger failure and needs no wait: proceed straight to the retry.

A third case is neither of these: Codex acknowledges the trigger (reacts, or a linked task appears) but the task does not produce the mode's expected final output — a review for any built-in review command (`@codex review`, `@codex review for ...`, `@codex security review`), a merged PR with a reported merge commit SHA for `@codex merge this pull request`, or the `required_output` report described in sections 7-10/13 for every other mode. A valid final response in that expected shape is success, whatever the mode; only its absence is a problem. This is an **unsuccessful completion**, not a trigger failure — the trigger worked, so the review retry below does not apply (retrying would risk a second concurrent Codex run against the same request). It covers two shapes:

- **Stalled**: no final response of the expected shape arrives within a reasonable time (a further 30+ minutes with no update after the acknowledgement).
- **Terminal failure**: the linked task itself later posts an explicit failure, error, or cancellation instead of its expected output.

Either shape: do not post another `@codex` comment; set the task status to `BLOCKED` with `next_action` describing the stall or the reported failure; and report it to the repository owner.

On a trigger failure:

1. If this is a built-in command (`@codex review`, `@codex review for ...`, `@codex security review`, or `@codex merge this pull request`) and no retry has been posted yet for this SHA, post the single authorized retry per sections 11/20/16 — the `ai:waiting-for-codex` label, if present, does not block this (section 15) — then wait for a response before doing anything else. Do not post a second retry.
2. Otherwise — this was not a built-in command, or the one authorized retry also ended in a trigger failure — do not post any further duplicate comments, and instead:
   1. Confirm that the exact ASCII mention `@codex` was used.
   2. Confirm that Codex Cloud is enabled for the repository.
   3. Confirm that the repository is accessible to the Codex GitHub integration.
   4. Confirm that the triggering GitHub identity is authorized.
   5. Set the task status to `BLOCKED`.
   6. Report the failure to the repository owner.

Do not use API, Codex Action, CLI or SDK as a fallback. (Re-recording the requirement in another lane,
the codex-cli lane included, is not a fallback: it is the operator's lane choice, per section 1.)

## 18. Codex account routing

Claude cannot select a Codex account through comment text.

The following instruction has no routing effect:

```text
@codex use account number 2
```

GitHub and the Codex integration determine which linked account and quota apply.

If the wrong Codex account is being used, Claude must:

1. Stop posting new Codex comments.
2. Mark the task `ai:blocked`.
3. Report that the GitHub-to-Codex account linkage must be corrected by the repository owner.

Claude must not attempt to solve account routing by posting additional mentions.

## 19. Safety rules

Claude must never ask Codex to:

- Push directly to `main`
- Force-push
- Delete branches or repository history
- Reveal secrets
- Modify production credentials
- Disable branch protection
- Bypass required reviews
- Execute unrelated refactoring
- Expand the approved scope silently

Merging a pull request is authorized once the completion criteria in section 20 are met — that section defines how and when, including how to ask Codex to perform the merge itself.

Implementation requests must always include:

```text
Work only on the approved branch.
Preserve unrelated behavior.
Report all changed files and tests.
```

## 20. Definition of done, and autonomous merge authority

A Claude-to-Codex task is complete only when:

- The `@codex` trigger produced verifiable evidence.
- Codex processed the intended Issue or PR.
- Codex used the intended task mode.
- Codex referenced or modified the correct SHA.
- Claude inspected the actual response or diff.
- Acceptance criteria have evidence.
- Relevant tests passed, and every repository-required check and review on the PR is green — not only Claude's own ad hoc test run.
- No unresolved P0 or P1 finding remains.
- No unauthorized scope expansion occurred.

When all of that holds, record the exact PR head SHA that was validated as `validated_head_sha`, and the base branch's head SHA at that same moment as `validated_base_sha`.

Once a task meets that definition, Claude is authorized to merge the pull request without waiting for a human — this applies uniformly, including to irreversible, destructive, security, and payment-shaped changes. But time passes between validating a head and executing the merge, and either side can move in that gap: a new commit or force-push on the PR branch, or new commits landing on the base branch. GitHub merges the PR head into whatever the base branch currently is at merge time, not the base branch as it was when the checks ran — so an advanced base can land an unvalidated combination even when the PR head itself is still exactly `validated_head_sha`, and every recorded check still refers to the old state either way.

The head side has an atomic guard: GitHub's merge API takes an expected-head-SHA parameter and fails closed if the PR head has moved (section 20's merge calls below use it). **The base side has no equivalent parameter** — nothing turns a "check the base, then merge" sequence into one atomic operation, so no amount of re-checking immediately before merging closes that gap; a base-branch commit landing between the check and the merge call is a real race no matter how short the window.

Because of that, autonomous merge on a repository requires branch protection's "require branches to be up to date before merging," or an equivalent merge queue, enabled on the base branch — confirm this before ever attempting an autonomous merge on that repository. With it enabled, GitHub itself atomically rejects the merge if the base has advanced past what the PR branch was validated against, the same way the expected-head-SHA parameter does for the head.

The rule being enabled is not enough by itself — it must also actually bind whichever identity will execute the merge. GitHub offers more than one way to exempt an actor from a branch rule, and this list is illustrative, not exhaustive — treat it as "confirm no bypass mechanism applies to this identity," not as a fixed checklist to clear: classic branch protection's "include administrators" toggle, a classic protection custom role granted the "bypass branch protections" repository permission, a ruleset's explicit bypass list, and organization-owner or app-installation privileges that sit above repository-level rules entirely. Before picking a merge path (direct or delegated to Codex, per step 3 below), confirm that the identity that path would actually use has no route around the rule by any mechanism the repository's configuration provides — check only the identity for the path being considered, not both; the one never used for this merge is irrelevant to it.

If the rule isn't enabled, or the merging identity can bypass it, autonomous merge is not safe on that repository: this is a blocker (the "genuine blocker" case below) — escalate to the repository owner to enable it, remove the bypass, or merge manually, rather than attempting a manual pre-check that cannot be made atomic.

With that precondition confirmed, close the head-side gap immediately before merging, with nothing else done in between:

1. Re-fetch the PR's current head SHA and confirm it still equals `validated_head_sha`, and the base branch's current head SHA and confirm it still equals `validated_base_sha`.
2. If either does not match, do not merge. Treat this as `STALE`.
   - If only the PR head moved: the new head has not been validated. Re-run the definition-of-done checks against it before considering a merge again.
   - If the base branch moved: merge the current base into the PR branch and re-run the definition-of-done checks against the result before recording fresh `validated_head_sha`/`validated_base_sha` and merging — merging the base in cleanly is not by itself proof the combination is safe.
3. Only once both are confirmed unchanged, merge — do either:

   - Merge directly, using Claude's own GitHub write access, with the merge call itself scoped to `validated_head_sha` so it fails closed if the head has moved since step 1; or
   - Ask Codex to merge it: `@codex merge this pull request` is a third built-in command, alongside review and security review — exempt from the task contract (section 5) and the tracking marker (section 6) the same way, and posted the same way: the exact prefix below with nothing preceding it, then the branch details, the exact validated head SHA, and status on the following lines. Checking the head and then merging is not enough — a change can still land in the gap between Codex's check and its merge call. The request must instruct Codex to pass that expected SHA to the merge operation itself (GitHub's merge API accepts an expected head SHA and atomically rejects the merge if the PR's actual head no longer matches, rather than merging and reporting a mismatch after the fact), not merge first and validate after. Its response must report the commit it actually merged, so Claude can verify it against `validated_head_sha` per section 14's MERGE rule.

     ```text
     @codex merge this pull request

     Branch: `<branch>` into `<base_branch>`.
     Expected head: `<validated_head_sha>` — pass this as the expected head SHA to the merge call itself, not as a manual pre-check, so the merge atomically fails if the PR's actual head no longer matches.
     Status: [one line naming what made it complete, e.g. the review round and head SHA, or the validated acceptance criteria].
     ```

Record the merge commit SHA and a one-line summary of what shipped, in the same PR or Issue thread.

### The loop every PR goes through

CLAUDE.md (*Pull request lifecycle*) makes this loop mandatory for every PR Claude creates, at every tier and in every lane. The sections it cites keep their own rules; this only fixes the order and the exit.

1. **Review, at once.** Claude and codex lanes: as soon as the PR is open and `scripts/codex_round_count.py` exits 0, request the review (section 11); Codex's own opening review is off, so none comes unless requested. Codex-cli lane: no request is posted and the count script does not apply — the review is `scripts/codex_cli.py review` on the head the PR carries (`docs/codex-cli-lane.md`), and "request" below reads "run".
2. **Validate.** Check the response per section 14. Act on a finding only after confirming it against the code (section 13); a wrong one is answered on its thread with the evidence, never ignored.
3. **Fix, then review the new head.** Fix each confirmed finding with a regression test and push. Request the next review on the new head SHA (each review command runs once per head), with no request unanswered (section 16).
4. **Exit when clean.** The loop ends when a review whose `Reviewed commit:` is the *current* PR head carries no open finding — a no-findings verdict such as "Didn't find any major issues", never the review-status summary comment (section 16) — and every earlier finding was fixed or rejected with evidence. A finding is not open when it was fixed, or when it is a P2/P3 whose rejection stands under "No review limit"; a rejected P0/P1 is open until that section's disagreement rules close it. A fix pushed after the last review is unreviewed and does not end the loop.
5. **Merge.** Check the definition of done above and merge under this section without asking anyone.
6. **Continue.** After the merge the session takes its next outstanding task (CLAUDE.md); it does not stop to ask.

Reviews have no cap (section 16), so the loop runs until a review is clean; the two exits in "No review limit" (an unresolved P0/P1 disagreement, a genuine blocker) are the only other ways out. If Codex cannot be reached (section 17, section 18), the PR is `BLOCKED`, never merged unreviewed; re-recording the requirement in another lane (section 1) moves who does the work and never waives the PR's Codex review.

A human is needed only for:

- **A genuine blocker**: the task cannot reach the definition of done above without a decision only a human can make — for example two valid designs with different tradeoffs and no documented preference, an ambiguous or missing acceptance criterion, a finding Claude cannot verify from the repository alone, or an unresolved P0 or P1 disagreement with Codex (section 16, "No review limit").
- **A change to the original requirement**: what should be built has itself changed, not just how to build it.

Escalate to the repository owner in those two cases and only those two; do not hold a completed task open waiting for a merge approval that this protocol does not require.

## 21. Official reference

Codex GitHub integration:

```text
https://developers.openai.com/codex/third-party/github
```
