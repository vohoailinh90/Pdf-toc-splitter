"""The installed process is wired to files that exist.

A hook that points at a missing script, an agent the policy names but nobody
defined, or a CLAUDE.md that sends a reader to a path that is not there all
fail silently at the moment they are needed. Each is a lookup, so each is a test.
"""
import json
import re
import unittest
from pathlib import Path

from tests.support.yamlcompat import require_yaml

ROOT = Path(__file__).resolve().parents[2]
PATH_REFERENCE = re.compile(r"`((?:docs|scripts|tests|\.claude|agent-routing|pdf_toc_splitter|examples)/[\w./-]*[\w/])`")
# Files a machine creates at run time; they are gitignored and absent in a fresh checkout.
RUNTIME_PATHS = ("artifacts/", ".claude/routing-lane.local", ".claude/settings.local.json")


def frontmatter(path):
    _, front, _ = path.read_text(encoding="utf-8").split("---\n", 2)
    return require_yaml().safe_load(front)


class Settings(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def commands(self):
        for entries in self.settings["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    yield hook["command"]
        yield self.settings["statusLine"]["command"]

    def test_every_hook_script_exists(self):
        for command in self.commands():
            script = re.search(r"\$\{CLAUDE_PROJECT_DIR\}/(scripts/[\w/]+\.py)", command)
            with self.subTest(command=command):
                self.assertIsNotNone(script)
                self.assertTrue((ROOT / script.group(1)).is_file())

    def test_the_spawn_budget_hook_is_wired_to_both_names_of_the_spawn_tool(self):
        matchers = [e["matcher"] for e in self.settings["hooks"]["PreToolUse"]
                    if any("agent_budget.py" in h["command"] for h in e["hooks"])]
        self.assertEqual(matchers, ["Agent|Task"])

    def test_nothing_refers_to_the_dashboard_that_is_not_installed(self):
        self.assertNotIn("office_events", json.dumps(self.settings))

    def test_the_example_matches_the_active_settings(self):
        example = json.loads((ROOT / ".claude" / "settings.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example, self.settings)


class Roster(unittest.TestCase):
    def setUp(self):
        self.policy = require_yaml().safe_load((ROOT / "agent-routing" / "policy.yaml").read_text(encoding="utf-8"))
        self.defined = {p.stem for p in (ROOT / ".claude" / "agents").glob("*.md")}

    def named_by_the_policy(self):
        names = set()
        for profile in self.policy["profiles"].values():
            names.update(profile["required_agents"])
            names.update(entry["agent"] for entry in profile["conditional_agents"])
        names.update(entry["agent"] for entry in self.policy["escalation_only_agents"])
        for lane in self.policy["lanes"].values():
            if isinstance(lane, dict):
                for profile in lane.get("profiles", {}).values():
                    names.update(profile["claude_agents"])
        for reserve in ("analyst_reserve", "escalation_reserve"):
            names.update(self.policy["budget"][reserve]["grantable_agents"])
        return names

    def test_every_agent_the_policy_names_is_defined(self):
        self.assertEqual(self.named_by_the_policy() - self.defined, set())

    def test_every_defined_agent_is_named_by_the_policy_and_this_repo_adds_none(self):
        self.assertEqual(self.defined - self.named_by_the_policy(), set())

    def test_agent_frontmatter_is_complete(self):
        for name in sorted(self.defined):
            meta = frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")
            with self.subTest(agent=name):
                self.assertEqual(meta["name"], name)
                self.assertGreater(meta["maxTurns"], 0)
                self.assertIn(meta["effort"], {"low", "medium", "high", "xhigh", "max"})

    def test_reviewers_cannot_edit_code(self):
        for name in ("code-reviewer", "code-reviewer-t3", "code-reviewer-verify", "architecture-critic", "architect", "test-engineer"):
            tools = {t.strip() for t in str(frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")["tools"]).split(",")}
            with self.subTest(agent=name):
                self.assertFalse(tools & {"Write", "Edit"})

    def test_every_skill_has_a_matching_name(self):
        skills = sorted((ROOT / ".claude" / "skills").iterdir())
        self.assertTrue(skills)
        for skill in skills:
            with self.subTest(skill=skill.name):
                self.assertEqual(frontmatter(skill / "SKILL.md")["name"], skill.name)

    def test_the_local_policy_overrides_use_only_declared_signals(self):
        overlay = require_yaml().safe_load((ROOT / "agent-routing" / "pdf-toc-splitter-overlay.yaml").read_text(encoding="utf-8"))
        local = [o for o in self.policy["overrides"] if o["id"].startswith("pdf-toc-splitter-")]
        self.assertEqual(len(local), 2)
        for override in local:
            self.assertLessEqual(set(override["match_any_signal"]), set(overlay["domain_signals"]))


class DocumentationPointsAtRealFiles(unittest.TestCase):
    def test_backticked_repository_paths_exist(self):
        for document in (ROOT / "CLAUDE.md", ROOT / "docs" / "PROCESS.md", ROOT / "README.md"):
            for reference in sorted(set(PATH_REFERENCE.findall(document.read_text(encoding="utf-8")))):
                if reference.startswith(RUNTIME_PATHS) or "<" in reference or "*" in reference:
                    continue
                with self.subTest(document=document.name, reference=reference):
                    self.assertTrue((ROOT / reference).exists(), f"{reference} does not exist")

    def test_the_artifact_folders_are_kept(self):
        for name in ("architecture", "critiques", "plans", "reviews", "handoff"):
            self.assertTrue((ROOT / "artifacts" / name / ".gitkeep").is_file(), name)


if __name__ == "__main__":
    unittest.main()
