# Agent budget: resolved by a script, enforced by a hook

The routing policy has always said that `budget.max_agent_invocations` is a
ceiling and that policy enforcement "must not be probabilistic". Until these
two scripts existed, both claims were prose: the model read `policy.yaml` and
applied it by hand, and nothing counted the subagents it then spawned.

| | Was | Is |
|---|---|---|
| Overrides, profile, conditional agents, reserves, ceiling | applied by the model, in its head | computed by `scripts/route.py` |
| The ceiling at run time | a sentence in CLAUDE.md | refused by `scripts/hooks/agent_budget.py` |
| An eval failure on tier or roster | "the classifier was wrong" | attributable: scored wrong, or applied the policy wrong |

## `scripts/route.py` — the deterministic half of the classifier

The classifier's judgement ends at six scores and a list of verb-first
signals. Everything after that is a pure function of `policy.yaml`:

```bash
python3 scripts/route.py --dimensions 1,1,1,1,2,2 --signals changes-schema,runs-backfill
python3 scripts/route.py -d ambiguity=2,scope=2,architecture=2,dependencies=2,risk=2,verification=2 \
    -s changes-authentication-flow -s migrates-data -s rolls-out-across-clients
python3 scripts/route.py --from classification.yaml            # scores and signals from a file
python3 scripts/route.py --from classification.yaml --check    # diff its route against the policy
```

It computes, in the policy's own order:

1. the raw score and the score tier;
2. the effective signals — `signal_taxonomy.read_only_exempt` entries are
   dropped and reported under `dropped_read_only_signals`; anything outside the
   policy vocabulary is reported under `unknown_signals` and matches nothing;
3. every matching override (`match_any_signal`, `match_all_signals`) and the
   final tier, which can only rise;
4. the profile's strategy and `required_agents`, plus each `conditional_agents`
   arm evaluated independently (OR across entries, never AND — an entry that
   combines a threshold and a signal list is rejected as ambiguous);
5. the ceiling: the tier's base plus any reserve whose `grant_condition` is a
   dimension threshold the scores already satisfy and whose `grantable_agents`
   include a conditional agent that fired. `analyst_reserve` qualifies on an
   ambiguity-2 T3. `escalation_reserve` never does — its condition is a
   reviewer's finding, so it is listed under `budget.runtime_reserves` and
   left out of the ceiling. A `grant_condition` that is neither a dimension
   threshold nor a runtime condition the resolver knows
   (`RUNTIME_GRANT_CONDITIONS`) is an error, not a runtime reserve: a misspelt
   threshold would otherwise stop granting at classification time and become
   grantable by hand while routing stayed green;
6. `human_gate`, from any override flagged `human_gate: true` (none today).

`parallelizable` is not emitted. The policy's parallelism rules are prose the
session judges; the resolver only owns what is computable.

`--check` reads a classification the model emitted, recomputes the route from
the model's own scores and signals, and exits 1 with the mismatching fields.
That is the tool the eval runner uses.

### What the eval runner now reports

`scripts/run-routing-evals.py` still scores the model's route against the
oracle. It additionally resolves each emission and reports:

- **Route accuracy, resolved from scores** — the route `policy.yaml` assigns to
  the model's own scores and signals, against the oracle. When this is high and
  plain route accuracy is lower, the model scores well and applies the policy
  badly — a skill-wording problem, not a rubric problem.
- **Policy self-applied OK** — how often the model's emitted route equals the
  resolver's. Every miss is one case where the model misread the policy.

Because the resolver reproduces every oracle case from the oracle's own
`semantic_hints` and `signals` (`tests/test_route.py`, the golden test), the
oracle's routes are now a machine-checked consequence of the policy rather
than a parallel copy of it. Edit `policy.yaml` and the test says which oracle
entries no longer follow.

## `scripts/hooks/agent_budget.py` — the ceiling as a refusal

```bash
python3 scripts/route.py --dimensions ... --signals ... --record --requirement REQ-007
```

`--record` writes `artifacts/handoff/agent-budget.json`: the tier, the base
ceiling, reserves granted at classification time, reserves that can still be
granted at run time, and the invocation list. It lives beside the session
handoff and is gitignored for the same reason — it describes one checkout's
requirement in progress. A restarted session resumes the same budget.

Re-recording the **same** requirement carries its spending over. The refusal
message tells a session whose requirement grew to re-classify and re-record;
if that started a fresh ledger, a T2 that had spent both base slots could
re-record as T3 and spawn three more. So when the `--requirement` label is
unchanged (or absent on either side), every invocation already charged, and
every runtime reserve already granted, is charged again against the new
ceiling in its original order — a charge the new ceiling cannot hold stays on
the record and blocks further spawns — an overdrawn ledger refuses every
agent, reserves included. A different label starts from zero and says so on
stderr; `--record --fresh` or `agent_budget.py reset` starts from zero
explicitly. The whole load/carry/write runs under the same lock the hook
takes, so a spawn charged while a re-record is in flight is never
overwritten by the re-record's snapshot.

