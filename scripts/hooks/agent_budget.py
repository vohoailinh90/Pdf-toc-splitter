#!/usr/bin/env python3
"""Enforce `budget.max_agent_invocations` at the moment a subagent is spawned.

The routing policy calls its ceiling "not advisory", but until now nothing
counted. This script keeps a ledger for the requirement in progress and runs as
a `PreToolUse` hook on the `Agent` tool: every spawn is charged to the base
ceiling or to a reserve that was granted for that agent, and a spawn past the
ceiling is refused with the policy's own escalation guidance. A count is
computable, so a script counts — see *Deterministic work is not agent work*
in CLAUDE.md.

The ledger is written by `scripts/route.py --record` and lives beside the
session handoff, per checkout rather than per session: a restarted session
resumes the same requirement's budget, and a new requirement re-records.

Hook subcommands (read the hook payload on stdin, fail open):

    pre-tool-use    PreToolUse (matcher: Agent)   charge the spawn, or deny it
    session-start   SessionStart                  replay the ledger's state

Manual subcommands:

    status                    show the ledger
    grant RESERVE [--agent A] grant a runtime reserve (escalation_reserve after
                              the reviewer formally escalated)
    charge --agent A          count an invocation the hook could not see, such
                              as a resumed critique round
    reset                     remove the ledger

Re-recording a route for the same requirement (same `--requirement` label, or
no label on either side) carries the charges already made into the new
ledger, so the hook's own advice — re-classify when the requirement grew —
cannot reopen the budget. Only `route.py --record --fresh`, a different
label, or `reset` starts from zero.

Set CLAUDE_AGENT_BUDGET_UNROUTED=deny to refuse spawns when no ledger exists;
by default they are allowed, uncounted, with a nudge to classify first.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # POSIX advisory locks
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:  # Windows byte-range locks
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

LEDGER_VERSION = 1
# The subagent-spawning tool is named `Task` in some Claude Code releases and
# `Agent` in others. The settings matcher must list every name here, as an
# exact-name alternation (`Agent|Task`): Claude Code treats a `|`-joined list
# of plain names as exact matches, not as a regex, so `Task` cannot bleed onto
# `TaskCreate`. tests/test_agent_budget.py ties the matcher to this set.
SPAWN_TOOLS = {"Agent", "Task"}
UNROUTED_VAR = "CLAUDE_AGENT_BUDGET_UNROUTED"
LOCK_WAIT_SECONDS = 3.0
LOCK_POLL_SECONDS = 0.05


class LedgerError(RuntimeError):
    """The ledger cannot be read or the request cannot be honoured."""


class BudgetExceeded(LedgerError):
    """The requested invocation would pass the ceiling."""


def log(message: str) -> None:
    print(f"agent_budget: {message}", file=sys.stderr)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def repo_root() -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2]


def ledger_path(root: Path) -> Path:
    return root / "artifacts" / "handoff" / "agent-budget.json"


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def build_ledger(route: dict[str, Any], requirement: str = "") -> dict[str, Any]:
    """A fresh ledger from a `scripts/route.py` result."""
    budget = route.get("budget") or {}
    reserves: list[dict[str, Any]] = []
    for grant in budget.get("reserves_granted", []):
        for _ in range(int(grant.get("amount", 1))):
            reserves.append(
                {
                    "reserve": grant["reserve"],
                    "grantable_agents": list(grant.get("grantable_agents", [])),
                    "granted_by": "classification",
                    "reason": grant.get("grant_condition", ""),
                    "spent_by": None,
                }
            )
    return {
        "version": LEDGER_VERSION,
        "requirement": requirement or "",
        "recorded_at": now(),
        "tier": route["final_tier"],
        # Older ledgers have no lane; they were all recorded in the claude lane.
        "lane": route.get("lane") or "claude",
        # The lane's runbook, so a refusal can point at the process actually in use.
        "process": (route.get("codex") or {}).get("process"),
        "strategy": route.get("strategy"),
        "required_agents": list(route.get("required_agents", [])),
        "base": int(budget.get("base", route["max_agent_invocations"])),
        "reserves": reserves,
        "runtime_reserves": [
            {
                "reserve": item["reserve"],
                "amount": int(item.get("amount", 1)),
                "grantable_agents": list(item.get("grantable_agents", [])),
                "grant_condition": item.get("grant_condition", ""),
            }
            for item in budget.get("runtime_reserves", [])
        ],
        "invocations": [],
    }


def validate_ledger(ledger: Any) -> dict[str, Any]:
    if not isinstance(ledger, dict):
        raise LedgerError("ledger is not a mapping")
    if ledger.get("version") != LEDGER_VERSION:
        raise LedgerError(f"ledger version {ledger.get('version')!r} is not {LEDGER_VERSION}")
    base = ledger.get("base")
    if isinstance(base, bool) or not isinstance(base, int) or base < 0:
        raise LedgerError(f"ledger base {base!r} is not a non-negative integer")
    for key in ("reserves", "runtime_reserves", "invocations"):
        if not isinstance(ledger.get(key), list):
            raise LedgerError(f"ledger `{key}` is not a list")
    return ledger


def load_ledger(root: Path) -> dict[str, Any] | None:
    """The ledger on disk, None when there is none, LedgerError when it is unusable."""
    path = ledger_path(root)
    if not path.exists():
        return None
    try:
        return validate_ledger(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(f"cannot read {path}: {exc}") from exc


def write_ledger(root: Path, ledger: dict[str, Any]) -> Path:
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(ledger, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path


def same_requirement(previous: dict[str, Any], requirement: str) -> bool:
    """Two labels name the same requirement unless both are given and differ."""
    old = str(previous.get("requirement") or "").strip()
    new = str(requirement or "").strip()
    return not old or not new or old == new


def carry_over(ledger: dict[str, Any], previous: dict[str, Any]) -> int:
    """Re-charge the previous ledger's spending against a freshly recorded one.

    Runtime-granted reserves come across first (within what the new tier
    allows), then every invocation is charged again in its original order.
    A charge the new ceiling cannot hold stays on the record anyway — the
    point is that nothing already spent can be spent twice.
    """
    allowed = {item["reserve"]: item for item in ledger["runtime_reserves"]}
    for reserve in previous.get("reserves", []):
        if reserve.get("granted_by") != "runtime":
            continue  # classification-time reserves come from the new route
        runtime = allowed.get(reserve["reserve"])
        already = sum(1 for item in ledger["reserves"] if item["reserve"] == reserve["reserve"])
        if runtime is None or already >= runtime["amount"]:
            continue
        ledger["reserves"].append({**reserve, "spent_by": None, "carried": True})

    carried = 0
    for item in previous.get("invocations", []):
        agent = str(item.get("agent") or "unspecified")
        try:
            charge(ledger, agent, str(item.get("source") or "carried"), str(item.get("reason") or ""))
        except BudgetExceeded:
            ledger["invocations"].append({**item, "charged_to": "base", "note": "exceeds the re-recorded ceiling"})
        entry = ledger["invocations"][-1]
        entry["at"] = item.get("at", entry["at"])
        entry["carried"] = True
        carried += 1
    return carried


def record(route: dict[str, Any], requirement: str = "", root: Path | None = None, fresh: bool = False) -> Path:
    """Record a freshly resolved route, carrying over what the same requirement already spent.

    The whole load/carry/write runs under the ledger lock: a hook charging a
    spawn between the load and the write would otherwise be overwritten by
    this function's stale snapshot, reopening the slot it had just taken.
    """
    root = root or repo_root()
    with ledger_lock(root):
        ledger = build_ledger(route, requirement)
        if not fresh:
            try:
                previous = load_ledger(root)
            except LedgerError as exc:
                log(f"{exc}; not carrying anything over")
                previous = None
            if previous is not None and same_requirement(previous, requirement):
                carried = carry_over(ledger, previous)
                if not ledger["requirement"]:
                    ledger["requirement"] = previous.get("requirement") or ""
                if carried:
                    log(f"carried {carried} invocation(s) already charged to this requirement into the new ledger")
            elif previous is not None:
                log(f"previous ledger for {previous.get('requirement') or 'an unnamed requirement'!r} replaced")
        return write_ledger(root, ledger)


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def base_used(ledger: dict[str, Any]) -> int:
    return sum(1 for item in ledger["invocations"] if item.get("charged_to") == "base")


def ceiling(ledger: dict[str, Any]) -> int:
    return ledger["base"] + len(ledger["reserves"])


def unspent_reserve_for(ledger: dict[str, Any], agent: str) -> dict[str, Any] | None:
    for reserve in ledger["reserves"]:
        if reserve.get("spent_by") is None and agent in reserve.get("grantable_agents", []):
            return reserve
    return None


def charge(ledger: dict[str, Any], agent: str, source: str, reason: str = "") -> str:
    """Charge one invocation of `agent`; return what it was charged to.

    A reserve granted for this agent is spent first, so the order in which the
    roster is spawned cannot strand a slot: an analyst spawned before the critic
    takes its own reserve and leaves the base for the roles that have no other
    slot. Everything else, including exploration agents, is charged to the
    base — the policy counts every subagent invocation.
    """
    if len(ledger["invocations"]) >= ceiling(ledger):
        # Normally unreachable: a slot is either base room or an unspent reserve,
        # and both keep the total below the ceiling. It binds after a re-record
        # to a smaller route carried more charges than the new ceiling holds —
        # an overdrawn ledger must refuse everything, reserves included.
        raise BudgetExceeded(refusal(ledger, agent))
    reserve = unspent_reserve_for(ledger, agent)
    if reserve is not None:
        reserve["spent_by"] = agent
        charged_to = reserve["reserve"]
    elif base_used(ledger) < ledger["base"]:
        charged_to = "base"
    else:
        raise BudgetExceeded(refusal(ledger, agent))
    ledger["invocations"].append(
        {"agent": agent, "charged_to": charged_to, "source": source, "reason": reason, "at": now()}
    )
    return charged_to


def grant(ledger: dict[str, Any], reserve_name: str, agent: str | None, reason: str) -> dict[str, Any]:
    """Grant one unit of a runtime reserve, within the amount the policy allows."""
    runtime = next((item for item in ledger["runtime_reserves"] if item["reserve"] == reserve_name), None)
    if runtime is None:
        names = [item["reserve"] for item in ledger["runtime_reserves"]] or ["none"]
        raise LedgerError(
            f"{reserve_name} is not a runtime reserve at {ledger['tier']}; available: {', '.join(names)}"
        )
    already = sum(1 for item in ledger["reserves"] if item["reserve"] == reserve_name)
    if already >= runtime["amount"]:
        raise LedgerError(f"{reserve_name} is already fully granted ({already}/{runtime['amount']}) at {ledger['tier']}")
    grantable = list(runtime["grantable_agents"])
    if agent is not None and agent not in grantable:
        raise LedgerError(f"{reserve_name} may only pay for {grantable}, not {agent}")
    entry = {
        "reserve": reserve_name,
        "grantable_agents": [agent] if agent else grantable,
        "granted_by": "runtime",
        "reason": reason or runtime.get("grant_condition", ""),
        "granted_at": now(),
        "spent_by": None,
    }
    ledger["reserves"].append(entry)
    return entry


def reset(root: Path) -> bool:
    path = ledger_path(root)
    if not path.exists():
        return False
    path.unlink()
    return True


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def summary(ledger: dict[str, Any]) -> str:
    used = [item["agent"] for item in ledger["invocations"]]
    lane = ledger.get("lane") or "claude"
    where = ledger["tier"] if lane == "claude" else f"{ledger['tier']} {lane} lane"
    parts = [
        f"{where} ({ledger.get('strategy') or 'strategy unrecorded'})",
        f"base {base_used(ledger)}/{ledger['base']} used",
    ]
    if ledger["reserves"]:
        spent = sum(1 for item in ledger["reserves"] if item.get("spent_by") is not None)
        names = sorted({item["reserve"] for item in ledger["reserves"]})
        parts.append(f"reserves {spent}/{len(ledger['reserves'])} spent ({', '.join(names)})")
    parts.append(f"ceiling {ceiling(ledger)}")
    parts.append("invocations: " + (", ".join(used) if used else "none"))
    label = ledger.get("requirement") or "unnamed requirement"
    return f"Agent budget for {label}: " + " · ".join(parts) + "."


def refusal(ledger: dict[str, Any], agent: str) -> str:
    used = [item["agent"] for item in ledger["invocations"]]
    lines = [
        f"Agent budget exhausted: {ledger['tier']} ceiling is {ceiling(ledger)} "
        f"(base {ledger['base']} + {len(ledger['reserves'])} reserve), all charged"
        + (f" to: {', '.join(used)}" if used else "")
        + f". Spawning `{agent}` would exceed budget.max_agent_invocations in agent-routing/policy.yaml.",
    ]
    lane = ledger.get("lane") or "claude"
    if ledger["base"] == 0 and lane == "claude":
        lines.append("This tier is main-session only. Do the work here.")
    elif ledger["base"] == 0:
        how = "through GitHub comments" if lane == "codex" else "through scripts/codex_cli.py"
        lines.append(
            f"The {lane} lane has no Claude roster at {ledger['tier']}: Codex designs, implements and reviews "
            f"{how}, and the main session orchestrates and validates "
            f"({ledger.get('process') or 'docs/codex-lane.md'})."
        )
    if len(ledger["invocations"]) > ceiling(ledger):
        lines.append(
            f"The ledger is overdrawn: {len(ledger['invocations'])} invocations were carried into a "
            f"route whose ceiling is {ceiling(ledger)}, so nothing further may spawn."
        )
    lines.append(
        "Reaching the ceiling with work outstanding is an escalation signal: report the partial "
        "result and the remaining risk rather than spawning past the cap."
    )
    for runtime in ledger["runtime_reserves"]:
        granted = sum(1 for item in ledger["reserves"] if item["reserve"] == runtime["reserve"])
        if granted < runtime["amount"] and agent in runtime["grantable_agents"]:
            lines.append(
                f"If {runtime['grant_condition'].replace('_', ' ')}, grant the reserve first: "
                f"`python3 scripts/hooks/agent_budget.py grant {runtime['reserve']} --agent {agent}`."
            )
    lines.append(
        "If the requirement itself revealed a higher tier, re-classify and re-record it with the same "
        "--requirement label: `python3 scripts/route.py ... --record --requirement <label>` — the charges "
        "above carry over into the new ceiling."
    )
    return " ".join(lines)


UNROUTED_NUDGE = (
    "No agent budget is recorded for this checkout, so this subagent invocation is not counted "
    "against any ceiling. For a non-trivial requirement, classify it first and record the result: "
    "`python3 scripts/route.py --dimensions ... --signals ... --record --requirement <id>`."
)


# ---------------------------------------------------------------------------
# Locking — two parallel Agent calls fire two hooks at once
# ---------------------------------------------------------------------------


CONTENTION_ERRNOS = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES}


def _try_lock(fd: int) -> None:
    """Take the OS lock on `fd` without blocking; BlockingIOError when another process holds it.

    Python maps EAGAIN/EWOULDBLOCK to BlockingIOError by itself, but some POSIX
    implementations report a contended non-blocking flock as EACCES, which
    arrives as PermissionError. That is contention too, not a broken lock, so
    it must poll rather than fall through to the fail-open path.
    """
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in CONTENTION_ERRNOS:
                raise BlockingIOError(exc.errno, str(exc)) from exc
            raise
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc
        return
    raise OSError("no file locking available on this platform")


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class ledger_lock:
    """An OS advisory lock on a file beside the ledger.

    The kernel releases it when the holder exits, so a hook that dies mid-write
    leaves nothing to reclaim — which is the point: a reclaim-by-age scheme has
    an ABA race where two waiters both judge the same lock stale and the second
    deletes the lock the first has just re-created. The lock file itself is
    never unlinked, for the same reason: unlinking a locked file lets the next
    opener lock a different inode under the same name.

    On a platform with no locking, or after LOCK_WAIT_SECONDS, the hook proceeds
    without the lock and says so: a guardrail must not wedge the session.
    """

    def __init__(self, root: Path) -> None:
        self.path = ledger_path(root).with_name("agent-budget.lock")
        self.fd: int | None = None
        self.held = False

    def __enter__(self) -> "ledger_lock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        except OSError as exc:
            log(f"lock file unavailable ({exc}); proceeding without it")
            return self
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                _try_lock(self.fd)
                self.held = True
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    log("could not take the ledger lock; proceeding without it")
                    self._close()
                    return self
                time.sleep(LOCK_POLL_SECONDS)
            except OSError as exc:
                log(f"lock unavailable ({exc}); proceeding without it")
                self._close()
                return self

    def __exit__(self, *_: Any) -> None:
        if self.fd is not None and self.held:
            try:
                _unlock(self.fd)
            except OSError:
                pass
        self._close()

    def _close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None
        self.held = False


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------


def hook_output(decision: str | None, message: str) -> str:
    output: dict[str, Any] = {"hookEventName": "PreToolUse"}
    if decision:
        output["permissionDecision"] = decision
        output["permissionDecisionReason"] = message
    else:
        output["additionalContext"] = message
    return json.dumps({"hookSpecificOutput": output})


def decide(root: Path, tool_name: str, tool_input: Any) -> tuple[str | None, str] | None:
    """(decision, message) for a spawn, or None when the tool is not a spawn."""
    if tool_name not in SPAWN_TOOLS:
        return None
    agent = "unspecified"
    if isinstance(tool_input, dict):
        raw = tool_input.get("subagent_type")
        if isinstance(raw, str) and raw.strip():
            agent = raw.strip()

    with ledger_lock(root):
        try:
            ledger = load_ledger(root)
        except LedgerError as exc:
            log(f"{exc}; treating the checkout as unrouted")
            ledger = None
        if ledger is None:
            if os.environ.get(UNROUTED_VAR, "").strip().lower() == "deny":
                return "deny", UNROUTED_NUDGE + " (Refused: CLAUDE_AGENT_BUDGET_UNROUTED=deny.)"
            return None, UNROUTED_NUDGE
        try:
            charged_to = charge(ledger, agent, source="hook")
        except BudgetExceeded as exc:
            return "deny", str(exc)
        write_ledger(root, ledger)
    return None, f"Charged `{agent}` to {charged_to}. {summary(ledger)}"


def cmd_pre_tool_use(payload: dict[str, Any]) -> int:
    outcome = decide(repo_root(), str(payload.get("tool_name") or ""), payload.get("tool_input"))
    if outcome is None:
        return 0
    decision, message = outcome
    print(hook_output(decision, message))
    return 0


def cmd_session_start(payload: dict[str, Any]) -> int:
    ledger = load_ledger(repo_root())
    if ledger is None:
        return 0
    print(
        summary(ledger)
        + " This ledger is enforced by the PreToolUse hook. If you are starting a different "
        "requirement, re-classify it and record the new budget with `scripts/route.py --record`."
    )
    return 0


HOOK_HANDLERS = {
    "pre-tool-use": cmd_pre_tool_use,
    "session-start": cmd_session_start,
}


# ---------------------------------------------------------------------------
# Manual subcommands
# ---------------------------------------------------------------------------


def require_ledger(root: Path) -> dict[str, Any]:
    ledger = load_ledger(root)
    if ledger is None:
        raise LedgerError(f"no ledger at {ledger_path(root)}; record one with `scripts/route.py --record`")
    return ledger


def cmd_status(args: argparse.Namespace, root: Path) -> int:
    ledger = load_ledger(root)
    if ledger is None:
        print("no agent budget recorded")
        return 1
    print(json.dumps(ledger, indent=2) if args.json else summary(ledger))
    return 0


def cmd_grant(args: argparse.Namespace, root: Path) -> int:
    with ledger_lock(root):
        ledger = require_ledger(root)
        entry = grant(ledger, args.reserve, args.agent, args.reason)
        write_ledger(root, ledger)
    print(f"granted {entry['reserve']} for {', '.join(entry['grantable_agents'])}. {summary(ledger)}")
    return 0


def cmd_charge(args: argparse.Namespace, root: Path) -> int:
    with ledger_lock(root):
        ledger = require_ledger(root)
        charged_to = charge(ledger, args.agent, source="manual", reason=args.reason)
        write_ledger(root, ledger)
    print(f"charged {args.agent} to {charged_to}. {summary(ledger)}")
    return 0


def cmd_reset(args: argparse.Namespace, root: Path) -> int:
    print("ledger removed" if reset(root) else "no ledger to remove")
    return 0


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show the ledger")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    granting = sub.add_parser("grant", help="grant a runtime reserve")
    granting.add_argument("reserve")
    granting.add_argument("--agent", help="restrict the granted slot to this agent")
    granting.add_argument("--reason", default="", help="the finding that earned it")
    granting.set_defaults(func=cmd_grant)

    charging = sub.add_parser("charge", help="count an invocation the hook could not see")
    charging.add_argument("--agent", required=True)
    charging.add_argument("--reason", default="")
    charging.set_defaults(func=cmd_charge)

    resetting = sub.add_parser("reset", help="remove the ledger")
    resetting.set_defaults(func=cmd_reset)
    return parser


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] in HOOK_HANDLERS:
        payload = read_payload()
        try:
            return HOOK_HANDLERS[argv[1]](payload)
        except Exception as exc:  # fail open: never break the session being protected
            log(f"{argv[1]} failed, continuing: {exc!r}")
            return 0
    parser = build_cli()
    args = parser.parse_args(argv[1:])
    try:
        return args.func(args, repo_root())
    except LedgerError as exc:
        log(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
