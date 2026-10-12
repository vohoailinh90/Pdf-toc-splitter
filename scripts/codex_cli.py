#!/usr/bin/env python3
"""Run one step of the codex-cli lane: the Codex CLI on this machine, never Codex Cloud.

The codex-cli lane (`lanes.codex-cli` in agent-routing/policy.yaml,
docs/codex-cli-lane.md) runs the same tiers as the codex lane, but the main
session starts Codex with `codex exec` instead of posting `@codex` on GitHub.
This script is the only approved way to do that. Everything about a run that
is computable is decided here rather than by a model -- see *Deterministic
work is not agent work* in CLAUDE.md:

  - the sandbox comes from the mode: read-only for BRAINSTORM and reviews,
    workspace-write with the network off for IMPLEMENT and FIX, never
    danger-full-access and never the approval bypass; a write run's only
    extra writable root is its own scratch directory (no /tmp, no $TMPDIR,
    no configured writable_roots), and no Codex config profile is passed;
  - all state lives under the repository's top level, which
    CLAUDE_PROJECT_DIR must name exactly; no directory this script creates
    or removes may be reached through a symlink, and no report or log is
    written over an existing file, so nothing lands outside its root;
  - Codex works in its own git worktree under `.codex-worktrees/`, never in
    the main session's working tree; a read-only step gets a detached
    worktree at the exact SHA it reads, removed afterwards;
  - Codex never commits or pushes: it leaves its changes uncommitted in the
    worktree, and the main session validates, then commits;
  - every run is charged to the requirement `route.py --lane codex-cli
    --record --requirement <id>` recorded, never to a label the caller
    passes, so renaming a task cannot reset a count;
  - a local ledger (`artifacts/handoff/codex-cli-ledger.json`) keeps the
    round caps of the policy per requirement and mode, refuses a duplicate,
    a skipped round, a write mode twice at one SHA, a review of a head that
    was already reviewed, a review past its cap (the policy sets none on
    reviews, so only the same-head rule bounds them), and a third attempt
    after two failures (BLOCKED), and a lock keeps one Codex run at a time;
  - after a run it checks what can be checked: a read-only run changed
    nothing; a write run stayed on its branch, on top of its SHA.

    python3 scripts/codex_cli.py doctor
    python3 scripts/codex_cli.py run contract.yaml [--model M] [--dry-run]
    python3 scripts/codex_cli.py review --branch feat/req-7 [--base main] [--security]
    python3 scripts/codex_cli.py status [--requirement REQ-7]

The contract is the codex lane's (task_id, mode, repository, base_branch,
target_branch, target_sha, goal, scope, out_of_scope, write_policy,
constraints, acceptance_criteria; optional round, required_output, lead,
notes), validated by scripts/codex_task.py's own rules. `CODEX_BIN` names the
Codex executable when it is not `codex` on PATH.

Exit status: 0 the run finished and passed the checks (or doctor found
nothing wrong), 1 refused, failed or violated -- read the message, 2 the
input, the repository or the policy cannot be used.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    print("PyYAML is required. Install it with: python -m pip install -r requirements-eval.txt", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "agent-routing" / "policy.yaml"
TASK_PATH = ROOT / "scripts" / "codex_task.py"
LANE = "codex-cli"
LEDGER_VERSION = 1
WORKTREES = ".codex-worktrees"
WRITE_MODES = ("IMPLEMENT", "FIX")
REVIEW_COMMANDS = ("review", "security review")
# One failed attempt may be retried once (docs/claude-to-codex.md section 17's
# single retry); the third attempt is BLOCKED and goes to the operator.
MAX_ATTEMPTS = 2
DEFAULT_TIMEOUT = 3600
# A requirement label becomes a directory name, so it is held to task_id's
# character set: no separator, no traversal, nothing a path could escape with.
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# Git as this script runs it: no hooks, no fsmonitor, so nothing Codex leaves
# in a worktree runs on the host when the main session inspects it.
GIT = ("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")

CLI_LINES = {
    "read-only": (
        "You run in a read-only sandbox, in a detached checkout of the target_sha above.",
        "Do not modify files.",
    ),
    "workspace-write": (
        "You run in a sandbox with the network off, in a git worktree checked out on target_branch.",
        "Leave your changes uncommitted: do not commit, push, rebase, reset or switch branches. "
        "The orchestrator validates your changes and commits them.",
    ),
}
GENERAL_REVIEW = (
    "Code review. You run in a read-only sandbox, in a detached checkout of {head}.",
    "Review only the changes `git diff {base}...HEAD` introduces: correctness bugs, regressions, missing or "
    "vacuous tests, unhandled errors, security issues, and changes outside what the commits claim to do.",
    "Report each finding with its file and line, severity (critical, high, medium, low), evidence and a "
    "failure scenario. Report only findings you can point to in the diff. If there are none, say so.",
    "Do not modify files.",
)
SECURITY_REVIEW = (
    "Security review. You run in a read-only sandbox, in a detached checkout of {head}.",
    "Review only the changes `git diff {base}...HEAD` introduces, for security defects: injection, "
    "authentication and authorization gaps, secret or PII exposure, unsafe deserialization, path "
    "traversal, unsafe defaults, missing input validation.",
    "Report each finding with its file and line, severity (critical, high, medium, low), evidence and a "
    "failure scenario. Report only findings you can point to in the diff. If there are none, say so.",
    "Do not modify files.",
)


class InputError(ValueError):
    """The input, the repository or the policy cannot be used (exit 2)."""


class Refused(RuntimeError):
    """The run must not start, or it finished and failed its checks (exit 1)."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_root() -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env_root) if env_root else ROOT


