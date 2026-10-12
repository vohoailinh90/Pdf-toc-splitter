"""Consistency checks for the pdf-toc-splitter routing overlay.

The overlay is a domain layer on top of agent-routing/policy.yaml. Its value
rests on properties that are cheap to assert and easy to break by hand: that
it never redefines the tier model it inherits, that every signal it maps lands
on a base signal that still fires the override it relies on, and - the one
that matters most - that every change class's tier floor is what its signals
actually resolve to through the policy, because scripts/route.py reads the
policy and never the overlay. These are computable answers, so they are a
script, not a review.
"""
import ast
import fnmatch
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.yamlcompat import require_yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "agent-routing" / "policy.yaml"
OVERLAY = ROOT / "agent-routing" / "pdf-toc-splitter-overlay.yaml"
PROCESS = ROOT / "docs" / "PROCESS.md"
CLASSIFIER = ROOT / ".claude" / "skills" / "classify-requirement" / "SKILL.md"
VERBS = {
    "adds", "calls", "changes", "coordinates", "creates", "deletes", "drops", "executes", "exposes",
    "issues", "migrates", "moves", "overwrites", "reads", "requires", "rolls", "rotates", "runs",
    "stores", "uses", "writes",
}


class Fixture(unittest.TestCase):
    def setUp(self):
        yaml = require_yaml()
        self.policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
        self.overlay = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))
        self.tiers = [t["tier"] for t in self.policy["score_tiers"]]
        self.domain = set(self.overlay["domain_signals"])
        self.exempt = set(self.overlay["read_only_exempt"])
        self.classes = {c["id"]: c for c in self.overlay["change_classes"]}

    def rank(self, tier):
        return self.tiers.index(tier)

    def routed_as(self, signal):
        """The signal the resolver actually sees for a domain signal."""
        return self.overlay["signal_mapping"].get(signal, {}).get("base_signal", signal)

    def overrides_firing(self, signal):
        return [o for o in self.policy["overrides"] if signal in o.get("match_any_signal", [])]

    def floor_of(self, signals):
        tiers = [o["minimum_tier"] for s in signals for o in self.overrides_firing(self.routed_as(s))]
        return max(tiers, key=self.rank) if tiers else "T0"


class Inheritance(Fixture):
    def test_points_at_the_real_base_policy(self):
        self.assertTrue((ROOT / self.overlay["base_policy"]).is_file())

    def test_never_redefines_an_inherited_section(self):
        for section, source in self.overlay["inherits"].items():
            with self.subTest(section=section):
                self.assertEqual(source, "base")
                self.assertNotIn(section, self.overlay)

    def test_declares_the_sections_that_matter_as_inherited(self):
        for section in ("scoring", "score_tiers", "profiles", "budget"):
            self.assertIn(section, self.overlay["inherits"])

    def test_the_overlay_defines_no_override_of_its_own(self):
        self.assertNotIn("overrides", self.overlay, "route.py cannot see overlay overrides; put them in policy.yaml")


class Signals(Fixture):
    def test_every_signal_is_verb_first(self):
        for signal in sorted(self.domain | self.exempt):
            with self.subTest(signal=signal):
                self.assertIn(signal.split("-", 1)[0], VERBS)

    def test_read_only_signals_are_disjoint_from_actionable_ones(self):
        self.assertEqual(self.domain & self.exempt, set())

    def test_no_read_only_signal_can_route(self):
        routed = {s for o in self.policy["overrides"] for s in o.get("match_any_signal", []) + o.get("match_all_signals", [])}
        self.assertEqual(self.exempt & (routed | set(self.overlay["signal_mapping"])), set())

    def test_mapping_keys_are_declared_domain_signals(self):
        self.assertEqual(set(self.overlay["signal_mapping"]) - self.domain, set())

    def test_every_mapping_still_resolves_to_its_declared_override_and_tier(self):
        for domain, spec in self.overlay["signal_mapping"].items():
            with self.subTest(signal=domain):
                firing = {o["id"]: o for o in self.overrides_firing(spec["base_signal"])}
                self.assertIn(spec["expected_override"], firing, f"{spec['base_signal']} now fires {sorted(firing)}")
                self.assertEqual(firing[spec["expected_override"]]["minimum_tier"], spec["expected_minimum_tier"])

    def test_every_domain_signal_reaches_the_resolver(self):
        """A signal that neither maps onto a base signal nor appears in a policy
        override would be reported as unknown and route nothing."""
        for signal in sorted(self.domain):
            with self.subTest(signal=signal):
                self.assertTrue(self.overrides_firing(self.routed_as(signal)), f"{signal} routes nothing")


