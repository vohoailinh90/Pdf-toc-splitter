# Session budget

The routing policy budgets agents. This budgets the session — the context that
exists at every tier, including T0, and the one that costs the most when it is
lost mid-requirement.

## Why a restart is not a re-run

Losing a session at 80% and starting over does not cost 80% again. It costs
substantially more than the original run:

| | Cause | Rough size |
|---|---|---|
| Cold prompt cache | A warm session re-reads its history at ~0.1x input price. A cold one pays full price plus a cache write (1.25x at the 5-minute TTL, 2x at the 1-hour TTL). | ~10x on the re-read, and a rate-limit wait guarantees the miss |
| Quadratic turn cost | Every turn resends the whole conversation, so total spend for an N-turn session grows with N², not N. Reaching the same depth twice pays the integral twice. | 2x the curve, plus the turns spent working out where the last run stopped |
| Re-derivation | The reads, greps and test runs that produced the session's conclusions are gone. Usually the largest single block of tokens in a task. | Paid twice in full |
| Compaction | Compacting reads the whole context and writes a summary; the summary is lossy, so the files get read again to recover the detail. | The same information billed twice, plus the summarisation |
| Lost subagent contexts | Each subagent built its own context. A T3 requirement that dies after three invocations rebuilds all three. | Multiplies by roster size |

Only what reached disk survives. That is what the handoff file is for.

## What is instrumented

### Status line

`scripts/hooks/statusline.py` renders the three numbers that decide when to
stop, none of which the model can see for itself:

```text
Opus · my-repo · ████░░░░░░ 62% ctx · 5h 41% · cache 91% · $1.23
```

The bar and the number are deliberately inverses: **the bar fills as context is
consumed, the number is what is left**. So `████░░░░░░ 62%` means 38% used, 62%
still available. Context and rate-limit headroom are colour-coded (green above
30%, yellow from 30%, red from 15%) — the same 70%/85% occupancy the hook
thresholds use, read from the other side.

**Segments appear only when their data does:**

| Field | Availability |
|---|---|
| `model.display_name`, `cost.total_cost_usd` | Always present. |
| `context_window` | Always present. |
| `workspace` | Always present. Renders the basename of `git_worktree` when a release provides it, otherwise of `project_dir`. |
| `rate_limits` | Claude.ai Pro and Max subscribers, or behind a Claude apps gateway that sets a spend limit; only after the session's first API response. Each window (`five_hour`, `seven_day`, `spend_limit`) is **independently** absent and is rendered independently; Claude Code drops a window once its `resets_at` passes. Requires Claude Code v2.1.251+. |
| `prompt_cache` | Absent until the main conversation's first API response. Requires Claude Code v2.1.251+. |

The script renders whatever is present and omits the rest rather than showing a
placeholder, so on a plan without `rate_limits` the line is simply shorter. If
you never see the 5h/7d segment, that is the expected behaviour, not a bug —
check your CLI version against the table before treating it as one.

The five-hour window also shows its reset clock (`→14:30`), but only once that
window is at least 60% used: before then the time is noise, and after it the
question is whether to wait or wrap up.

The payload comes from a different program on a different release cadence, so
the renderer treats every field as optional and every value as possibly the
wrong type. Percentages are clamped to `0-100` (the bar is always ten cells,
whatever the payload claims), `NaN`/`Infinity`/booleans read as absent rather
than as numbers, newlines and escape sequences in payload strings are stripped
so a name cannot break the line, and a malformed reset timestamp costs the
clock rather than the numbers beside it. When something is discarded the script
still exits `0`, but says why on stderr: a segment that silently stops
rendering is indistinguishable from one that legitimately has no data.

### Hooks

| Event | Matcher | Behaviour |
|---|---|---|
| `PreToolUse` | `*` | Estimates context occupancy from the transcript tail. At ~70% asks the session to update the handoff; at ~85% asks it to wrap up. Fires **once per level per session**, never blocks. |
| `PreToolUse` | `Agent\|Task` | `scripts/hooks/agent_budget.py`: charges the spawn to the requirement's agent budget and **denies** it past the ceiling. See [`agent-budget.md`](agent-budget.md). |
| `PreCompact` | `auto` | Captures the handoff before auto-compaction summarises the context away. |
| `StopFailure` | `rate_limit` | Captures the handoff at the moment a rate limit ends the turn. |
| `SessionStart` | `startup\|resume\|compact` | Prints an existing handoff to stdout, which Claude Code adds to the new session's context. `agent_budget.py session-start` does the same for the agent-budget ledger. |

`StopFailure` and `PreCompact` are the two events that fire *exactly* when a
session is about to lose its context, which is why they own the capture rather
than a timer.

Every subcommand fails open: unexpected errors exit 0 with a note on stderr. A
guardrail that breaks the session it protects is worse than no guardrail.

`PreToolUse` runs on every tool call, so its cost is worth stating plainly:
measured at **~40 ms per call**, almost all of it Python interpreter startup —
the transcript itself is read from the tail and parsed lazily from the newest
line, so a 400 KB transcript costs the same as an empty one. If that latency is
not worth the guardrail for your workload, drop the `PreToolUse` entry and keep
`PreCompact`/`StopFailure`/`SessionStart`: the capture and replay are what make
a restart cheap, and the nudge is only what makes it more likely to happen
before the capture is forced.

### Handoff file

`artifacts/handoff/SESSION-HANDOFF.md`, gitignored — it quotes the last prompt
and describes one machine's working tree, so it is local session state, not
repository content.

Two blocks, and the split is the design:

- `auto-state` — regenerated on every capture from git and the transcript.
  Branch, HEAD, uncommitted paths, diffstat, recent commits, context occupancy,
  the last request. Computable, so a script computes it.
- `model-notes` — preserved verbatim across every capture. Goal, done,
  remaining, next command, decisions already settled. Not computable, so the
  session writes it.

A capture never overwrites `model-notes`, and a hand-edit inside `auto-state`
will be overwritten by the next capture.

## Enabling it

```bash
# share the guardrails with everyone working in the repo
cp .claude/settings.example.json .claude/settings.json

# or keep them to your own checkout (.claude/settings.local.json is gitignored)
cp .claude/settings.example.json .claude/settings.local.json
```

This repository ships `.claude/settings.json` already enabled (a copy of the example), so a
session started here loads the hooks; a repository created from the template inherits that file
and should delete or edit it if it does not want the guardrails. The example stays the contract
the tests check, and a test fails if `settings.json` drifts from it. The example invokes
`python3`; on Windows change it to `python` if that is what is on PATH.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_SESSION_BUDGET_CONTEXT_WINDOW` | `200000` | Context window used for the occupancy estimate. Set to `1000000` for a 1M-context model — hook payloads, unlike the status line payload, do not carry the window size. |

Thresholds live in `scripts/hooks/session_budget.py` as `WRITE_HANDOFF_AT`
(0.70) and `WRAP_UP_AT` (0.85).

The occupancy estimate sums `input_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens` and `output_tokens` from the newest usage block in
the transcript — an assistant turn is billed for everything it read plus what it
wrote, which is the closest proxy a hook can compute. It is an estimate; the
thresholds are set to tolerate that.

## Manual use

```bash
echo '{}' | python3 scripts/hooks/session_budget.py capture
```

## Tests

```bash
python3 -m unittest tests.test_session_budget -v
```

`SettingsConformanceTests` asserts that every hook command in
`.claude/settings.example.json` names a subcommand the script actually
implements. A drifted name fails silently at runtime, and per
*Deterministic work is not agent work* in CLAUDE.md, a validator is the check —
not a reviewer.
