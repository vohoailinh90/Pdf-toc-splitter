#!/usr/bin/env python3
"""Show or change which lane runs a requirement when `route.py` is not given `--lane`.

A lane is who executes a tier (`lanes` in agent-routing/policy.yaml): `claude`
(Claude subagents), `codex` (Codex Cloud through `@codex` GitHub comments) or
`codex-cli` (the Codex CLI on this machine, through scripts/codex_cli.py).
Which one you can use is a fact about this machine and its accounts, not
about a requirement, so the choice is saved locally in
`.claude/routing-lane.local` (gitignored) and never committed.

    python3 scripts/lane.py                  # the lane in effect and where it comes from
    python3 scripts/lane.py list             # every lane the policy defines
    python3 scripts/lane.py set codex-cli    # use the Codex CLI from now on
    python3 scripts/lane.py set codex        # back to Codex Cloud
    python3 scripts/lane.py reset            # back to the policy default (claude)

`route.py --lane X` still wins for one run. The saved lane never changes the
tier, an override or a reserve, never reaches `route.py --check` or the
evals, and does not apply to T0, which stays in the main session. A
requirement already recorded keeps its lane until you record it again:
`route.py ... --record --requirement <id>` (charges carry over).

The free-model team pipeline (scripts/team/, docs/vps-team.md) is not a lane
and is not switched here: it is a separate tool you start with `team run`.

Exit status: 0 done, 2 unknown lane or unreadable policy/file.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE_PATH = ROOT / "scripts" / "route.py"


def load_route():
    spec = importlib.util.spec_from_file_location("route", ROUTE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"lane: cannot load {ROUTE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe(policy: dict, name: str) -> str:
    spec = (policy.get("lanes") or {}).get(name)
    if not isinstance(spec, dict):
        return "Claude subagents (profiles in policy.yaml)"
    runner = {"cloud": "Codex Cloud via GitHub comments", "cli": "Codex CLI on this machine"}.get(
        spec.get("runner", "cloud"), str(spec.get("runner")))
    return f"{runner} — {spec.get('protocol')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("show", help="the lane in effect (default)")
    sub.add_parser("list", help="every lane the policy defines")
    setter = sub.add_parser("set", help="save a lane for this machine")
    setter.add_argument("name")
    sub.add_parser("reset", help="forget the saved lane; use the policy default")
    args = parser.parse_args(argv)

    route = load_route()
    try:
        policy = route.load_policy(route.POLICY_PATH)
        root = route.project_root()
        path = root / route.LANE_FILE
        names = route.lane_names(policy)
        default = route.default_lane(policy)

        if args.command == "list":
            for name in names:
                mark = " (policy default)" if name == default else ""
                print(f"{name}{mark}: {describe(policy, name)}")
            return 0
        if args.command == "set":
            if args.name not in names:
                print(f"lane: unknown lane {args.name!r}; policy defines {names}", file=sys.stderr)
                return 2
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.name + "\n", encoding="utf-8")
            print(f"saved lane {args.name} in {path}")
            return 0
        if args.command == "reset":
            path.unlink(missing_ok=True)
            print(f"no saved lane; route.py uses the policy default ({default})")
            return 0

        lane, source = route.operator_lane(policy, root)
        print(f"lane: {lane or default} (from {source})")
        print(f"  {describe(policy, lane or default)}")
        return 0
    except route.RoutingError as exc:
        print(f"lane: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