class ChangeClasses(Fixture):
    def test_ids_are_unique_and_floors_are_known_tiers(self):
        ids = [c["id"] for c in self.overlay["change_classes"]]
        self.assertEqual(len(ids), len(set(ids)))
        for c in self.overlay["change_classes"]:
            self.assertIn(c["tier_floor"], self.tiers)

    def test_class_signals_are_declared(self):
        for c in self.overlay["change_classes"]:
            for signal in c.get("signals", []):
                with self.subTest(change_class=c["id"], signal=signal):
                    self.assertIn(signal, self.domain)

    def test_each_floor_is_exactly_what_its_signals_resolve_to_in_the_policy(self):
        for c in self.overlay["change_classes"]:
            with self.subTest(change_class=c["id"]):
                self.assertEqual(self.floor_of(c.get("signals", [])), c["tier_floor"])

    def test_a_class_above_t0_carries_a_signal(self):
        for c in self.overlay["change_classes"]:
            if c["tier_floor"] != "T0":
                with self.subTest(change_class=c["id"]):
                    self.assertTrue(c.get("signals"))

    def test_gated_classes_are_t3(self):
        for c in self.overlay["change_classes"]:
            if c.get("human_gate"):
                with self.subTest(change_class=c["id"]):
                    self.assertEqual(c["tier_floor"], "T3")

    def test_a_gated_class_is_matched_by_an_action_and_shares_no_path_with_an_ungated_one(self):
        """Classes are matched cumulatively, so an overlap promotes: if a gated
        T3 class named split(), every ordinary edit there would become gated."""
        ungated = [(c["id"], p) for c in self.overlay["change_classes"] if not c.get("human_gate") for p in c["paths"]]
        for c in self.overlay["change_classes"]:
            if not c.get("human_gate"):
                continue
            for path in c["paths"]:
                with self.subTest(change_class=c["id"], path=path):
                    self.assertIn(" ", path, "a gated class must describe an action, not a file")
                    for other, other_path in ungated:
                        self.assertFalse(path == other_path or fnmatch.fnmatch(path, other_path), other)

    def test_discrimination_rules_state_both_sides(self):
        for rule in self.overlay["discrimination_rules"]:
            with self.subTest(about=rule["about"]):
                self.assertTrue(rule["is_signal"].strip() and rule["is_not_signal"].strip())


class HumanGates(Fixture):
    def test_every_gate_is_a_declared_signal_that_a_class_carries(self):
        carried = {s for c in self.overlay["change_classes"] for s in c.get("signals", [])}
        for gate in self.overlay["human_gates"]:
            with self.subTest(gate=gate):
                self.assertIn(gate, self.domain)
                self.assertIn(gate, carried)

    def test_no_read_only_signal_is_gated(self):
        self.assertEqual(set(self.overlay["human_gates"]) & self.exempt, set())

    def test_every_signal_of_a_gated_class_is_a_gate(self):
        for c in self.overlay["change_classes"]:
            if c.get("human_gate"):
                for signal in c["signals"]:
                    with self.subTest(change_class=c["id"], signal=signal):
                        self.assertIn(signal, self.overlay["human_gates"])