The hook runs on `PreToolUse` with matcher `Agent|Task` — the spawn tool is
named `Task` in some Claude Code releases and `Agent` in others, and Claude
Code matches a `|`-joined list of plain names exactly, name by name, rather
than as a regex (so `Task` never bleeds onto `TaskCreate`). The test suite
ties that matcher to the hook's own `SPAWN_TOOLS`. It charges every spawn:

- to an unspent reserve whose `grantable_agents` name the subagent type — so an
  analyst spawned first takes its own slot and leaves the base to the roster;
- otherwise to the base, until it is full;
- otherwise it **denies** the spawn, with the policy's escalation guidance:
  report the partial result, grant the reserve if the reviewer earned it, or
  re-classify if the requirement itself grew.

Every spawn counts, exploration agents included — the policy counts every
subagent invocation. A T0 ledger refuses the first one.

```text
Charged `code-reviewer` to base. Agent budget for REQ-007: T2 (main_with_architect_and_reviewer)
· base 1/2 used · ceiling 2 · invocations: code-reviewer.
```

```text
Agent budget exhausted: T2 ceiling is 2 (base 2 + 0 reserve), all charged to: code-reviewer,
Explore. Spawning `test-engineer` would exceed budget.max_agent_invocations … If reviewer
formally escalated for verification design, grant the reserve first:
`python3 scripts/hooks/agent_budget.py grant escalation_reserve --agent test-engineer`.
```

### Reserves and resumes

| Command | When |
|---|---|
| `agent_budget.py grant escalation_reserve --agent test-engineer --reason "…"` | After `code-reviewer`/`code-reviewer-t3` has formally reported that verification needs its own design. Refused past the policy's amount, and for an agent the reserve may not pay for. |
| `agent_budget.py charge --agent architecture-critic --reason "critique round 2, resumed"` | A resumed agent is an invocation the policy counts but the hook cannot see (it is a message, not a spawn). Charge it by hand; the same accounting applies. |
| `agent_budget.py status [--json]` | The ledger, as a sentence or in full. |
| `agent_budget.py reset` | Remove the ledger. Recording a route under a different `--requirement` label, or with `--fresh`, also starts from zero; re-recording the same label carries charges over. |

### When no budget is recorded

A spawn with no ledger is **allowed and not counted**, with a nudge to
classify first. That keeps a question-answering session usable without a
ceremony. Set `CLAUDE_AGENT_BUDGET_UNROUTED=deny` to refuse unrouted spawns
instead — the right setting for a checkout where every task goes through the
routing flow.

### Failure posture

The hook fails open on its own errors, like `session_budget.py`: an unreadable
ledger is reported on stderr and treated as unrouted, never overwritten, and
an unexpected exception exits 0 without a decision. It never fails open on a
full ledger — a refusal is a decision, and the hook exits 0 with it.

Two spawns issued in one turn fire two hooks at once. An OS advisory lock on
`agent-budget.lock` beside the ledger serialises them (`flock` on POSIX,
`msvcrt.locking` on Windows). The kernel drops the lock when its holder exits,
so a hook that dies mid-write leaves nothing to reclaim — a reclaim-by-age
scheme has an ABA race in which two waiters both judge the same lock stale and
the second deletes the one the first just re-created. For the same reason the
lock file is never unlinked: unlinking a locked file lets the next opener lock
a different inode under the same name. After three seconds without the lock
the hook proceeds without it and says so on stderr.

### What it cannot see

- **Resumes** (`SendMessage` to an existing subagent): charge by hand, above.
- **Depth**: `max_subagent_spawn_depth: 1` is enforced by the agent definitions
  (none carries the `Agent` tool), not by this hook.
- **Concurrency**: `max_concurrent_subagents` is a schedule, not a count; the
  hook counts.

## Enabling it

Both hooks are wired in `.claude/settings.example.json`; copying it to
`.claude/settings.json` or `.claude/settings.local.json` enables them together
with the session-budget hooks. `SessionStart` replays the ledger's state into
a new session so a restart knows what it has already spent.

## Tests

```bash
python3 -m unittest tests.test_route tests.test_agent_budget -v
python3 scripts/mutation_check.py tests/mutations/route.yaml
python3 scripts/mutation_check.py tests/mutations/agent_budget.yaml
```