def load_task_module():
    """scripts/codex_task.py, for its contract rules: one contract format in both Codex lanes."""
    spec = importlib.util.spec_from_file_location("codex_task", TASK_PATH)
    if spec is None or spec.loader is None:
        raise InputError(f"cannot load {TASK_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recorded_requirement(root: Path) -> str:
    """The requirement route.py recorded for this lane: what every run is charged to."""
    path = root / "artifacts" / "handoff" / "agent-budget.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"no requirement is recorded ({path} is missing); run "
                         "`scripts/route.py ... --lane codex-cli --record --requirement <id>` first") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    if not isinstance(ledger, dict) or ledger.get("lane") != LANE:
        raise InputError(f"the recorded requirement is in the {ledger.get('lane') if isinstance(ledger, dict) else '?'} "
                         f"lane, not {LANE}; re-record it with `--lane {LANE} --record`")
    label = ledger.get("requirement") or ""
    if not LABEL.fullmatch(label):
        raise InputError(f"recorded requirement label {label!r} must be 1-64 letters, digits, '.', '_' or '-' "
                         "(it names report directories); re-record it under such a label")
    return label


def lane_spec(policy_path: Path = POLICY_PATH) -> dict[str, Any]:
    try:
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        spec = policy["lanes"][LANE]
        caps = spec["round_caps"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise InputError(f"cannot read lanes.{LANE} from {policy_path}: {exc}") from exc
    for mode in ("BRAINSTORM", "IMPLEMENT", "FIX", "review"):
        cap = caps.get(mode)
        if mode == "review" and mode in caps and cap is None:
            continue  # an explicit `review: null` is no limit (section 16); an omitted key is a malformed policy
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise InputError(f"lanes.{LANE}.round_caps.{mode} must be a positive integer, got {cap!r}")
    return spec


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run([*GIT, *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if check and done.returncode != 0:
        raise InputError(f"git {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip() if done.returncode == 0 else ""


def repo_root(root: Path) -> Path:
    top = git(root, "rev-parse", "--show-toplevel", check=False)
    if not top:
        raise InputError(f"{root} is not inside a git repository")
    return Path(top)


def branch_head(repo: Path, branch: str) -> str:
    return git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False)


def worktrees(repo: Path) -> dict[str, Path]:
    """Checked-out branch -> worktree path, for every worktree of the repository."""
    found: dict[str, Path] = {}
    path: Path | None = None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line.startswith("branch refs/heads/") and path is not None:
            found[line[len("branch refs/heads/"):]] = path
    return found


def no_symlink(root: Path, path: Path) -> Path:
    """`path`, refused if any directory between `root` and it is a symlink.

    The roots are gitignored, so a repository can ship one as a symlink; a
    lexical join would then follow it and create or remove things elsewhere.
    """
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise InputError(f"{current} is a symlink; this script never creates or removes anything through one "
                             "-- replace it with a real directory")
    return path


def worktree_path(repo: Path, name: str) -> Path:
    return no_symlink(repo, repo / WORKTREES / name.replace("/", "__"))


def dirty(path: Path) -> list[str]:
    return [line for line in git(path, "status", "--porcelain", "--untracked-files=all").splitlines() if line]


# ---------------------------------------------------------------------------
# Ledger and lock
# ---------------------------------------------------------------------------


def state_root() -> Path:
    """The repository's top level, where the ledger, the lock and the reports live.

    CLAUDE_PROJECT_DIR must name it exactly: two subdirectories of one
    repository would otherwise keep two ledgers and two locks for the same
    worktrees. Its state directory is refused when reached through a symlink.
    """
    root = project_root()
    top = repo_root(root)
    if root.resolve() != top.resolve():
        raise InputError(f"the project directory {root} is not the repository's top level {top}; run from the top "
                         "level (or set CLAUDE_PROJECT_DIR to it) so one repository keeps one ledger and one lock")
    no_symlink(top, top / "artifacts" / "handoff")
    return top


def fresh(path: Path) -> Path:
    """`path`, refused when anything (a file, a symlink) is already there."""
    if path.exists() or path.is_symlink():
        raise Refused(f"{path} already exists; this script never writes over an existing report or log -- move it")
    return path


def write_new(path: Path, text: str) -> None:
    """Create `path` exclusively: O_EXCL never follows or reuses whatever appeared there."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def ledger_path(root: Path) -> Path:
    return root / "artifacts" / "handoff" / "codex-cli-ledger.json"


def load_ledger(root: Path) -> dict[str, Any]:
    path = ledger_path(root)
    if not path.exists():
        return {"version": LEDGER_VERSION, "runs": []}
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}; fix or move it -- it is what enforces the caps") from exc
    if not isinstance(ledger, dict) or ledger.get("version") != LEDGER_VERSION or not isinstance(ledger.get("runs"), list):
        raise InputError(f"{path} is not a version-{LEDGER_VERSION} codex-cli ledger")
    return ledger


def save_ledger(root: Path, ledger: dict[str, Any]) -> None:
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) terminates the process on Windows; assume it is alive.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:
    """One Codex run at a time for this project.

    The lock names this process and, once started, its Codex child: a lock is
    taken over only when every process it names is gone, so killing this
    script while Codex survives still keeps the next run out.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "artifacts" / "handoff" / "codex-cli.lock"

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The pid is written to a private file first and linked into place in
        # one atomic step, so the lock is never seen without its holder.
        mine = self.path.with_name(f"{self.path.name}.{os.getpid()}")
        mine.write_text(str(os.getpid()), encoding="utf-8")
        try:
            for _ in range(2):
                try:
                    os.link(mine, self.path)
                    return self
                except FileExistsError:
                    pass
                try:
                    holders = [int(word) for word in self.path.read_text(encoding="utf-8").split()]
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    holders = []
                if not holders:
                    raise Refused(f"{self.path} exists but names no process; if no Codex CLI run is in progress, "
                                  "delete it")
                alive = [pid for pid in holders if pid_alive(pid)]
                if alive:
                    raise Refused(f"another Codex CLI run is in progress (pid {' '.join(map(str, alive))}); one run "
                                  f"at a time -- if those processes are gone, delete {self.path}")
                self.path.unlink(missing_ok=True)
            raise Refused(f"cannot take {self.path}")
        finally:
            mine.unlink(missing_ok=True)

    def add(self, pid: int) -> None:
        """Name the Codex child in the lock as well, replacing the file in one step."""
        staged = self.path.with_name(f"{self.path.name}.{os.getpid()}.child")
        staged.write_text(f"{os.getpid()} {pid}", encoding="utf-8")
        os.replace(staged, self.path)

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def refuse_task(ledger: dict[str, Any], contract: dict[str, Any], requirement: str, cap: int) -> int:
    """The attempt number this round may run as, or Refused naming the rule it breaks.

    Rounds are counted per requirement and mode, never per task_id: the caller
    names the task, so a count keyed on it would reset with every new name.
    """
    mode, rnd, sha = contract["mode"], contract["round"], contract["target_sha"]
    tasks = [run for run in ledger["runs"] if run.get("kind") == "task"]
    mine = [run for run in tasks if run.get("requirement") == requirement and run["mode"] == mode]
    done = [run for run in mine if run["status"] == "done"]
    if len(done) >= cap:
        raise Refused(f"{requirement} has used {len(done)} of {cap} {mode} round(s); resolve what is left in the "
                      "main session or escalate")
    if rnd != len(done) + 1:
        raise Refused(f"{requirement} has finished {len(done)} {mode} round(s), so the next one is round "
                      f"{len(done) + 1}, not {rnd}; a round never runs twice and is never skipped")
    this_round = [run for run in mine if run["round"] == rnd]
    if any(run["status"] == "running" for run in this_round):
        raise Refused(f"{requirement} {mode} round {rnd} is recorded as still running; if no Codex process is left, "
                      "mark it failed in the ledger by hand and say so")
    failed = [run for run in this_round if run["status"] in ("failed", "violated")]
    if any(run["status"] == "violated" for run in failed):
        raise Refused(f"BLOCKED: {requirement} {mode} round {rnd} broke its sandbox checks; report it, never retry")
    if len(failed) >= MAX_ATTEMPTS:
        raise Refused(f"BLOCKED: {requirement} {mode} round {rnd} failed {len(failed)} times; report it to the "
                      "operator, or re-record the requirement in another lane")
    if mode == "BRAINSTORM" and done and done[-1]["target_sha"] != sha:
        raise Refused(f"a read-only correction round reads the SHA its first round read "
                      f"({done[-1]['target_sha']}), not {sha}")
    if mode in WRITE_MODES:
        same_sha = [run for run in tasks if run["mode"] in WRITE_MODES and run["target_sha"] == sha
                    and run["status"] == "done"]
        if same_sha:
            other = same_sha[-1]
            raise Refused(f"{other['task_id']} {other['mode']} already wrote on top of {sha}; commit the validated "
                          "result and give the next write its new SHA")
    return len(failed) + 1


def refuse_review(ledger: dict[str, Any], requirement: str, command: str, head: str, cap: int | None) -> int:
    reviews = [run for run in ledger["runs"] if run.get("kind") == "review" and run["requirement"] == requirement]
    done = [run for run in reviews if run["status"] == "done"]
    if cap is not None and len(done) >= cap:  # cap is None when the policy sets no limit on reviews
        raise Refused(f"{requirement} has used {len(done)} of {cap} review rounds; resolve what is left in the "
                      "main session or escalate")
    if any(run.get("kind") == "review" and run["command"] == command and run["head_sha"] == head
           and run["status"] == "done" for run in ledger["runs"]):
        raise Refused(f"a {command} of {head} already ran; push a change before asking again")
    # Attempts belong to the head, not to whichever requirement was recorded.
    same = [run for run in ledger["runs"] if run.get("kind") == "review" and run["command"] == command
            and run["head_sha"] == head]
    if any(run["status"] == "running" for run in same):
        raise Refused(f"a {command} of {head} is recorded as still running; if no Codex process is left, "
                      "mark it failed in the ledger by hand and say so")
    failed = [run for run in same if run["status"] in ("failed", "violated")]
    if any(run["status"] == "violated" for run in failed):
        raise Refused(f"BLOCKED: a {command} of {head} broke its sandbox checks; report it, never retry")
    if len(failed) >= MAX_ATTEMPTS:
        raise Refused(f"BLOCKED: the {command} of {head} failed {len(failed)} times; report it to the operator")
    return len(failed) + 1


# ---------------------------------------------------------------------------
# Prompts and commands
# ---------------------------------------------------------------------------


def task_prompt(contract: dict[str, Any], sandbox: str) -> str:
    keys = ("task_id", "mode", "round", "repository", "base_branch", "target_branch", "target_sha", "goal",
            "scope", "out_of_scope", "write_policy", "constraints", "acceptance_criteria", "required_output")
    body = yaml.safe_dump({key: contract[key] for key in keys}, sort_keys=False, allow_unicode=True, width=4096)
    parts = [f"MODE: {contract['mode']}", contract["lead"], *CLI_LINES[sandbox], "Task contract:", body.rstrip()]
    if contract.get("notes"):
        parts.append(contract["notes"])
    parts.append("End with a final message that has one section for each item of required_output.")
    return "\n\n".join(parts) + "\n"


def codex_bin(required: bool = True) -> str:
    name = os.environ.get("CODEX_BIN") or "codex"
    found = shutil.which(name)
    if not found and not required:
        return name
    if not found:
        raise InputError(f"Codex CLI not found ({name!r}); install it (npm i -g @openai/codex), run `codex login`, "
                         "or set CODEX_BIN")
    return found


def exec_command(binary: str, sandbox: str, workdir: Path, output: Path, model: str | None,
                 scratch: Path | None = None) -> list[str]:
    command = [binary, "exec", "--sandbox", sandbox, "--cd", str(workdir), "--output-last-message", str(output)]
    if sandbox == "workspace-write":
        if scratch is None:
            raise InputError("a write run needs its scratch directory")
        # Always off: a write run that could reach the network could push or
        # exfiltrate. And workspace-write would otherwise also open /tmp,
        # $TMPDIR and any configured writable_roots; the only extra root is
        # the run's own scratch directory, which TMPDIR points at.
        command += [
            "--config", "sandbox_workspace_write.network_access=false",
            "--config", f"sandbox_workspace_write.writable_roots=[{json.dumps(str(scratch))}]",
            "--config", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "--config", "sandbox_workspace_write.exclude_slash_tmp=true",
        ]
    if model:
        command += ["--model", model]
    return command + ["-"]


def run_codex(command: list[str], prompt: str, cwd: Path, log: Path, timeout: int,
              lock: Lock | None = None, scratch: Path | None = None) -> int:
    # The prompt goes in on stdin, and only after the lock names the child:
    # `codex exec -` does nothing until stdin closes, and exits on an empty
    # one ("No prompt provided via stdin."), so a wrapper killed between
    # Popen and lock.add leaves a child that reads EOF and stops unworked.
    if not prompt:
        raise InputError("every Codex run is started with a non-empty prompt on stdin")
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if scratch is not None:
        env.update({"TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch)})
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        if lock is not None:
            lock.add(process.pid)
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            write_new(log, f"timed out after {timeout}s\n{stdout or ''}\n{stderr or ''}")
            return 124
    except BaseException:
        # Never leave a Codex process running once this script stops watching it.
        process.kill()
        process.wait()
        raise
    write_new(log, f"$ {' '.join(command)}\nexit {process.returncode}\n--- stdout\n{stdout}\n--- stderr\n"
                   f"{stderr}")
    return process.returncode


def output_dir(root: Path, label: str) -> Path:
    return no_symlink(root, root / "artifacts" / "handoff" / "codex-cli" / label)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def prepare_task(repo: Path, contract: dict[str, Any], dry_run: bool, attempt: int = 1) -> tuple[Path, bool]:
    """The worktree Codex works in, and whether it is temporary (removed after the run)."""
    mode, sha, branch = contract["mode"], contract["target_sha"], contract["target_branch"]
    if not git(repo, "cat-file", "-t", f"{sha}^{{commit}}", check=False):
        raise InputError(f"target_sha {sha} is not a commit in {repo}; fetch it first")
    if mode == "BRAINSTORM":
        path = worktree_path(repo, f"read-{contract['task_id']}-r{contract['round']}")
        if path.exists():
            raise Refused(f"{path} already exists; remove it (git worktree remove --force) before running")
        if not dry_run:
            git(repo, "worktree", "add", "--detach", str(path), sha)
        return path, True
    path = worktree_path(repo, branch)
    head = branch_head(repo, branch)
    if mode == "IMPLEMENT" and head and attempt > 1:
        # The retry of a failed attempt: same branch, still at target_sha, and
        # whatever the failed run left behind discarded by the operator first.
        if head != sha or worktrees(repo).get(branch, Path()).resolve() != path.resolve():
            raise Refused(f"retrying {contract['task_id']} needs {branch} at {sha}, checked out at {path}")
        if dirty(path):
            raise Refused(f"{path} still holds the failed attempt's changes; inspect, then discard them "
                          f"(git -C {path} reset --hard && git -C {path} clean -fd) before the retry")
        return path, False
    if mode == "IMPLEMENT":
        if head:
            raise Refused(f"branch {branch} already exists; IMPLEMENT starts a new branch -- corrections are FIX rounds")
        if path.exists():
            raise Refused(f"{path} already exists; remove it before running")
        if not dry_run:
            git(repo, "worktree", "add", "-b", branch, str(path), sha)
        return path, False
    if not head:
        raise Refused(f"branch {branch} does not exist; FIX corrects an implementation branch")
    if head != sha:
        raise Refused(f"STALE: {branch} is at {head}, not target_sha {sha}; re-read the branch and re-validate")
    checked_out = worktrees(repo).get(branch)
    if checked_out is not None:
        if checked_out.resolve() != path.resolve():
            raise Refused(f"{branch} is checked out at {checked_out}; Codex only writes in {path} -- switch that "
                          "worktree to another branch first")
        pending = dirty(path)
        if pending:
            raise Refused(f"{path} has uncommitted changes ({len(pending)} paths); commit or discard the validated "
                          "result before a FIX round")
    elif not dry_run:
        git(repo, "worktree", "add", str(path), branch)
    return path, False


def check_after(path: Path, contract: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    """status, problems, facts -- what a finished run can be checked for without reading its code."""
    if contract["mode"] == "BRAINSTORM":
        problems = []
        changed = dirty(path)
        if changed:
            problems.append(f"the read-only run left {len(changed)} changed path(s): {changed[:5]}")
        if git(path, "rev-parse", "HEAD") != contract["target_sha"]:
            problems.append("the read-only run moved HEAD")
        return ("violated" if problems else "done"), problems, {}
    sha, branch = contract["target_sha"], contract["target_branch"]
    problems = []
    current = git(path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if current != branch:
        problems.append(f"the worktree is on {current or 'a detached HEAD'}, not {branch}")
    elif subprocess.run([*GIT, "merge-base", "--is-ancestor", sha, "HEAD"], cwd=path).returncode != 0:
        problems.append(f"{branch} no longer contains target_sha {sha}: history was rewritten")
    commits = git(path, "log", "--oneline", f"{sha}..HEAD", check=False).splitlines()
    facts = {"changed_paths": dirty(path), "commits": commits,
             "diffstat": git(path, "diff", "--stat", sha, check=False).splitlines()[-1:] or []}
    if commits:
        facts["warnings"] = [f"Codex committed {len(commits)} commit(s) although told not to; validate them too"]
    if not problems and not commits and not facts["changed_paths"]:
        # Not "done": that would spend IMPLEMENT's only round on nothing.
        return "failed", ["Codex finished without changing anything; read its report"], facts
    return ("violated" if problems else "done"), problems, facts


def cmd_run(args: argparse.Namespace) -> int:
    root = state_root()
    spec = lane_spec()
    task = load_task_module()
    caps = {mode: spec["round_caps"][mode] for mode in task.MODES}
    try:
        document = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read {args.contract}: {exc}") from exc
    try:
        contract = task.validate(document, caps)
    except task.ContractError as exc:
        raise InputError(str(exc)) from exc
    except task.Refused as exc:
        raise Refused(f"round {document.get('round')} is past lanes.{LANE}.round_caps; do not run it") from exc
    sandbox = "workspace-write" if contract["mode"] in WRITE_MODES else "read-only"
    requirement = recorded_requirement(root)
    repo = root
    label = f"{contract['task_id']}/{contract['mode']}-r{contract['round']}"

    with Lock(root) as lock:
        ledger = load_ledger(root)
        attempt = refuse_task(ledger, contract, requirement, spec["round_caps"][contract["mode"]])
        out = output_dir(root, requirement) / f"{contract['task_id']}-{contract['mode']}-r{contract['round']}-a{attempt}.md"
        fresh(out)
        fresh(out.with_suffix(".log"))
        prompt = task_prompt(contract, sandbox)
        binary = codex_bin(required=not args.dry_run)
        if args.dry_run:
            path, _ = prepare_task(repo, contract, dry_run=True, attempt=attempt)
            scratch = path.with_name(path.name + ".tmp") if sandbox == "workspace-write" else None
            print("$ " + " ".join(exec_command(binary, sandbox, path, out, args.model, scratch)))
            print(prompt)
            return 0
        path, temporary = prepare_task(repo, contract, dry_run=False, attempt=attempt)
        scratch = None
        if sandbox == "workspace-write":
            scratch = no_symlink(repo, path.with_name(path.name + ".tmp"))
            shutil.rmtree(scratch, ignore_errors=True)
            scratch.mkdir()
        command = exec_command(binary, sandbox, path, out, args.model, scratch)
        entry = {"kind": "task", "requirement": requirement, "task_id": contract["task_id"], "mode": contract["mode"],
                 "round": contract["round"], "attempt": attempt, "target_sha": contract["target_sha"],
                 "branch": contract["target_branch"],
                 "sandbox": sandbox, "workdir": str(path), "output": str(out), "status": "running",
                 "started_at": now(), "command": command}
        ledger["runs"].append(entry)
        save_ledger(root, ledger)
        try:
            code = run_codex(command, prompt, path, out.with_suffix(".log"), args.timeout, lock, scratch)
            if code != 0:
                status, problems, facts = "failed", [f"codex exited {code}; see {out.with_suffix('.log')}"], {}
            else:
                status, problems, facts = check_after(path, contract)
        except BaseException as exc:
            # Ctrl-C or a git error: never leave the ledger saying "running".
            entry.update({"status": "failed", "finished_at": now(), "problems": [f"interrupted: {exc!r}"]})
            save_ledger(root, ledger)
            raise
        finally:
            if temporary:
                git(repo, "worktree", "remove", "--force", str(path), check=False)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
        entry.update({"status": status, "finished_at": now(), "exit_code": code, "problems": problems, **facts})
        save_ledger(root, ledger)

    print(f"{label} attempt {attempt}: {status}")
    for problem in problems + facts.get("warnings", []):
        print(f"  ! {problem}")
    if status == "done":
        print(f"  report: {out}")
        if not temporary:
            print(f"  worktree: {path}")
            print(f"  changed: {len(facts.get('changed_paths', []))} path(s) {' '.join(facts.get('diffstat', []))}")
            print("  next: validate per docs/codex-cli-lane.md, then commit in that worktree")
    return 0 if status == "done" else 1


def cmd_review(args: argparse.Namespace) -> int:
    root = state_root()
    spec = lane_spec()
    repo = root
    command_name = "security review" if args.security else "review"
    requirement = recorded_requirement(root)
    head = branch_head(repo, args.branch)
    if not head:
        raise InputError(f"branch {args.branch} does not exist")
    if not branch_head(repo, args.base):
        raise InputError(f"base branch {args.base} does not exist locally")
    checked_out = worktrees(repo).get(args.branch)
    if checked_out is not None and dirty(checked_out):
        raise Refused(f"{args.branch} has uncommitted changes at {checked_out}; a review reads commits -- commit first")

    with Lock(root) as lock:
        ledger = load_ledger(root)
        attempt = refuse_review(ledger, requirement, command_name, head, spec["round_caps"]["review"])
        n = sum(1 for run in ledger["runs"] if run.get("kind") == "review" and run["requirement"] == requirement)
        out = output_dir(root, requirement) / f"{command_name.replace(' ', '-')}-{n + 1}.md"
        fresh(out)
        fresh(out.with_suffix(".log"))
        path = worktree_path(repo, f"review-{requirement}-{head[:12]}")
        if path.exists():
            raise Refused(f"{path} already exists; remove it before running")
        # Both reviews go through `codex exec --sandbox read-only` with a prompt.
        # The built-in `codex exec review` has no --sandbox flag, so its sandbox
        # would rest on config precedence against the operator's own profile.
        binary = codex_bin(required=not args.dry_run)
        command = exec_command(binary, "read-only", path, out, args.model)
        lines = SECURITY_REVIEW if args.security else GENERAL_REVIEW
        prompt = "\n\n".join(lines).format(head=head, base=args.base) + "\n"
        if args.dry_run:
            print("$ " + " ".join(command))
            print(prompt)
            return 0
        git(repo, "worktree", "add", "--detach", str(path), head)
        entry = {"kind": "review", "requirement": requirement, "command": command_name, "attempt": attempt,
                 "base": args.base, "branch": args.branch, "head_sha": head, "output": str(out),
                 "status": "running", "started_at": now(), "command_line": command}
        ledger["runs"].append(entry)
        save_ledger(root, ledger)
        try:
            code = run_codex(command, prompt, path, out.with_suffix(".log"), args.timeout, lock)
            problems = [] if code == 0 else [f"codex exited {code}; see {out.with_suffix('.log')}"]
            if code == 0:
                changed = dirty(path)
                if changed or git(path, "rev-parse", "HEAD") != head:
                    problems.append(f"the review changed its checkout: {changed[:5]}")
            status = "failed" if code != 0 else ("violated" if problems else "done")
        except BaseException as exc:
            entry.update({"status": "failed", "finished_at": now(), "problems": [f"interrupted: {exc!r}"]})
            save_ledger(root, ledger)
            raise
        finally:
            git(repo, "worktree", "remove", "--force", str(path), check=False)
        entry.update({"status": status, "finished_at": now(), "exit_code": code, "problems": problems})
        save_ledger(root, ledger)

    print(f"{command_name} of {args.branch} at {head[:12]} (attempt {attempt}): {status}")
    for problem in problems:
        print(f"  ! {problem}")
    if status == "done":
        print(f"  findings: {out}")
        print("  next: confirm each finding against the code before a FIX round")
    return 0 if status == "done" else 1


def cmd_status(args: argparse.Namespace) -> int:
    ledger = load_ledger(state_root())
    runs = ledger["runs"]
    if args.requirement:
        runs = [run for run in runs if run.get("requirement") == args.requirement
                or str(run.get("task_id", "")).startswith(args.requirement)]
    if not runs:
        print("no codex-cli runs recorded")
    for run in runs:
        if run["kind"] == "task":
            what = f"{run['task_id']} {run['mode']} r{run['round']} a{run['attempt']} @ {run['target_sha'][:12]}"
        else:
            what = f"{run['requirement']} {run['command']} a{run['attempt']} @ {run['head_sha'][:12]}"
        print(f"{run.get('started_at', '?')}  {run['status']:<8}  {what}  {run.get('output', '')}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True

    def line(good: bool, text: str, critical: bool = True) -> None:
        nonlocal ok
        ok = ok and (good or not critical)
        print(f"{'ok  ' if good else ('FAIL' if critical else 'warn')}  {text}")

    try:
        binary = codex_bin()
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
        line(version.returncode == 0, f"codex: {binary} ({(version.stdout or version.stderr).strip()})")
        login = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=30)
        line(login.returncode == 0, f"codex login: {(login.stdout or login.stderr).strip() or login.returncode}")
    except (InputError, OSError, subprocess.TimeoutExpired) as exc:
        line(False, str(exc))
    try:
        repo = repo_root(project_root())
        line(True, f"git repository: {repo}")
        ignored = subprocess.run([*GIT, "check-ignore", "-q", f"{WORKTREES}/x"], cwd=repo).returncode == 0
        line(ignored, f"{WORKTREES}/ is gitignored", critical=False)
    except InputError as exc:
        line(False, str(exc))
    try:
        caps = lane_spec()["round_caps"]
        line(True, f"policy lane {LANE}: round caps {caps}")
    except InputError as exc:
        line(False, str(exc))
    lane_file = project_root() / ".claude" / "routing-lane.local"
    saved = lane_file.read_text(encoding="utf-8").strip() if lane_file.exists() else ""
    line(saved == LANE, f"saved lane: {saved or 'none (policy default)'} -- `python3 scripts/lane.py set {LANE}` "
         "makes route.py use this lane", critical=False)
    return 0 if ok else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="is the Codex CLI installed, logged in, and the lane configured")

    run = sub.add_parser("run", help="run a BRAINSTORM, IMPLEMENT or FIX contract")
    run.add_argument("contract", type=Path)

    review = sub.add_parser("review", help="run Codex's review of a branch against its base")
    review.add_argument("--branch", required=True, help="the implementation branch to review (its committed head)")
    review.add_argument("--base", default="main", help="the branch it will merge into (default: main)")
    review.add_argument("--security", action="store_true", help="a security review instead of the general one")

    for command in (run, review):
        command.add_argument("--model", help="Codex model (default: your Codex config)")
        command.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds before the run is failed")
        command.add_argument("--dry-run", action="store_true", help="check everything, print the command, run nothing")

    status = sub.add_parser("status", help="the runs recorded in the ledger")
    status.add_argument("--requirement", help="only this requirement's runs (or task_ids starting with it)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handlers = {"doctor": cmd_doctor, "run": cmd_run, "review": cmd_review, "status": cmd_status}
    try:
        return handlers[args.command](args)
    except Refused as exc:
        print(f"codex_cli: refused: {exc}", file=sys.stderr)
        return 1
    except InputError as exc:
        print(f"codex_cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