class RequiredChecks(Fixture):
    def test_every_check_guards_a_real_class(self):
        for check in self.overlay["required_checks"]:
            for guarded in check["guards"]:
                if guarded != "always":
                    with self.subTest(check=check["id"], guards=guarded):
                        self.assertIn(guarded, self.classes)

    def test_every_check_is_specified_in_the_process_document(self):
        process = PROCESS.read_text(encoding="utf-8")
        for check in self.overlay["required_checks"]:
            with self.subTest(check=check["id"]):
                self.assertIn(check["id"], process)

    def test_every_t2_plus_ungated_class_is_guarded_by_a_check(self):
        guarded = {g for c in self.overlay["required_checks"] for g in c["guards"]}
        for c in self.overlay["change_classes"]:
            if c["tier_floor"] in {"T2", "T3"} and not c.get("human_gate"):
                with self.subTest(change_class=c["id"]):
                    self.assertIn(c["id"], guarded)

    def test_the_checks_that_run_on_every_change_are_declared_always(self):
        always = {c["id"] for c in self.overlay["required_checks"] if c.get("always_runs")}
        self.assertTrue({"check_no_documents_committed", "check_required_checks_exist"} <= always)


class ResolverAgrees(Fixture):
    """The overlay is only worth its tiers if scripts/route.py produces them."""

    def route(self, *signals, dims="0,0,0,0,0,0"):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "route.py"), "--dimensions", dims,
                              "--signals", ",".join(signals), "--json"],
                             capture_output=True, text=True, cwd=ROOT, check=True)
        return json.loads(out.stdout)

    def test_each_class_resolves_to_its_floor_with_all_scores_zero(self):
        for c in self.overlay["change_classes"]:
            with self.subTest(change_class=c["id"]):
                result = self.route(*[self.routed_as(s) for s in c.get("signals", [])])
                self.assertEqual(result["final_tier"], c["tier_floor"])
                self.assertEqual(result["unknown_signals"], [])

    def test_a_floor_never_lowers_a_higher_score(self):
        self.assertEqual(self.route("changes-page-arithmetic", dims="2,2,2,1,1,1")["final_tier"], "T3")

    def test_the_t1_floor_includes_an_independent_reviewer_and_the_t3_floor_a_critic(self):
        self.assertEqual(self.route("changes-toc-parsing")["required_agents"], ["code-reviewer"])
        self.assertIn("architecture-critic", self.route("executes-irreversible-operation")["required_agents"])


class ReferencesResolve(Fixture):
    """A profile that misdescribes the code it routes is worse than none."""

    def defined(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}

    def test_every_file_and_symbol_reference_resolves(self):
        for c in self.overlay["change_classes"]:
            for ref in c["paths"]:
                if " " in ref:
                    continue
                with self.subTest(change_class=c["id"], ref=ref):
                    name, _, symbol = ref.partition("::")
                    matches = sorted(ROOT.glob(name))
                    self.assertTrue(matches, f"{name} matches no file")
                    if symbol:
                        self.assertIn(symbol, self.defined(matches[0]))


class ClassifierConsumesTheOverlay(Fixture):
    def setUp(self):
        super().setUp()
        self.text = CLASSIFIER.read_text(encoding="utf-8")

    def test_it_shadows_the_base_skill_name_and_names_the_overlay(self):
        _, front, _ = self.text.split("---\n", 2)
        self.assertEqual(require_yaml().safe_load(front)["name"], "classify-requirement")
        self.assertIn("pdf-toc-splitter-overlay.yaml", self.text)

    def test_it_uses_every_overlay_mechanism(self):
        for mechanism in ("domain_signals", "read_only_exempt", "signal_mapping", "discrimination_rules",
                          "change_classes", "human_gates", "required_checks", "always_runs"):
            with self.subTest(mechanism=mechanism):
                self.assertIn(mechanism, self.text)

    def test_it_evaluates_conditional_agents_and_parallelism_and_the_budget(self):
        for needle in ("conditional_agents", "requirement-analyst", "prefer_concurrent_when_any",
                       "prefer_strict_sequence_when_any", "max_agent_invocations", "escalation_reserve",
                       "scripts/route.py", "union"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)


if __name__ == "__main__":
    unittest.main()
