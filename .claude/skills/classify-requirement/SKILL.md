---
name: classify-requirement
description: Classify a pdf-toc-splitter requirement into a T0-T3 execution profile using the base scoring rubric, the base routing policy, and this repository's routing overlay. Use before choosing subagents for non-trivial engineering work in this repository.
argument-hint: "[requirement text]"
---

Classify this requirement:

$ARGUMENTS

Sources of truth, all three, in this order:

1. `agent-routing/complexity-rubric.md` - the six scoring dimensions
2. `agent-routing/policy.yaml` - tiers, profiles, budgets, base overrides
3. `agent-routing/pdf-toc-splitter-overlay.yaml` - this repository's domain layer

The overlay is **not optional**. It is where this repository's signals, change
classes and discrimination rules live. Classifying with the base policy alone
ignores the two risks this tool actually has: a bug in `split()` that writes
over a user's file, and a bug in the page arithmetic that exports the wrong
pages without raising anything.

## Procedure

1. Parse the requirement semantically. State what the change **does**, as one
   verb-first sentence, before scoring anything.
2. Inspect enough of `pdf_toc_splitter/` to avoid a clearly wrong score. The
   package is small; read the function the change touches.
3. **Fast path.** Look the change up in the overlay's `change_classes` and
   collect **every** class it matches, not the first one. A change to
   `plan()` that also renames a flag matches `page-planning` *and*
   `cli-surface`; recording one drops the other's deterministic check. Take
   the **maximum** `tier_floor`, and the **union** of `signals`, `human_gate`
   flags and guarded checks. A class whose path contains a space describes an
   **action** (`destructive-io`, `release`, `password-handling`): match it by
   what the change does, never by which file it edits.
4. **Full path.** Scoring always happens - a matching class never replaces it.
   The class contributes signals (and through them its floor); the score decides
   whether the tier rises above that floor. Score each dimension 0-2: ambiguity,
   scope, architecture, dependencies, risk, verification. Sum the raw score (0-12).
5. Extract signals as **actions the change performs**, using the base
   `signal_taxonomy` plus the overlay's `domain_signals`. Ask "what does this
   change do?", not "what does this requirement mention?".
6. Run every candidate signal past the overlay's `discrimination_rules`
   (`pdf-files`, `password`, `documentation`) before keeping it.
7. Drop every signal listed in the base `signal_taxonomy.read_only_exempt` or
   the overlay's `read_only_exempt`. A read-only signal never raises a tier.
   If it genuinely adds risk, that belongs in the risk or verification score.
8. Apply the overlay's `signal_mapping`: translate each surviving domain
   signal that has a mapping into its base signal (`overwrites-user-file` ->
   `executes-irreversible-operation`). Report both names - the domain signal
   is what the change does, the base signal is what routes it. A domain
   signal without a mapping (`changes-page-arithmetic`, `changes-output-write-path`
   ...) is passed to the resolver as it is: the repository-local overrides at
   the end of `policy.yaml` turn it into its class floor.
9. Resolve the policy with the resolver, never by hand, passing the six scores
   and every surviving signal (mapped base names plus the unmapped domain names):

   ```bash
   python3 scripts/route.py --dimensions <a>,<s>,<arch>,<dep>,<risk>,<verif> \
       --signals <signal>,<signal>
   ```

   Copy `raw_score`, `score_tier`, `overrides_applied`, `final_tier`,
   `strategy`, `required_agents` and `max_agent_invocations` verbatim. The
   class floors are already inside `final_tier`: each change class's `signals`
   resolve to its `tier_floor` (asserted by `tests/harness/test_overlay.py`),
   so nothing is applied by hand. If it reports `unknown_signals`, a name
   drifted from the overlay: fix the name, then re-run.
10. Evaluate the profile's `conditional_agents`: each entry fires on its own
    (OR across entries, never AND). `architect` at T2 when architecture scored
    2 or a signal in its `when_any_signal` list fired; `requirement-analyst`
    at T3 when ambiguity scored 2. `scripts/route.py` reports these as
    `conditional_agents_fired`; copy them verbatim.
11. Read `max_agent_invocations` from the resolver output as the base ceiling.
    Never add `escalation_reserve` at classification time: it is granted only
    after a reviewer formally escalates mid-review.
12. Set `human_gate.required` if **any** matched change class carries
    `human_gate: true`, or any surviving domain signal is in the overlay's
    `human_gates`. Every entry there is a declared signal, so the match is
    total.
13. List the overlay's `required_checks` whose `guards` include **any** matched
    change class, **or** whose `always_runs` flag is true.
    `check_no_documents_committed` runs on every change: dropping it because
    no class matched is how a confidential PDF gets committed. These are
    deterministic tests under `tests/checks/`; they do not consume budget.
14. When this classification is for a requirement the session will now
    execute, re-run step 9 with `--record --requirement <id>` so the
    `PreToolUse` hook enforces the ceiling (`docs/agent-budget.md`).
15. Decide `parallelizable` from the base `parallelism` section:
    `prefer_concurrent_when_any` against `prefer_strict_sequence_when_any`;
    sequencing wins where both apply. Parallelism changes **when** agents run,
    never how many, and never raises `max_agent_invocations`.

## Output format

Return exactly one YAML block with this shape:

```yaml
requirement_summary: "..."
change_classes: []
classification:
  ambiguity: 0
  scope: 0
  architecture: 0
  dependencies: 0
  risk: 0
  verification: 0
raw_score: 0
domain_signals: []
mapped_base_signals: []
dropped_read_only_signals: []
score_tier: T0
change_class_floor: null
overrides_applied: []
final_tier: T0
strategy: main_session
required_agents: []
conditional_agents_fired: []
max_agent_invocations: 0
required_checks: []
parallelizable: false
human_gate:
  required: false
  reason: null
rationale:
  - "..."
```

## Guardrails

- Do not inflate scores because the requirement uses sophisticated terminology.
- Do not lower risk because the change is small. In this repository the most
  dangerous edits are the smallest: changing `'xb'` to `'wb'` in `split()`, or
  `starts[i+1]-1` to `starts[i+1]` in `plan()`, is one character and ships
  silently.
- Signals must be verb-first actions. `password` is not a signal;
  `changes-password-handling` and `uses-existing-password-prompt` are, and
  only the first can raise a tier.
- Never emit more required agents than the budget allows.
- Do not add a role for work that is deterministic. If the verification is
  counting, hashing or conformance checking, the answer is a check from
  `required_checks`, not an agent.
- Attach a class's signal only when the change really performs that action. A
  wording or comment edit inside `parse_toc` does not change parsing, so it
  carries no signal and routes by its score. If you believe a class floor is
  wrong, say so in `rationale` and fix the overlay and the policy block in the
  same change - never route silently below it.
- Never pick an execution lane. Whether Codex executes the tier is the
  operator's choice after classification (`docs/codex-lane.md`).
